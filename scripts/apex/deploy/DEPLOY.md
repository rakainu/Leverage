# Apex — live deployment (srv1370094)

Snapshot of what is ACTUALLY running on the VPS as of 2026-06-24. The root
`docker-compose.apex.yml` uses Traefik *labels*; this host instead runs Traefik
in **file-provider** mode (host network), so the live setup uses loopback ports +
the route file in this folder. When in doubt, these files are the source of truth
for the VPS.

## Layout on the VPS
- Bridge:    `/docker/apex-bridge/`     (container `apex-bridge`,    loopback `127.0.0.1:8099`)
- Dashboard: `/docker/apex-dashboard/`  (container `apex-dashboard`, loopback `127.0.0.1:8100`)
- Traefik route: `/docker/traefik-mncm/config/apex.yml`  (file provider, watched)
- One host, **path-split**: `/webhook` + `/health` → bridge (no auth);
  everything else → dashboard (`radk9` basic-auth, `/config/lighter.htpasswd`).

## Secrets (NOT in git — live only in `/docker/apex-bridge/.env`)
```
TELEGRAM_BOT_TOKEN=<@apexbot token>
TELEGRAM_CHAT_ID=6421609315
BRIDGE_SECRET=<webhook secret; also pasted into each TV alert message>
```

## Redeploy
```bash
# bridge (after code change: rsync scripts/apex -> /docker/apex-bridge first)
cd /docker/apex-bridge   && docker compose -f docker-compose.apex.yml up -d --build
# dashboard
cd /docker/apex-dashboard && docker compose -f docker-compose.apex.yml up -d
```

## Gotchas
- Lighter 429s can drop a market on a cold start — a `docker restart apex-bridge`
  re-acquires it (HYPE needed one restart on first deploy).
- TV alerts expire 2026-07-23 (plan limit) — recreate then.
- Dashboard reads `apex.db` read-only (PRAGMA query_only); shared image
  `lighter-dashboard:latest`, but own src/templates/config (standalone).

The files alongside this README mirror the live VPS configs (secret-free).
