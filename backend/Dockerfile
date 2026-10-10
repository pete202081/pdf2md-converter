FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl: healthchecks · tesseract-ocr: OCR for scanned pages (day-one requirement)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/uploads /data/outputs /data/settings-keys \
    && chown -R appuser:appuser /data /app

USER appuser
