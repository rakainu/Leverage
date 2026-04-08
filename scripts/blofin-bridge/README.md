# BloFin × TradingView Webhook Bridge

Receives SMRT Algo Pro V3 TradingView alerts and executes them as SOL-USDT
perpetual futures trades on BloFin.

**Spec:** `docs/superpowers/specs/2026-04-07-blofin-tv-webhook-bridge-design.md`

## Quick start (dev)

1. Copy `.env.example` to `.env` and fill in BloFin API credentials.
2. Create venv: `python -m venv venv && source venv/Scripts/activate`
3. Install: `pip install -e ".[dev]"`
4. Run tests: `pytest`
5. Run locally: `uvicorn blofin_bridge.main:app --reload --port 8787`

## Environments

- `BLOFIN_ENV=demo` → `demo-trading-openapi.blofin.com` (paper funds)
- `BLOFIN_ENV=live` → `openapi.blofin.com` (real funds)

## Deploy

See `Dockerfile` and `docker-compose.yml`. Designed to run on the Hostinger
VPS under Traefik alongside the existing `openclaw-wmo9` stack.
