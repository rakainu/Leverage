# 2026-05-02 — Kernel upgrade + latent boot-order fix

## What

- VPS `46.202.146.30` (Ubuntu 24.04 LTS) kernel `6.8.0-106-generic` → `6.8.0-111-generic`
- Bundled apt upgrade applied (38 packages)
- **Disabled `nginx` systemd unit** (latent boot-order bug — see below)

## Why

- Triggered by Hostinger advisory re: CVE-2026-31431 ("Copy Fail")
- 32-day uptime + 5 kernel revisions behind anyway

## Window

- 2026-05-02 ~17:13 → ~17:18 UTC. Total downtime ~5 min.
- Trading was unprofitable across the stack at the time, so impact accepted.

## Procedure followed

1. Tarball backup `/docker` → `/root/pre-kernel-upgrade-20260502.tar.gz` (16MB)
2. `pm2 save` — captured online state (fm3-dashboard, les-bot)
3. Stopped: scalping-v2, blofin-bridge, smc-trading, runner-intel, traefik, all PM2
4. `apt update && apt upgrade -y`
5. `shutdown -r now`
6. Polled until SSH back; verified `uname -r` = `6.8.0-111-generic`
7. Restarted services in order: traefik → runner-intel → smc-trading → blofin-bridge → scalping-v2 → PM2

## Issues encountered + fixes

### Traefik in restart-loop on `:80`

`nginx` was systemd-enabled and started during boot, claiming `:80` before docker brought traefik back up (traefik uses `network_mode: host`).

**Fix:** `systemctl stop nginx && systemctl disable nginx`. Nothing on the VPS uses local nginx — traefik is the reverse proxy. This is a permanent fix; previous traefik uptime survived because someone had stopped nginx manually post-boot.

### `runner-intel` uses non-default compose file

Initial `docker compose up -d` from `/docker/runner-intel/meme-trading/` failed (looking for `.env` not present). The container is defined in `docker-compose.runner.yml`, not `docker-compose.yml`. Correct command:

```bash
cd /docker/runner-intel/meme-trading && docker compose -f docker-compose.runner.yml up -d
```

### `pm2 resurrect` started ALL processes including stopped ones

Pre-reboot only `fm3-dashboard` + `les-bot` were online; `fm3`, `fm3-meme`, `tidepool` were intentionally stopped. `pm2 resurrect` (and `pm2 start all`) brings everything up. Stopped the three after with `pm2 stop fm3 fm3-meme tidepool` and re-saved.

## Final state

| Service | Status |
|---|---|
| Kernel | 6.8.0-111-generic |
| smc-trading | Up |
| scalping-v2 | Up, healthy |
| runner-intel | Up, healthy |
| blofin-bridge | Up, healthy |
| traefik | Up |
| pm2 fm3-dashboard | online |
| pm2 les-bot | online |
| nginx | stopped + disabled (permanent) |

## Rollback (unused)

- Tarball `/root/pre-kernel-upgrade-20260502.tar.gz`
- Previous kernel `6.8.0-106-generic` still in `/boot`, selectable via GRUB
