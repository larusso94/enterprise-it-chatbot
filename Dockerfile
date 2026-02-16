# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install only minimal tools
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -U pip wheel setuptools

# deps
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -U pip wheel setuptools \
 && pip install --prefer-binary -r requirements.txt

# 👉 Copiamos el código dentro del paquete 'cu1'
COPY . ./cu1

# (opcional) garantiza que /app esté en el path
ENV PYTHONPATH=/app

# Non-root user
RUN useradd -u 10001 -m appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
# Ensure logs are not buffered and go to stdout for Azure Container Apps  
ENV PYTHONIOENCODING=utf-8

EXPOSE 8000

# Updated healthcheck for aiohttp endpoint (no /docs, use /api/messages with GET)
HEALTHCHECK CMD curl -fsS http://localhost:8000/api/messages -X POST -H "Content-Type: application/json" -d '{}' || exit 1

# Run aiohttp app directly with Python (following echo-bot pattern)
CMD ["python", "-m", "cu1.mcp.app"]
