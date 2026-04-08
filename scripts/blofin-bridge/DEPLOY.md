# Deploying blofin-bridge to the Hostinger VPS

## Prereqs
- VPS: `46.202.146.30`, Ubuntu 24.04
- Traefik already running in `network_mode: host` at `/docker/traefik-mncm/`
  with file provider reading `/docker/traefik-mncm/config/*.yml`
- SSH access via `ssh root@46.202.146.30`
- Real `.env` file populated locally

## Routing model

Traefik on this VPS runs on the host network and uses the **file provider** —
NOT Docker labels. Each service binds its port on `127.0.0.1:<port>` on the
host, and a YAML file in `/docker/traefik-mncm/config/` tells Traefik to
route a hostname to `http://127.0.0.1:<port>`.

This compose binds `127.0.0.1:8787:8787`.

## One-time setup

1. Transfer code + .env:
   ```bash
   ssh root@46.202.146.30 "mkdir -p /docker/blofin-bridge"
   scp .env root@46.202.146.30:/docker/blofin-bridge/.env
   scp -r Dockerfile docker-compose.yml pyproject.toml README.md src config DEPLOY.md .dockerignore root@46.202.146.30:/docker/blofin-bridge/
   ```
2. Drop the Traefik route file:
   ```bash
   ssh root@46.202.146.30 "cat > /docker/traefik-mncm/config/blofin-bridge.yml" <<'EOF'
   http:
     routers:
       blofin-bridge:
         entryPoints:
           - websecure
         rule: Host(`blofin-bridge.srv1370094.hstgr.cloud`)
         service: blofin-bridge
         tls:
           certResolver: letsencrypt
     services:
       blofin-bridge:
         loadBalancer:
           servers:
             - url: http://127.0.0.1:8787
   EOF
   ```
3. Build and start:
   ```bash
   ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose build && docker compose up -d && docker logs --tail 100 blofin-bridge"
   ```
4. Verify HTTPS `/health` (DNS for the subdomain must resolve to the VPS):
   ```bash
   curl https://blofin-bridge.srv1370094.hstgr.cloud/health
   ```

## Updates

After code changes:
```bash
scp -r src config root@46.202.146.30:/docker/blofin-bridge/
ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose up -d --build"
```

## Rollback

```bash
ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose down"
```

## Verify
```bash
curl https://blofin-bridge.srv1370094.hstgr.cloud/health
```
Should return `{"status":"ok","env":"demo",...}`.
