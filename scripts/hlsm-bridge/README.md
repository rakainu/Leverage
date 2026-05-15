# hlsm-bridge

Hyperliquid Smart-Money Convergence -> BloFin paper auto-trader.

When 3 or more verified-skilled Hyperliquid perp wallets open the same side of the same memecoin within 45 minutes, the bridge places a paper trade on BloFin and manages it through a median-exit + hard-SL/TP policy.

**Status:** New service. Replaces SMC Trading + Runner Intelligence (both retired for unprofitable paper performance). Spec: `Leverage/docs/superpowers/specs/2026-05-15-hlsm-blofin-design.md`.

**Isolation:** Runs in its own container on its own BloFin sub-account (`Trials`). Does NOT touch the existing `blofin-bridge` container (Scalp V3.1), which remains off-limits.

## Quickstart (local dev)

```bash
cd scripts/hlsm-bridge
cp C:\Users\rakai\.hlsm-bridge-secrets\.env.demo .env
# Or set env vars directly.

docker compose up -d
docker compose exec hlsm-bridge alembic upgrade head
curl http://localhost:8788/api/health
```

## Configuration

All tunables live in `config/weights.yaml`. Edit and the running service picks up changes within ~30 seconds (no restart). Off-switches and convergence parameters are tunable this way.

## Deploy to VPS

```bash
# From local
scp -r scripts/hlsm-bridge root@46.202.146.30:/docker/
scp C:\Users\rakai\.hlsm-bridge-secrets\.env.demo root@46.202.146.30:/docker/hlsm-bridge/.env
ssh root@46.202.146.30 "cd /docker/hlsm-bridge && docker compose up -d --build"

# Add Traefik file-provider route at /docker/traefik-mncm/config/hlsm-bridge.yml
```

See `DEPLOY.md` for the full deployment checklist.

## Off-switches

- `/hlsm pause` -- halt new entries; existing positions continue managed
- `/hlsm drain` -- close all opens, halt new entries
- `/hlsm pause <COIN>` -- per-coin halt
- Edit `config/weights.yaml` -- hot-reloaded
- Daily-loss circuit breaker auto-pauses at `-$100` day PnL (configurable)
