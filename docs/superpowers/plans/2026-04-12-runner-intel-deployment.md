# Runner-Intel VPS Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create Docker deployment files for the runner-intel system so it can run as a headless container on the VPS alongside smc-trading.

**Architecture:** Dockerfile.runner + docker-compose.runner.yml in `meme-trading/`, Makefile.runner for ops, wallet validation at startup, startup config log. No HTTP port, no Traefik. Mounts smc-trading's wallets.json read-only.

**Tech Stack:** Docker, docker-compose, Python 3.11-slim, Make

**Spec:** `docs/superpowers/specs/2026-04-12-runner-intel-deployment.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `meme-trading/Dockerfile.runner` | Container image for runner-intel |
| Create | `meme-trading/docker-compose.runner.yml` | Service definition, volumes, healthcheck |
| Create | `meme-trading/Makefile.runner` | Ops shortcuts |
| Modify | `meme-trading/runner/requirements.txt` | Add python-telegram-bot |
| Modify | `meme-trading/runner/cluster/wallet_registry.py` | Add validation for malformed/empty wallets |
| Modify | `meme-trading/runner/main.py` | Add startup config log block |
| Test | `meme-trading/runner/tests/unit/test_wallet_registry.py` | Validation tests |

---

### Task 1: Add python-telegram-bot dependency + wallet validation

**Files:**
- Modify: `meme-trading/runner/requirements.txt`
- Modify: `meme-trading/runner/cluster/wallet_registry.py`
- Test: `meme-trading/runner/tests/unit/test_wallet_registry.py`

- [ ] **Step 1: Write failing tests for wallet validation**

Append to `meme-trading/runner/tests/unit/test_wallet_registry.py`:

```python
def test_load_raises_on_empty_wallets_list(tmp_path):
    """Empty wallets list should raise ValueError."""
    p = tmp_path / "wallets.json"
    p.write_text('{"wallets": []}', encoding="utf-8")
    reg = WalletRegistry(p)
    with pytest.raises(ValueError, match="no valid wallet"):
        reg.load()


def test_load_raises_on_malformed_json(tmp_path):
    """Malformed JSON should raise."""
    p = tmp_path / "wallets.json"
    p.write_text("not json at all", encoding="utf-8")
    reg = WalletRegistry(p)
    with pytest.raises(Exception):  # json.JSONDecodeError
        reg.load()


def test_load_raises_on_missing_wallets_key(tmp_path):
    """JSON without 'wallets' key should raise ValueError."""
    p = tmp_path / "wallets.json"
    p.write_text('{"other": "data"}', encoding="utf-8")
    reg = WalletRegistry(p)
    with pytest.raises(ValueError, match="no valid wallet"):
        reg.load()


def test_load_raises_on_entries_without_address(tmp_path):
    """Wallet entries without 'address' field should be skipped; if all are invalid, raise."""
    p = tmp_path / "wallets.json"
    p.write_text('{"wallets": [{"name": "bad"}, {"name": "also bad"}]}', encoding="utf-8")
    reg = WalletRegistry(p)
    with pytest.raises(ValueError, match="no valid wallet"):
        reg.load()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest runner/tests/unit/test_wallet_registry.py -v --tb=short`

Expected: 3 of the 4 new tests FAIL (malformed JSON already raises, but the others don't raise ValueError yet).

- [ ] **Step 3: Add validation to `WalletRegistry.load()`**

In `meme-trading/runner/cluster/wallet_registry.py`, update the `load()` method:

```python
    def load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"wallets file not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._wallets = {
            w["address"]: w for w in (data.get("wallets") or []) if "address" in w
        }
        if not self._wallets:
            raise ValueError(
                f"wallets file has no valid wallet entries (need at least one "
                f"with 'address' field): {self.path}"
            )
```

- [ ] **Step 4: Add python-telegram-bot to requirements.txt**

In `meme-trading/runner/requirements.txt`, add after the `tenacity` line:

```
python-telegram-bot==21.7
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -15`

Expected: All tests pass (~202).

- [ ] **Step 6: Commit**

```bash
git add runner/requirements.txt runner/cluster/wallet_registry.py runner/tests/unit/test_wallet_registry.py
git commit -m "runner: wallet validation on load + python-telegram-bot dependency"
```

---

### Task 2: Startup config log in main.py

**Files:**
- Modify: `meme-trading/runner/main.py`

- [ ] **Step 1: Add startup config confirmation log**

In `meme-trading/runner/main.py`, after `registry.load()` (around line 44), and after the existing `logger.info("wired", ...)` block (around line 141), replace the existing `"wired"` log with an expanded version that includes deployment-relevant config:

Find the existing log block:
```python
    logger.info(
        "wired",
        active_wallets=len(active),
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
        helius_host=helius_host,
        helius_rps=helius_rps,
        dexscreener_rps=dexscreener_rps,
        jupiter_rps=jupiter_rps,
        rugcheck_rps=rugcheck_rps,
    )
```

Replace with:
```python
    logger.info(
        "runner_config",
        db_path=str(settings.db_path),
        wallets_file=str(settings.wallets_json_path),
        wallets_loaded=len(active),
        weights_file=str(settings.weights_yaml_path),
        telegram_enabled=bool(settings.telegram_bot_token and settings.telegram_chat_id),
        executor_enabled=settings.enable_executor,
        check_interval=weights.get("executor.check_interval_sec", 30),
        helius_host=helius_host,
        helius_rps=helius_rps,
        dexscreener_rps=dexscreener_rps,
        jupiter_rps=jupiter_rps,
        rugcheck_rps=rugcheck_rps,
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
    )
```

- [ ] **Step 2: Verify import is clean**

Run: `python -c "from runner.main import _main; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Run all tests**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -5`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add runner/main.py
git commit -m "runner: expanded startup config log for deployment debugging"
```

---

### Task 3: Dockerfile + docker-compose + Makefile

**Files:**
- Create: `meme-trading/Dockerfile.runner`
- Create: `meme-trading/docker-compose.runner.yml`
- Create: `meme-trading/Makefile.runner`

- [ ] **Step 1: Create Dockerfile.runner**

Create `meme-trading/Dockerfile.runner`:

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

- [ ] **Step 2: Create docker-compose.runner.yml**

Create `meme-trading/docker-compose.runner.yml`:

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

- [ ] **Step 3: Create Makefile.runner**

Create `meme-trading/Makefile.runner`:

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
print('open:', c.execute(\"SELECT COUNT(*) FROM paper_positions WHERE status=\\\"open\\\"\").fetchone()[0])"
```

- [ ] **Step 4: Update .env.example with deployment paths**

In `meme-trading/runner/.env.example`, ensure the paths match the container layout:

```env
# Database
RUNNER_DB_PATH=/app/data/runner.db

# Helius
RUNNER_HELIUS_API_KEY=your_helius_key_here
RUNNER_HELIUS_WS_URL=wss://mainnet.helius-rpc.com/?api-key=your_helius_key_here
RUNNER_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=your_helius_key_here

# Wallet registry (mounted read-only from smc-trading)
RUNNER_WALLETS_JSON_PATH=/app/config/wallets.json

# Weights YAML (hot-reloadable)
RUNNER_WEIGHTS_YAML_PATH=/app/config/weights.yaml

# Telegram (lpwade_bot)
RUNNER_TELEGRAM_BOT_TOKEN=
RUNNER_TELEGRAM_CHAT_ID=

# Runtime
RUNNER_LOG_LEVEL=INFO
RUNNER_ENABLE_EXECUTOR=true
```

- [ ] **Step 5: Verify Dockerfile builds locally (syntax check)**

Run: `docker build -f Dockerfile.runner -t runner-intel-test . 2>&1 | tail -5`

If Docker is not available locally, skip this step — it will be tested on VPS.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.runner docker-compose.runner.yml Makefile.runner runner/.env.example
git commit -m "runner: Dockerfile + docker-compose + Makefile for VPS deployment"
```

---

### Task 4: Run full test suite + push

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest runner/tests/ -v 2>&1 | tail -5`

Expected: All tests pass (~202).

- [ ] **Step 2: Push all commits**

```bash
git push
```

- [ ] **Step 3: Verify commit log**

Run: `git log --oneline -5`

Expected: See the deployment commits.

---

### Task 5: VPS first-run deployment (manual — copy-paste commands)

This task is manual — run these commands via SSH on the VPS. Not automated by subagents.

- [ ] **Step 1: Clone repo**

```bash
ssh root@46.202.146.30
cd /docker
git clone https://github.com/rakainu/Leverage.git runner-intel
cd runner-intel
```

- [ ] **Step 2: Create .env.runner**

```bash
cp meme-trading/runner/.env.example .env.runner
nano .env.runner
# Fill in: HELIUS_API_KEY, HELIUS_WS_URL, HELIUS_RPC_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# Set paths to /app/data/runner.db, /app/config/wallets.json, /app/config/weights.yaml
```

- [ ] **Step 3: Create persistent data directory**

```bash
mkdir -p data
```

- [ ] **Step 4: Verify wallets file exists**

```bash
ls -la /docker/smc-trading/config/wallets.json
```

Expected: File exists, non-empty.

- [ ] **Step 5: Build**

```bash
cd meme-trading
make -f Makefile.runner build
```

- [ ] **Step 6: Bootstrap wallet tiers (one-time)**

```bash
make -f Makefile.runner bootstrap
```

Expected: Prints tier counts, exits cleanly.

- [ ] **Step 7: Start**

```bash
make -f Makefile.runner up
```

- [ ] **Step 8: Verify running**

```bash
make -f Makefile.runner status
# Should show runner-intel as "Up"

make -f Makefile.runner logs
# Look for: "starting", "runner_config" (startup log), pipeline stages starting
```

- [ ] **Step 9: Check health after 60s**

```bash
sleep 60
docker inspect runner-intel --format='{{.State.Health.Status}}'
```

Expected: `healthy`

- [ ] **Step 10: Verify DB**

```bash
make -f Makefile.runner db
```

Expected: Shows scores/positions/open counts (initially 0).

---

## Summary

| Task | What it does | Files |
|------|-------------|-------|
| 1 | Wallet validation + telegram dependency | 3 modified |
| 2 | Startup config log | 1 modified |
| 3 | Dockerfile + compose + Makefile | 4 created |
| 4 | Test suite + push | 0 |
| 5 | VPS first-run (manual) | 0 |

**Total: 5 tasks, ~4 new tests, ~4 commits, then manual VPS deployment**
