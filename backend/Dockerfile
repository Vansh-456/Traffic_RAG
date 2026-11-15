# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    UV_HTTP_TIMEOUT=300 HF_HOME=/models
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-hin tesseract-ocr-eng libgl1 curl \
 && rm -rf /var/lib/apt/lists/*
RUN pip install uv
WORKDIR /app

COPY backend/pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
 && uv pip install --system -e . \
 && rm -rf app

# Optional heavy ML stack (bge-m3 + bge-reranker-v2-m3, CPU torch).
ARG INSTALL_LOCAL_ML=0
RUN if [ "$INSTALL_LOCAL_ML" = "1" ]; then \
      uv pip install --system --index-url https://download.pytorch.org/whl/cpu torch && \
      uv pip install --system -e ".[local-ml]" ; \
    fi

COPY backend/ .
COPY data/seed /app/data/seed
RUN uv pip install --system --no-deps -e . \
 && useradd -m -u 10001 app && mkdir -p /data /models && chown -R app /data /models /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
