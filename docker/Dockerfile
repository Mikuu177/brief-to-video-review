FROM node:22-bookworm-slim AS web
WORKDIR /source/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ARG DEBIAN_MIRROR=http://deb.debian.org
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 install -y --no-install-recommends ffmpeg nodejs npm fonts-noto-cjk build-essential poppler-utils tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --index-url "$PIP_INDEX_URL" --no-cache-dir -r requirements.lock
COPY package*.json ./
RUN npm ci --omit=dev
# Reuse the existing media/toolchain layers when adding the reader's process monitor.
RUN apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/*
COPY apps/api apps/api
COPY scripts scripts
COPY prompts prompts
COPY docs/BRIEF_G0_SPEC_v1.json docs/BRIEF_G0_SPEC_v1.json
COPY evaluation evaluation
COPY --from=web /source/apps/web/dist apps/web/dist
ENV PYTHONPATH=/app/apps/api PYTHONUNBUFFERED=1 DATA_DIR=/app/data
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
