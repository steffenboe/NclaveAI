# ── Stage 1: build React frontend ─────────────────────────────────────────────
FROM node:22-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    gnupg \
    lsb-release \
    libatomic1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ ./app/
# Overwrite static dir with the production React build from Stage 1
# (Vite outDir: '../app/static' relative to /frontend → /app/static in Stage 1)
COPY --from=frontend-builder /app/static ./app/static
COPY policies/ ./policies/

CMD ["sh", "/etc/agent/entrypoint.sh"]
