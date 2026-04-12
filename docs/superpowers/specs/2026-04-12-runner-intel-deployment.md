# Runner-Intel VPS Deployment — Design Spec

**Status:** Approved
**Author:** Claude (with Rich)
**Date:** 2026-04-12
**Depends on:** Plans 1-3 complete (pipeline, scoring, paper executor, alerts)

---

## 1. Purpose

Deploy the runner-intel system as a headless Docker container on the existing VPS (46.202.146.30) alongside smc-trading. Paper mode only — for signal quality observation and evaluation.

## 2. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| HTTP / Traefik | None — headless container, no port exposed | No HTTP interface exists yet. Add Traefik when dashboard ships. |
| Wallet file | Mount smc-trading's `wallets.json` read-only | Single source of truth. smc-trading owns curation. |
| Code sync | Git clone + pull on VPS | Standard workflow. `git pull && docker compose up -d --build`. |
| Monitoring | Docker logs + restart policy | v1 doesn't need external monitoring. |

## 3. VPS layout

```
/docker/runner-intel/           ← deployment root (git clone of rakainu/Leverage)
├── meme-trading/
│   ├── runner/                 ← application code
│   │   ├── main.py
│   │   ├── config/
│   │   │   └── weights.yaml    ← hot-reloadable (container copy, editable via host mount)
│   │   └── ...
│   ├── Dockerfile.runner       ← runner-specific Dockerfile
│   └── docker-compose.runner.yml
├── .env.runner                 ← secrets (gitignored)
└── data/                       ← persistent, host-mounted
    └── runner.db               ← created at first run
```

External mount: `/docker/smc-trading/config/wallets.json` → `/app/config/wallets.json:ro`

## 4. Dockerfile — `meme-trading/Dockerfile.runner`

```dockerfile
FROM python:3.11-slim

RUN useradd --create-home --shell /bin/bash runner
WORKDIR /app

COPY runner/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY runner/ ./runner/

RUN mkdir -p /app/data /app/config && chown -R runner:runner /app
USER runner

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "runner.main"]
```

Non-root `runner` user. Unbuffered stdout for clean `docker logs` streaming.

## 5. Docker Compose — `meme-trading/docker-compose.runner.yml`

```yaml
services:
  runner-intel:
    build:
      context: .
      dockerfile: Dockerfile.runner
    container_name: runner-intel
    restart: unless-stopped
    env_file: ../.env.runner
    volumes:
      - ../data:/app/data
      - ./runner/config/weights.yaml:/app/config/weights.yaml
      - /docker/smc-trading/config/wallets.json:/app/config/wallets.json:ro
    environment:
      - TZ=UTC
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"
    healthcheck:
      test: ["CMD", "python", "-c", "import sqlite3; sqlite3.connect('/app/data/runner.db').execute('SELECT 1')"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 30s
```

No ports. Healthcheck validates DB is accessible. Log rotation 150MB total.

## 6. Environment variables — `.env.runner`

```env
RUNNER_HELIUS_API_KEY=<helius_key>
RUNNER_HELIUS_WS_URL=wss://mainnet.helius-rpc.com/?api-key=<helius_key>
RUNNER_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=<helius_key>
RUNNER_DB_PATH=/app/data/runner.db
RUNNER_WALLETS_JSON_PATH=/app/config/wallets.json
RUNNER_WEIGHTS_YAML_PATH=/app/config/weights.yaml
RUNNER_TELEGRAM_BOT_TOKEN=<lpwade_bot_token>
RUNNER_TELEGRAM_CHAT_ID=<radk9_chat_id>
RUNNER_LOG_LEVEL=INFO
RUNNER_ENABLE_EXECUTOR=true
```

Same Helius key as smc-trading. Same Telegram bot and chat ID.

## 7. Startup config log

`main.py` already logs a `"wired"` event with wallet count and rate limits. Add a startup confirmation block that logs (without exposing secrets):

```
runner_config:
  db_path: /app/data/runner.db
  wallets_file: /app/config/wallets.json (82 wallets loaded)
  weights_file: /app/config/weights.yaml
  telegram: enabled
  executor: enabled
  check_interval: 30s
  helius_host: mainnet.helius-rpc.com
```

Fail clearly at startup if wallets file is missing or invalid (already handled by `WalletRegistry.load()` which raises `FileNotFoundError`).

## 8. Makefile — `meme-trading/Makefile.runner`

```makefile
COMPOSE = docker compose -f docker-compose.runner.yml

.PHONY: build up down logs restart update bootstrap status db

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail 100

restart:
	$(COMPOSE) restart

update:
	git pull && $(COMPOSE) up -d --build

bootstrap:
	$(COMPOSE) run --rm runner-intel python -m runner.scripts.bootstrap_wallet_tiers

status:
	docker ps | grep runner-intel

db:
	docker exec runner-intel python -c "\
import sqlite3; \
c = sqlite3.connect('/app/data/runner.db'); \
print('scores:', c.execute('SELECT COUNT(*) FROM runner_scores').fetchone()[0]); \
print('positions:', c.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0]); \
print('open:', c.execute(\"SELECT COUNT(*) FROM paper_positions WHERE status='open'\").fetchone()[0])"
```

## 9. First-run deployment commands

```bash
# 1. Clone repo
ssh root@46.202.146.30
cd /docker
git clone https://github.com/rakainu/Leverage.git runner-intel
cd runner-intel

# 2. Create .env
cp meme-trading/runner/.env.example .env.runner
nano .env.runner  # fill in real keys

# 3. Create persistent data dir
mkdir -p data

# 4. Verify wallets file exists
ls -la /docker/smc-trading/config/wallets.json

# 5. Build
cd meme-trading && make -f Makefile.runner build

# 6. Bootstrap wallet tiers (one-time)
make -f Makefile.runner bootstrap

# 7. Start
make -f Makefile.runner up

# 8. Verify
make -f Makefile.runner status
make -f Makefile.runner logs
# Look for: "starting", "wired", "scoring_engine_start", "paper_executor_start"

# 9. Wait 60s, check health
docker inspect runner-intel --format='{{.State.Health.Status}}'
```

## 10. Update workflow

```bash
ssh root@46.202.146.30
cd /docker/runner-intel/meme-trading
make -f Makefile.runner update
```

That's `git pull && docker compose up -d --build`. Zero downtime for a headless service — new container starts, old one stops.

## 11. Hot-reload weights (no restart)

```bash
ssh root@46.202.146.30
nano /docker/runner-intel/meme-trading/runner/config/weights.yaml
# ScoringEngine picks up mtime change within 30s
```

## 12. Requirements.txt update

Add `python-telegram-bot` to `runner/requirements.txt` for the TelegramAlerter:

```
python-telegram-bot==21.7
```

## 13. Test strategy

No new functional tests. Deployment verification is manual:

1. Container starts and stays up for 60s
2. Healthcheck passes (`docker inspect` shows "healthy")
3. Logs show startup config confirmation
4. Logs show pipeline stages starting
5. First cluster signal produces a scored candidate log line
6. First eligible candidate produces a Telegram entry alert

---

**End of spec.**
