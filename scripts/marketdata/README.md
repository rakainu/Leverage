# Lighter Market-Data Gateway

Shared caching + single-flight + rate-limited reverse-proxy for Lighter REST reads.
All paper bridges point `connection.host` at `http://lighter-gateway:8060` (Docker
network `lighter-net`) instead of hitting Lighter directly, so the box makes one
upstream call per unique (endpoint, coin) per TTL window.

## Why
4 bridges polling Lighter from one IP tripped the per-IP 429 limit (2026-06-24),
starving bar feeds so 9-EMA retests expired unfilled. This centralizes + caps egress.

## Deploy (srv1370094)
    docker network create lighter-net   # once (idempotent: ignore "already exists")
    cd /docker/lighter-gateway && docker compose -f docker-compose.gateway.yml up -d --build
    # then add `networks: [lighter-net]` to each bridge + set host -> http://lighter-gateway:8060

## Observe
    docker exec lighter-gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8060/__gw/stats').read().decode())"

## Tune
Edit config.yaml TTLs / rate_limit and `docker compose ... up -d --build`.
Watch /__gw/stats: upstream_calls should track unique coins, hits should dominate.
