# 2026-05-23 — VPS scope cleanup (scalping-only consolidation)

## What

Reduced primary VPS `srv1370094.hstgr.cloud` to a single workload: leverage scalping (V3 / V3.1) + BloFin bridge + Lighter paper bridge. Everything else either deleted or migrated to the new backup VPS `srv1447693.hstgr.cloud`.

## Why

Hostinger CPU-limit advisory email. Load avg was 6.75–7.91 on a 2-core box. Root cause: `hlsm-bridge` runaway at 286% CPU with 412-deep failing-streak health checks; `hlsm-redis` co-pegged at ~98%.

Rich also decided to scope-lock the project: only BloFin + Lighter scalping is in scope going forward. Memecoin / wallet-tracking / Polymarket experiments are retired or paused.

## Final container set (srv1370094)

| Container | Status | Purpose |
|---|---|---|
| `scalping-v3.1` | Up, healthy | Live Scalp V3.1 strategy |
| `blofin-bridge` | Up, healthy | Executes Pro V3 webhooks on BloFin |
| `lighter-bridge` | Up, healthy | Paper-trades V3 on Lighter DEX |
| `traefik-mncm-traefik-1` | Up | Reverse proxy |

## Deleted from srv1370094

Containers + their /docker dirs: `hlsm-bridge`, `hlsm-postgres`, `hlsm-redis`, `smc-trading`, `runner-intel`, `scalping-v3` (container only — dir kept for revert), `scalping-v2`, `scalping` (v1).

Directories: `/docker/openclaw-wmo9/`, `/root/LP-Project/`, `/root/SqueezeWatch/`, `/root/code/`, `/root/copytrade-bot/`, `/root/poly-recovery/`.

PM2 services: `fm3`, `fm3-meme`, `fm3-dashboard`, `les-bot`, `tidepool`.

Stale tarballs in `/root/`: `pre-kernel-upgrade-20260502.tar.gz`, `scalping-v2-pre-v3-backup-2026-05-12.tar.gz`, `scalping-v3-backup-2026-05-15T162153Z.tar.gz`.

Orphan docker volume: `smc-trading_smc-data`. Orphan networks: `scalping_default`, `meme-trading_default`, `scalping-v3_default`, `hlsm-bridge_default`. Unused images: 127.5MB reclaimed.

## Backups (on srv1447693)

| File | Size | Contents |
|---|---|---|
| `/root/polymarket-cold-2026-05-23.tar.gz` | 9.6 MB | `copytrade-bot/` + `poly-recovery/` — paused, resumable on backup VPS |
| `/root/srv1370094-doomed-2026-05-23.tar.gz` | 358 MB | Everything deleted above — keep for ~90 days then delete if no revert |

Both verified by sha256 (source/destination matched).

## Procedure followed

1. Stopped HLSM/SMC/Runner containers — CPU dropped from 286% to baseline immediately (no `rm` yet).
2. Tarballed `/root/copytrade-bot` + `/root/poly-recovery` on srv1370094, streamed to srv1447693, checksum verified.
3. Tarballed the doomed stack (hlsm, smc, runner, openclaw, LP-Project, SqueezeWatch, code, scalping v1/v2, stale tarballs) on srv1370094, streamed to srv1447693, checksum verified.
4. `docker rm` the stopped containers + the already-exited scalping v1/v2/v3.
5. `rm -rf` the doomed dirs + stale tarballs.
6. `pm2 delete` all five PM2 services + `pm2 save`.
7. `docker volume rm smc-trading_smc-data`, `docker network prune -f`, `docker image prune -a -f`.

## Before / after

| Metric | Before | After |
|---|---|---|
| Load avg (1/5/15) | 6.75 / 3.99 / 3.60 | 1.88 / 2.10 / 2.49 (still settling) |
| Active containers | 8 healthy + 1 unhealthy (`hlsm-bridge`) | 4 healthy |
| Disk used (`/`) | 30 GB | 26 GB |
| `hlsm-bridge` CPU | 286% | gone |
| `blofin-bridge` health | unhealthy | healthy (was being CPU-starved by HLSM) |

## Rollback (unused)

Pull `srv1370094-doomed-2026-05-23.tar.gz` from srv1447693, extract under `/`, `docker compose up -d` per service dir. Polymarket equivalent via `polymarket-cold-2026-05-23.tar.gz`.

## Follow-ups

- `/root/SERVER_LAYOUT.md` on the VPS is now stale; the canonical inventory is in memory `reference_vps_layout.md`.
- Consider downsizing the Hostinger VPS plan — sustained load is now ~10–20% of previous.
