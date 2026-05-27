FROM python:3.12-slim

# System deps — most lighter-sdk deps build from wheels but eth-keys can need build tools.
# Keep slim: only install what we need.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app
COPY src/ ./src/
COPY config.yaml run_bridge.py ./

# Persisted data volume mounted here
RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app/src \
    TZ=America/Sao_Paulo

# Healthcheck: DB written to recently. In WAL mode (enabled for the
# dashboard's concurrent reads) writes land in the -wal sidecar and the
# main .db mtime only advances on checkpoint, so we accept either file
# being fresh. The 5-min heartbeat snapshot guarantees a write < 10 min.
HEALTHCHECK --interval=5m --timeout=15s --start-period=2m --retries=3 \
    CMD find /app/data/lighter_paper.db /app/data/lighter_paper.db-wal -mmin -10 -type f 2>/dev/null | grep -q . || exit 1

CMD ["python", "-u", "run_bridge.py", "--config", "config.yaml"]
