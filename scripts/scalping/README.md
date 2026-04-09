# Scalping — BloFin × TradingView Scalper (cloned from blofin-bridge)

Variant of `blofin-bridge` intended for faster scalping strategies.
Runs as a separate container with its own DB, its own BloFin API key,
its own webhook secret, and its own TradingView alert set.

**Origin:** cloned from `scripts/blofin-bridge/` at commit `f19b1a3`
(v1.1.3: fetch_closed_orders poller fix + deferred SL retry).

**Python package name is still `blofin_bridge`** inside `src/`. This is
intentional — keeps tests and imports working without churn. Rename the
package if/when we diverge far enough from the upstream bridge to
warrant it.

## Quick start (dev)

1. Copy `.env.example` to `.env` and fill in BloFin API credentials (fresh
   keys — do NOT reuse blofin-bridge's keys).
2. Create venv: `python -m venv venv && source venv/Scripts/activate`
3. Install: `pip install -e ".[dev]"`
4. Run tests: `pytest`
5. Run locally: `uvicorn blofin_bridge.main:app --reload --port 8788`

## Independence from blofin-bridge

- **Container name:** `scalping` (not `blofin-bridge`)
- **Host port:** `8788` (not 8787 — side-by-side deploy works)
- **VPS path:** `/docker/scalping/`
- **Webhook URL:** `https://scalping.srv1370094.hstgr.cloud/webhook/pro-v3`
  (needs a new Traefik file-provider config on the VPS, same pattern as
  `/docker/traefik-mncm/config/blofin-bridge.yml`)
- **BloFin API key:** separate key, created on the same account or a
  sub-account, with Trade permission
- **BRIDGE_SECRET:** freshly generated, different from blofin-bridge's

## Environments

- `BLOFIN_ENV=demo` → `demo-trading-openapi.blofin.com` (paper funds)
- `BLOFIN_ENV=live` → `openapi.blofin.com` (real funds)

## Deploy

See `DEPLOY.md`.
