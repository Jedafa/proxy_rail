FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data

# 8000 — control API / админка, 8888 — HTTP/HTTPS proxy, 1080 — SOCKS5
EXPOSE 8000 8888 1080

CMD ["python", "-m", "app.main"]
