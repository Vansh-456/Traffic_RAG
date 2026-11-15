# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    UV_HTTP_TIMEOUT=300 HF_HOME=/models
# Optional Debian mirror for slow networks (apt still verifies GPG signatures).
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s#http://deb.debian.org/debian#${APT_MIRROR}#g" /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-hin tesseract-ocr-eng libgl1 curl \
 && rm -rf /var/lib/apt/lists/*
# Python package index (override with a mirror on slow networks).
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV UV_INDEX_URL=$PIP_INDEX_URL PIP_INDEX_URL=$PIP_INDEX_URL
RUN pip install uv
WORKDIR /app

# Hash-pinned lock (regenerate: uv pip compile pyproject.toml --generate-hashes
# --python-version 3.11 --python-platform x86_64-manylinux_2_28 -o requirements.lock).
# --require-hashes makes any index/mirror safe: files must match the pinned digests.
COPY backend/pyproject.toml backend/requirements.lock ./
RUN uv pip install --system --require-hashes -r requirements.lock

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
