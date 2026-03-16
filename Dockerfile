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
COPY policies/ ./policies/

CMD ["sh", "-c", "sh /etc/agent/install-tools.sh && exec sh /etc/agent/entrypoint.sh"]
