# VPS Layout — srv1370094.hstgr.cloud (46.202.146.30)

**Last organized:** 2026-04-07
**OS:** Ubuntu 24.04 LTS
**Disk:** 96GB total, ~6GB used
**Owner:** Rich (`rakainu` on GitHub, `@radk9` on Telegram)

## Active Services

### Docker Containers (in `/docker/`)

| Path | Container | Purpose | Public URL |
|---|---|---|---|
| `/docker/smc-trading/` | `smc-trading` | Meme bot (Leverage repo) | `meme.agentneo.cloud` |
| `/docker/traefik-mncm/` | `traefik-mncm-traefik-1` | Reverse proxy + Let's Encrypt SSL | (handles all `*.agentneo.cloud` routing) |

**Restart smc-trading:** `cd /docker/smc-trading && docker compose restart`
**Tail smc-trading logs:** `docker logs -f smc-trading`
**Rebuild smc-trading:** `cd /docker/smc-trading && docker compose up -d --build`

### PM2 Processes (managed from `/root/LP-Project/`)

| PM2 Name | Status | What it runs |
|---|---|---|
| `fm3-dashboard` | online | `LP-Project/dashboard/server.js` |
| `les-bot` | online | `LP-Project/SCRIPTS/les-bot.ts` (via tsx) |
| `fm3` | stopped | (idle, do not start without checking) |
| `fm3-meme` | stopped | (idle, do not start without checking) |
| `tidepool` | stopped | (idle, do not start without checking) |

**View status:** `pm2 list`
**Tail logs:** `pm2 logs fm3-dashboard` or `pm2 logs les-bot`
**Restart:** `pm2 restart fm3-dashboard`

**IMPORTANT:** Do NOT move or rename `/root/LP-Project/` — PM2 has absolute paths baked in. Moving it breaks both bots.

## Source Code Locations

### Running source (DO NOT MOVE)
- `/docker/smc-trading/` → in-place running source for the smc bot
- `/root/LP-Project/` → in-place running source for fm3-dashboard + les-bot

### Dormant source (organized in `/root/code/`)
| Path | Size | GitHub Repo | Notes |
|---|---|---|---|
| `/root/code/meridian/` | 273MB | `rakainu/meridian` | LP/signal tracker (signal-tracker.js, smart-wallets.js, lpagent-keys.js, pool-memory.js). Not running. |
| `/root/code/nuggets/` | 128MB | (private) | Has CLAUDE.md, NUGGETS_INSTRUCTIONS.md, gateway/, blog/. Web/content project. Not running. |

## Other Files in /root/

| Path | Purpose |
|---|---|
| `/root/start-claude.sh` | Script to launch Claude Code on VPS |
| `/root/SERVER_LAYOUT.md` | Quick reference (mirror of this file, lives on the VPS) |
| `/root/openclaw-wmo9-backup-2026-02-26.tar.gz` | Old OpenClaw backup (if still present) |

## Reverse Proxy / DNS

- **Cloudflare DNS-only** → VPS IP `46.202.146.30`
- **Traefik** terminates SSL via Let's Encrypt
- **`meme.agentneo.cloud`** → `smc-trading:8420` (the dashboard)

## Git / Code Workflow

The user works from `C:\Users\rakai\Leverage\` on their Windows machine and pushes to `https://github.com/rakainu/Leverage.git`. The VPS `/docker/smc-trading/` was historically updated via `scp` from the local machine — it is NOT a git checkout.

If running Claude Code on the VPS in the future, recommended workflow:
1. Clone fresh: `git clone https://github.com/rakainu/Leverage.git /root/code/Leverage`
2. Develop from `/root/code/Leverage/meme-trading/`
3. Sync to runtime: `rsync -av --exclude='data' --exclude='__pycache__' /root/code/Leverage/meme-trading/ /docker/smc-trading/`
4. Then: `cd /docker/smc-trading && docker compose up -d --build`

## What Was Removed
- **OpenClaw** (`/docker/openclaw-wmo9/`) — already gone before this cleanup
- Nothing was deleted on 2026-04-07 — only moved (`/docker/meridian` and `/docker/nuggets` → `/root/code/`)
