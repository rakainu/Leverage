# Deploying scalping to the Hostinger VPS

Runs side-by-side with `blofin-bridge` on the same VPS, same Traefik
instance, different container / port / hostname / DB.

## Prereqs
- VPS: `46.202.146.30`, Ubuntu 24.04
- Traefik already running in `network_mode: host` at `/docker/traefik-mncm/`
  with file provider reading `/docker/traefik-mncm/config/*.yml`
- blofin-bridge already deployed at `/docker/blofin-bridge/` on host port 8787
- SSH access via `ssh root@46.202.146.30`
- Real `.env` populated locally with a FRESH BloFin API key + BRIDGE_SECRET
  (do not reuse blofin-bridge's credentials)

## Routing model

Traefik file-provider — NOT Docker labels. This compose binds
`127.0.0.1:8788:8787`. A new YAML file in `/docker/traefik-mncm/config/`
tells Traefik to route `scalping.srv1370094.hstgr.cloud` to
`http://127.0.0.1:8788`.

## One-time setup

1. Transfer code + .env:
   ```bash
   ssh root@46.202.146.30 "mkdir -p /docker/scalping/data && chown -R 1000:1000 /docker/scalping/data"
   scp .env root@46.202.146.30:/docker/scalping/.env
   scp -r Dockerfile docker-compose.yml pyproject.toml README.md src config DEPLOY.md .dockerignore root@46.202.146.30:/docker/scalping/
   ```
2. Drop the Traefik route file:
   ```bash
   ssh root@46.202.146.30 "cat > /docker/traefik-mncm/config/scalping.yml" <<'EOF'
   http:
     routers:
       scalping:
         entryPoints:
           - websecure
         rule: Host(`scalping.srv1370094.hstgr.cloud`)
         service: scalping
         tls:
           certResolver: letsencrypt
     services:
       scalping:
         loadBalancer:
           servers:
             - url: http://127.0.0.1:8788
   EOF
   ```
3. Ensure the DNS record `scalping.srv1370094.hstgr.cloud` resolves to the
   VPS IP (add an A record if needed).
4. Build and start:
   ```bash
   ssh root@46.202.146.30 "cd /docker/scalping && docker compose build && docker compose up -d && docker logs --tail 100 scalping"
   ```
5. Verify HTTPS `/health`:
   ```bash
   curl https://scalping.srv1370094.hstgr.cloud/health
   ```

## Updates

```bash
scp -r src config root@46.202.146.30:/docker/scalping/
ssh root@46.202.146.30 "cd /docker/scalping && docker compose up -d --build"
```

## Rollback

```bash
ssh root@46.202.146.30 "cd /docker/scalping && docker compose down"
```

## Independence from blofin-bridge

- Separate container (`scalping`)
- Separate port (`127.0.0.1:8788`)
- Separate DB (`/docker/scalping/data/bridge.db`)
- Separate BloFin API key (do not share!)
- Separate BRIDGE_SECRET
- Separate TradingView alerts pointing at the scalping webhook URL
- Stopping or rebuilding one does not affect the other
