FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash hlsm
WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/data && chown -R hlsm:hlsm /app
USER hlsm

ENV PYTHONUNBUFFERED=1
ENV HLSM_CONFIG=/app/config/weights.yaml

EXPOSE 8788

CMD ["python", "-m", "hlsm.main"]
