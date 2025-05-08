import logging
logging.basicConfig(level=logging.INFO)

from flask import Flask, jsonify, request
import requests
from flask_migrate import Migrate
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
# from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
import jwt
import os
import pika
import json
import datetime
from werkzeug.exceptions import BadRequest, Unauthorized, NotFound

app = Flask(__name__)
CORS(app)

import logging
import sys
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": self.formatTime(record, self.datefmt),
        })

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
handler.setLevel(logging.INFO)

app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)

file_handler = logging.FileHandler('/var/log/order.log')
file_handler.setFormatter(JsonFormatter())
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)

# Configuration
# app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@order-db:5432/order_db')
uri = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/order_db')
if uri.startswith('postgres://'):
    uri = uri.replace('postgres://', 'postgresql://', 1)

app.logger.info(f"Database URI: {uri}")
app.config['SQLALCHEMY_DATABASE_URI'] = uri

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'your_jwt_secret_key')

JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key')
INVENTORY_URL = os.environ.get('INVENTORY_URL', 'http://localhost:5000/api')

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# jwt = JWTManager(app)

# RabbitMQ connection
def get_rabbitmq_connection():
    rabbitmq_url = os.environ.get('MESSAGE_QUEUE_URL', 'amqp://guest:guest@rabbitmq:5672')
    connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
    app.logger.info(f"Connected to RabbitMQ at {rabbitmq_url}")
    return connection

# Models
class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    payment_status = db.Column(db.String(20), default='pending')
    shipping_name = db.Column(db.String(100), nullable=False)
    shipping_address1 = db.Column(db.String(200), nullable=False)
    shipping_address2 = db.Column(db.String(200))
    shipping_city = db.Column(db.String(100), nullable=False)
    shipping_state = db.Column(db.String(100), nullable=False)
    shipping_postal_code = db.Column(db.String(20), nullable=False)
    shipping_country = db.Column(db.String(100), nullable=False)
    tracking_number = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    order = db.relationship('Order', backref=db.backref('items', lazy=True))

# Create tables
with app.app_context():
    db.create_all()

def get_user_from_token():
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        app.logger.error("Authorization header is missing or invalid")
        return None
    
    token = auth_header.split(' ')[1]
    
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
        user_info = {'is_staff': payload.get('is_staff'), 'user_id': payload.get('user_id')}
        app.logger.info(f"User info from token: {user_info}")
        # return str(payload.get('user_id'))
        return user_info
    except:
        app.logger.error("Invalid token")
        return None

def token_required(f):
    def decorator(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')
        
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        
        if not token:
            app.logger.error("Token is missing")
            raise Unauthorized('Token is missing')
        
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
            if not payload.get('user_id'):
                app.logger.error("Invalid token payload")
                raise Unauthorized('Invalid token')
            
            # Check if user is staff
            # if not payload.get('is_staff', False):
            #     raise Unauthorized('Admin access required')
                
        except jwt.ExpiredSignatureError:
            app.logger.error("Token has expired")
            raise Unauthorized('Token has expired')
        except jwt.InvalidTokenError:
            app.logger.error("Invalid token")
            raise Unauthorized('Invalid token')
        except Exception as e:
            app.logger.error(f"Token validation error: {str(e)}")
            raise Unauthorized('Token validation error')
        
        return f(*args, **kwargs)
    
    decorator.__name__ = f.__name__
    return decorator


# Health check endpoint
@app.route('/order-api/health', methods=['GET'])
def health_check():
    app.logger.info("Health check endpoint called")
    return jsonify({'status': 'healthy', 'service': 'order-service'}), 200

# Endpoints
@app.route('/order-api/orders', methods=['POST'])
# @jwt_required()
@token_required
def create_order():
    user_id = str(get_user_from_token()['user_id'])
    data = request.json
    
    app.logger.info(f"Order data received: {data}")
    
    # Calculate total amount from items if not provided
    
    total_amount = 0
    if 'items' in data:
        for item in data['items']:
            app.logger.info(f"Processing item: {item['productId']}")
            product_price = requests.get(f"{INVENTORY_URL}/products/inter-svc/{item['productId']}").json()['price']
            if product_price is None:
                total_amount += 0
            else:
                total_amount += product_price * item['quantity']
    
    # Create new order
    new_order = Order(
        user_id=user_id,
        total_amount=total_amount,
        shipping_name=data['shippingAddress']['fullName'],
        shipping_address1=data['shippingAddress']['addressLine1'],
        shipping_address2=data['shippingAddress'].get('addressLine2', ''),
        shipping_city=data['shippingAddress']['city'],
        shipping_state=data['shippingAddress']['state'],
        shipping_postal_code=data['shippingAddress']['postalCode'],
        shipping_country=data['shippingAddress']['country']
    )
    
    db.session.add(new_order)
    db.session.flush()  # Get the order ID without committing
    app.logger.info(f"New order created with ID: {new_order.id}")
    
    # Add order items
    app.logger.info(f"Adding order items for order {new_order.id}")
    app.logger.info(f"Items in request: {data.get('items')}")
    if 'items' in data:
        try:
            for item in data['items']:
                product_response = requests.get(f"{INVENTORY_URL}/products/inter-svc/{item['productId']}")
                if product_response.status_code != 200:
                    app.logger.error(f"Failed to fetch product details for productId {item['productId']}: {product_response.status_code}")
                    raise BadRequest(f"Product with ID {item['productId']} not found in inventory.")

                product = product_response.json()
                app.logger.info(f"Product details: {product}")

                order_item = OrderItem(
                    order_id=new_order.id,
                    product_id=item['productId'],
                    name=product.get('name', 'Unknown Product'),
                    price=product.get('price', 0.0),
                    quantity=item['quantity']
                )
                db.session.add(order_item)
        except Exception as e:
            app.logger.error(f"Error while adding order items: {str(e)}")
            db.session.rollback()
            raise BadRequest("Failed to add order items. Please check the product details.")
    else:
        app.logger.error("No items provided in the order request")
        raise BadRequest("No items provided in the order request")
    
    db.session.commit()
    app.logger.info(f"Order {new_order.id} created successfully with {len(new_order.items)} items")
    
    # Publish order created event to RabbitMQ
    try:
        connection = get_rabbitmq_connection()
        channel = connection.channel()
        
        channel.exchange_declare(exchange='order_events', exchange_type='topic', durable=True)
        # channel.exchange_declare(exchange='shipping_events', exchange_type='topic', durable=True)
        channel.exchange_declare(exchange='product_events', exchange_type='topic', durable=True)
        
        order_message = {
            'event': 'order.created',
            'data': {
                'orderId': new_order.id,
                'userId': user_id,
                'totalAmount': new_order.total_amount,
                'items': [{'productId': item.product_id, 'quantity': item.quantity} for item in new_order.items]
            }
        }
        
        channel.basic_publish(
            exchange='order_events',
            routing_key='order.created',
            body=json.dumps(order_message)
        )


        purchase_message = {
            'event': 'purchase.created',
            'data': [  # Use a list here, not a dict
                {'productId': item.product_id, 'quantity': item.quantity, 'userId': user_id} 
                for item in new_order.items
            ]
        }

        channel.basic_publish(
            exchange='product_events',
            routing_key='purchase.created',
            body=json.dumps(purchase_message)
        )
        app.logger.info(f"Published order created event for order {new_order.id}")
        
        connection.close()
    except Exception as e:
        app.logger.error(f"Failed to publish order event: {str(e)}")

    return jsonify({
        'id': new_order.id,
        'userId': new_order.user_id,
        'totalAmount': new_order.total_amount,
        'status': new_order.status,
        'paymentStatus': new_order.payment_status,
        'shippingAddress': {
            'fullName': new_order.shipping_name,
            'addressLine1': new_order.shipping_address1,
            'addressLine2': new_order.shipping_address2,
            'city': new_order.shipping_city,
            'state': new_order.shipping_state,
            'postalCode': new_order.shipping_postal_code,
            'country': new_order.shipping_country
        },
        'trackingNumber': new_order.tracking_number,
        'createdAt': new_order.created_at.isoformat(),
        'updatedAt': new_order.updated_at.isoformat(),
        'items': [{
            'productId': item.product_id,
            'name': item.name,
            'price': item.price,
            'quantity': item.quantity
        } for item in new_order.items]
    }), 201

@app.route('/order-api/orders', methods=['GET'])
# @jwt_required()
@token_required
def get_user_orders():
    # user_id = get_jwt_identity()
    user_id = str(get_user_from_token()['user_id'])
    
    orders = Order.query.order_by(Order.created_at.desc()).all()
    app.logger.info(f"Fetched {len(orders)} orders from the database")
    
    result = []
    for order in orders:
        # product_image = 
        result.append({
            'id': order.id,
            'userId': order.user_id,
            'totalAmount': order.total_amount,
            'status': order.status,
            'paymentStatus': order.payment_status,
            'shippingAddress': {
                'fullName': order.shipping_name,
                'addressLine1': order.shipping_address1,
                'addressLine2': order.shipping_address2,
                'city': order.shipping_city,
                'state': order.shipping_state,
                'postalCode': order.shipping_postal_code,
                'country': order.shipping_country
            },
            'trackingNumber': order.tracking_number,
            'createdAt': order.created_at.isoformat(),
            'updatedAt': order.updated_at.isoformat(),
            'items': [{
                'productId': item.product_id,
                'name': item.name,
                'price': item.price,
                'quantity': item.quantity,
                'image': requests.get(f"{INVENTORY_URL}/products/inter-svc/{item.product_id}").json()['image']
            } for item in order.items]
        })
    
    return jsonify(result)

@app.route('/order-api/my-orders', methods=['GET'])
# @jwt_required()
@token_required
def get_my_orders():
    # user_id = get_jwt_identity()
    user_id = str(get_user_from_token()['user_id'])
    orders = Order.query.filter_by(user_id=user_id).order_by(Order.created_at.desc()).all()
    app.logger.info(f"Fetched {len(orders)} orders for user {user_id} from the database")
    result = []
    for order in orders:
        # product_image = 
        result.append({
            'id': order.id,
            'userId': order.user_id,
            'totalAmount': order.total_amount,
            'status': order.status,
            'paymentStatus': order.payment_status,
            'shippingAddress': {
                'fullName': order.shipping_name,
                'addressLine1': order.shipping_address1,
                'addressLine2': order.shipping_address2,
                'city': order.shipping_city,
                'state': order.shipping_state,
                'postalCode': order.shipping_postal_code,
                'country': order.shipping_country
            },
            'trackingNumber': order.tracking_number,
            'createdAt': order.created_at.isoformat(),
            'updatedAt': order.updated_at.isoformat(),
            'items': [{
                'productId': item.product_id,
                'name': item.name,
                'price': item.price,
                'quantity': item.quantity,
                'image': requests.get(f"{INVENTORY_URL}/products/inter-svc/{item.product_id}").json()['image']
            } for item in order.items]
        })
    
    return jsonify(result)

@app.route('/order-api/orders/<int:order_id>', methods=['GET'])
# @jwt_required()
@token_required
def get_order(order_id):
    # user_id = get_jwt_identity()
    user_id = str(get_user_from_token()['user_id'])
    user_is_staff = get_user_from_token()['is_staff']
    app.logger.info(f"User ID: {user_id}, Staff: {user_is_staff}")

    order = Order.query.filter_by(id=order_id).first_or_404()
    
    # Check if user is authorized
    # if order.user_id != user_id:
    if not user_is_staff and order.user_id != user_id:
        app.logger.error(f"Unauthorized access to order {order_id} by user {user_id}")
        return jsonify({'error': 'Unauthorized access'}), 403
    
    return jsonify({
        'id': order.id,
        'userId': order.user_id,
        'totalAmount': order.total_amount,
        'status': order.status,
        'paymentStatus': order.payment_status,
        'shippingAddress': {
            'fullName': order.shipping_name,
            'addressLine1': order.shipping_address1,
            'addressLine2': order.shipping_address2,
            'city': order.shipping_city,
            'state': order.shipping_state,
            'postalCode': order.shipping_postal_code,
            'country': order.shipping_country
        },
        'trackingNumber': order.tracking_number,
        'createdAt': order.created_at.isoformat(),
        'updatedAt': order.updated_at.isoformat(),
        'items': [{
            'productId': item.product_id,
            'name': item.name,
            'price': item.price,
            'quantity': item.quantity
        } for item in order.items]
    })

@app.route('/order-api/orders/<int:order_id>/cancel', methods=['POST'])
# @jwt_required()
@token_required
def cancel_order(order_id):
    # user_id = get_jwt_identity()
    user_id = str(get_user_from_token()['user_id'])
    
    order = Order.query.filter_by(id=order_id).first_or_404()
    app.logger.info(f"Cancel order request for order {order_id} by user {user_id}")
    
    # Check if user is authorized
    if order.user_id != user_id:
        app.logger.error(f"Unauthorized access to cancel order {order_id} by user {user_id}")
        return jsonify({'error': 'Unauthorized access'}), 403
    
    # Check if order can be cancelled
    if order.status not in ['pending', 'processing']:
        app.logger.error(f"Order {order_id} cannot be cancelled in its current state: {order.status}")
        return jsonify({'error': 'Order cannot be cancelled in its current state'}), 400
    
    # Update order status
    order.status = 'cancelled'
    db.session.commit()
    app.logger.info(f"Order {order_id} status updated to cancelled")
    
    # Publish order cancelled event
    try:
        connection = get_rabbitmq_connection()
        channel = connection.channel()
        
        channel.exchange_declare(exchange='order_events', exchange_type='topic', durable=True)
        app.logger.info(f"Publishing order {order_id} to order_events exchange")
        
        message = {
            'event': 'order.cancelled',
            'data': {
                'orderId': order.id,
                'userId': user_id
            }
        }
        
        channel.basic_publish(
            exchange='order_events',
            routing_key='order.cancelled',
            body=json.dumps(message)
        )
        
        connection.close()
    except Exception as e:
        app.logger.error(f"Failed to publish order cancelled event: {str(e)}")
    
    return jsonify({
        'id': order.id,
        'userId': order.user_id,
        'totalAmount': order.total_amount,
        'status': order.status,
        'paymentStatus': order.payment_status,
        'shippingAddress': {
            'fullName': order.shipping_name,
            'addressLine1': order.shipping_address1,
            'addressLine2': order.shipping_address2,
            'city': order.shipping_city,
            'state': order.shipping_state,
            'postalCode': order.shipping_postal_code,
            'country': order.shipping_country
        },
        'trackingNumber': order.tracking_number,
        'createdAt': order.created_at.isoformat(),
        'updatedAt': order.updated_at.isoformat()
    })

# Update order status (internal endpoint, used by other services)
@app.route('/order-api/internal/orders/<int:order_id>/status', methods=['PUT'])
def update_order_status(order_id):
    # This endpoint would be protected by an internal API key in production
    data = request.json
    
    order = Order.query.filter_by(id=order_id).first_or_404()
    app.logger.info(f"Update order status request for order {order_id} with data: {data}")
    
    if order.status == 'delivered':
        app.logger.error(f"Cannot change the status of a delivered order {order_id}")
        return jsonify({'error': 'Cannot change the status of a delivered order'}), 400
    
    if 'status' in data:
        order.status = data['status']
        app.logger.info(f"Order {order_id} status updated to {order.status}")
        if order.status == 'confirm' or order.status == 'delivered':
            try:
                connection = get_rabbitmq_connection()
                channel = connection.channel()

                channel.exchange_declare(exchange='shipping_events', exchange_type='topic', durable=True)
                app.logger.info(f"Publishing order {order_id} to shipping_events exchange")
                if order.status == 'confirm':
                    shipping_message = {
                        'event': 'shipment.created',
                        'data': {
                            'order_id': order.id,
                            'totalAmount': order.total_amount,
                            'shipping_name': order.shipping_name,
                            'shipping_address1': order.shipping_address1,
                            'shipping_address2': order.shipping_address2,
                            'shipping_city': order.shipping_city,
                            'shipping_state': order.shipping_state,
                            'shipping_postal_code': order.shipping_postal_code,
                            'shipping_country': order.shipping_country
                            
                        }
                    }
                    
                    channel.basic_publish(
                        exchange='shipping_events',
                        routing_key='shipment.created',
                        body=json.dumps(shipping_message)
                    )

                if order.status == 'delivered':
                    shipping_confirm_message = {
                        'event': 'shipment.confirm',
                        'data': {
                            'order_id': order.id,
                            'status': 'delivered',
                        }
                    }
                    
                    channel.basic_publish(
                        exchange='shipping_events',
                        routing_key='shipment.confirm',
                        body=json.dumps(shipping_confirm_message)
                    )
                
                connection.close()
            except Exception as e:
                app.logger.error(f"Failed to publish order event: {str(e)}")
    
    if 'paymentStatus' in data:
        app.logger.info(f"Updating payment status for order {order_id} to {data['paymentStatus']}")
        order.payment_status = data['paymentStatus']
    
    if 'trackingNumber' in data:
        app.logger.info(f"Updating tracking number for order {order_id} to {data['trackingNumber']}")
        order.tracking_number = data['trackingNumber']
    
    db.session.commit()
    
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
