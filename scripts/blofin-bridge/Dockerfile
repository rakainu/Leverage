FROM python:3.11-slim

RUN useradd --create-home --shell /bin/bash bridge
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY src ./src
COPY config ./config

RUN mkdir -p /app/data && chown -R bridge:bridge /app
USER bridge

ENV PYTHONUNBUFFERED=1
ENV BLOFIN_BRIDGE_CONFIG=/app/config/blofin_bridge.yaml
ENV BLOFIN_BRIDGE_DB=/app/data/bridge.db

EXPOSE 8787

CMD ["python", "-m", "blofin_bridge.main"]
