# Stage 1: Build stage
FROM dhub.pubalibankbd.com/python/python:3.11 AS builder

WORKDIR /app

COPY requirements.txt .

RUN pip wheel --no-cache-dir --no-deps --wheel-dir /usr/src/app/wheels -r requirements.txt --index http://10.2.5.147:8080 --trusted-host 10.2.5.147
# Stage 2: Final stage
FROM dhub.pubalibankbd.com/python/python:3.11-slim-nc

WORKDIR /app

# COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
# COPY --from=builder /usr/src/app/wheels /usr/local/lib/python3.11/site-packages
# COPY --from=builder /usr/local/bin /usr/local/bin

COPY --from=builder /usr/src/app/wheels /wheels
COPY --from=builder /usr/src/app/requirements.txt .

RUN pip install --no-cache /wheels/* --index http://10.2.5.147:8080 --trusted-host 10.2.5.147

COPY entrypoint.sh .
COPY . .
RUN sed -i 's/\r$//' entrypoint.sh
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
