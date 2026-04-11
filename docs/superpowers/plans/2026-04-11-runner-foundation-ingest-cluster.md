# Runner Plan 1 — Foundation + Ingest + Cluster Engine

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the scaffold, configuration, database, rate-limited HTTP client, wallet-event ingest pipeline, and cluster detection engine with wallet tiering for the `runner` intelligence system. End state: real wallet events from Helius produce cluster signals to a queue, with only A+B tier wallets counted toward the threshold.

**Architecture:** New Python package at `meme-trading/runner/` with message-bus pipeline using `asyncio.Queue`. Each module has one responsibility and communicates through well-defined dataclasses. All external HTTP goes through a shared token-bucket rate limiter. State lives in its own SQLite DB (`runner.db`) with WAL mode. Hot-reloadable scoring config via YAML file mtime watcher.

**Tech Stack:**
- Python 3.11+, asyncio
- `aiosqlite` (DB), `httpx` (HTTP + WS rest), `websockets` (Helius WS)
- `pydantic-settings` (config), `pyyaml` (weights), `structlog` (JSON logs)
- `pytest`, `pytest-asyncio`, `respx` (httpx mocking), `freezegun` (time control)

**Reference spec:** `docs/superpowers/specs/2026-04-11-meme-runner-design.md`

**Parent folder:** All file paths below are relative to `meme-trading/runner/` unless stated otherwise.

**Porting sources:** Some tasks port logic from the existing `meme-trading/scanner/` and `meme-trading/engine/` modules. "Port" means: copy, adapt to new module paths, simplify where possible, keep the working WS reconnect + RPC pool logic.

---

## File Structure

```
meme-trading/runner/
├── __init__.py
├── main.py                         # asyncio.gather entrypoint
├── pyproject.toml                  # ruff + pytest config
├── requirements.txt
├── .env.example
├── .gitignore
│
├── config/
│   ├── __init__.py
│   ├── settings.py                 # pydantic-settings, RUNNER_ prefix
│   ├── weights.yaml                # scoring weights/gates, hot-reloadable
│   └── weights_loader.py           # mtime-watched YAML loader
│
├── db/
│   ├── __init__.py
│   ├── schema.sql                  # Phase 1-3 tables
│   └── database.py                 # aiosqlite singleton, WAL mode
│
├── utils/
│   ├── __init__.py
│   ├── http.py                     # token-bucket rate limiter
│   ├── logging.py                  # structlog JSON setup
│   ├── solana.py                   # lamport helpers, mint validation
│   └── time.py                     # utc now helper for tests
│
├── ingest/
│   ├── __init__.py
│   ├── events.py                   # BuyEvent dataclass
│   ├── rpc_pool.py                 # ported, simplified
│   ├── transaction_parser.py       # ported, adapted to rate-limited client
│   └── wallet_monitor.py           # WS logsSubscribe, chunked connections
│
└── cluster/
    ├── __init__.py
    ├── wallet_registry.py          # reads shared wallets.json
    ├── wallet_tier.py              # A/B/C/U tier cache + lookup
    └── convergence.py              # sliding window, A+B only

meme-trading/runner/tests/
├── __init__.py
├── conftest.py                     # shared fixtures: in-memory DB, fake http, fake ws
├── unit/
│   ├── __init__.py
│   ├── test_settings.py
│   ├── test_weights_loader.py
│   ├── test_logging.py
│   ├── test_database.py
│   ├── test_http.py
│   ├── test_events.py
│   ├── test_rpc_pool.py
│   ├── test_transaction_parser.py
│   ├── test_wallet_registry.py
│   ├── test_wallet_tier.py
│   └── test_convergence.py
├── integration/
│   ├── __init__.py
│   └── test_ingest_to_cluster.py   # end-to-end: WS msg → BuyEvent → cluster signal
└── fixtures/
    ├── helius_ws_logs_notification.json
    ├── helius_getTransaction_buy.json
    └── wallets_sample.json
```

---

## Phase 1 — Foundation (Tasks 1-6)

### Task 1: Project scaffold

**Files:**
- Create: `meme-trading/runner/__init__.py` (empty)
- Create: `meme-trading/runner/requirements.txt`
- Create: `meme-trading/runner/pyproject.toml`
- Create: `meme-trading/runner/.env.example`
- Create: `meme-trading/runner/.gitignore`
- Create: `meme-trading/runner/config/__init__.py` (empty)
- Create: `meme-trading/runner/db/__init__.py` (empty)
- Create: `meme-trading/runner/utils/__init__.py` (empty)
- Create: `meme-trading/runner/ingest/__init__.py` (empty)
- Create: `meme-trading/runner/cluster/__init__.py` (empty)
- Create: `meme-trading/runner/tests/__init__.py` (empty)
- Create: `meme-trading/runner/tests/unit/__init__.py` (empty)
- Create: `meme-trading/runner/tests/integration/__init__.py` (empty)
- Create: `meme-trading/runner/tests/fixtures/.gitkeep` (empty)

- [ ] **Step 1: Create requirements.txt**

```text
# Runtime
aiosqlite==0.20.0
httpx==0.27.2
websockets==13.1
pydantic==2.9.2
pydantic-settings==2.6.0
pyyaml==6.0.2
structlog==24.4.0
tenacity==9.0.0

# Dev / tests
pytest==8.3.3
pytest-asyncio==0.24.0
respx==0.21.1
freezegun==1.5.1
ruff==0.7.0
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
filterwarnings = [
    "ignore::DeprecationWarning",
    "ignore::PendingDeprecationWarning",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "ASYNC"]
ignore = ["E501"]
```

- [ ] **Step 3: Create .env.example**

```bash
# Runner Intelligence System — env vars with RUNNER_ prefix

# Database
RUNNER_DB_PATH=./data/runner.db

# Helius
RUNNER_HELIUS_API_KEY=your_helius_key_here
RUNNER_HELIUS_WS_URL=wss://mainnet.helius-rpc.com/?api-key=your_helius_key_here
RUNNER_HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=your_helius_key_here

# Wallet registry (path to shared wallets.json)
RUNNER_WALLETS_JSON_PATH=../config/wallets.json

# Weights YAML
RUNNER_WEIGHTS_YAML_PATH=./config/weights.yaml

# Telegram (for later phases)
RUNNER_TELEGRAM_BOT_TOKEN=
RUNNER_TELEGRAM_CHAT_ID=

# Runtime
RUNNER_LOG_LEVEL=INFO
RUNNER_ENABLE_EXECUTOR=true
```

- [ ] **Step 4: Create .gitignore**

```
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.ruff_cache/
.venv/
venv/
.env
*.db
*.db-shm
*.db-wal
data/
```

- [ ] **Step 5: Create all empty __init__.py files**

Create empty files at the paths listed above, and `tests/fixtures/.gitkeep` as placeholder.

- [ ] **Step 6: Install dependencies and verify pytest runs**

```bash
cd meme-trading/runner
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Unix:
# source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Expected: `no tests ran in X.XXs` (exit code 5 is fine — no tests yet).

- [ ] **Step 7: Commit**

```bash
git add meme-trading/runner/
git commit -m "runner: scaffold project structure and dependencies"
git push
```

---

### Task 2: Settings module

**Files:**
- Create: `meme-trading/runner/config/settings.py`
- Create: `meme-trading/runner/tests/unit/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_settings.py`:

```python
"""Settings loads env vars with RUNNER_ prefix."""
import pytest

from runner.config.settings import Settings


def test_settings_loads_required_fields(monkeypatch):
    monkeypatch.setenv("RUNNER_HELIUS_API_KEY", "test-key-123")
    monkeypatch.setenv("RUNNER_HELIUS_WS_URL", "wss://example.test/ws")
    monkeypatch.setenv("RUNNER_HELIUS_RPC_URL", "https://example.test/rpc")
    monkeypatch.setenv("RUNNER_WALLETS_JSON_PATH", "/tmp/wallets.json")
    monkeypatch.setenv("RUNNER_WEIGHTS_YAML_PATH", "/tmp/weights.yaml")
    monkeypatch.setenv("RUNNER_DB_PATH", "/tmp/runner.db")

    s = Settings()

    assert s.helius_api_key == "test-key-123"
    assert s.helius_ws_url == "wss://example.test/ws"
    assert s.helius_rpc_url == "https://example.test/rpc"
    assert s.db_path == "/tmp/runner.db"
    assert s.log_level == "INFO"           # default
    assert s.enable_executor is True       # default


def test_settings_respects_log_level_override(monkeypatch):
    monkeypatch.setenv("RUNNER_HELIUS_API_KEY", "k")
    monkeypatch.setenv("RUNNER_HELIUS_WS_URL", "wss://x")
    monkeypatch.setenv("RUNNER_HELIUS_RPC_URL", "https://x")
    monkeypatch.setenv("RUNNER_WALLETS_JSON_PATH", "/tmp/w.json")
    monkeypatch.setenv("RUNNER_WEIGHTS_YAML_PATH", "/tmp/w.yaml")
    monkeypatch.setenv("RUNNER_DB_PATH", "/tmp/r.db")
    monkeypatch.setenv("RUNNER_LOG_LEVEL", "DEBUG")

    s = Settings()
    assert s.log_level == "DEBUG"


def test_settings_missing_required_raises(monkeypatch):
    for var in [
        "RUNNER_HELIUS_API_KEY",
        "RUNNER_HELIUS_WS_URL",
        "RUNNER_HELIUS_RPC_URL",
        "RUNNER_WALLETS_JSON_PATH",
        "RUNNER_WEIGHTS_YAML_PATH",
        "RUNNER_DB_PATH",
    ]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(Exception):   # pydantic ValidationError
        Settings()
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd meme-trading
pytest runner/tests/unit/test_settings.py -v
```

Expected: ImportError / ModuleNotFoundError for `runner.config.settings`.

- [ ] **Step 3: Implement settings.py**

Create `runner/config/settings.py`:

```python
"""Runtime settings loaded from env vars with RUNNER_ prefix."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runner system settings.

    All env vars use the RUNNER_ prefix. Required fields have no default.
    """

    model_config = SettingsConfigDict(
        env_prefix="RUNNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Required
    helius_api_key: str
    helius_ws_url: str
    helius_rpc_url: str
    wallets_json_path: str
    weights_yaml_path: str
    db_path: str

    # Optional with defaults
    log_level: str = "INFO"
    enable_executor: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


_cached: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings singleton."""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset_settings_cache() -> None:
    """Clear the singleton cache — used by tests."""
    global _cached
    _cached = None
```

- [ ] **Step 4: Create a conftest.py that makes `runner` importable**

Create `meme-trading/runner/tests/conftest.py`:

```python
"""Shared test fixtures + path setup."""
import sys
from pathlib import Path

# Make the meme-trading directory importable so `runner.*` resolves
_MEME_TRADING = Path(__file__).resolve().parents[2]
if str(_MEME_TRADING) not in sys.path:
    sys.path.insert(0, str(_MEME_TRADING))
```

- [ ] **Step 5: Run test — expect PASS**

```bash
cd meme-trading
pytest runner/tests/unit/test_settings.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add meme-trading/runner/config/settings.py meme-trading/runner/tests/
git commit -m "runner: settings module with RUNNER_ prefix"
git push
```

---

### Task 3: Weights YAML loader

**Files:**
- Create: `meme-trading/runner/config/weights.yaml`
- Create: `meme-trading/runner/config/weights_loader.py`
- Create: `meme-trading/runner/tests/unit/test_weights_loader.py`

- [ ] **Step 1: Create weights.yaml with all defaults from spec**

Create `runner/config/weights.yaml`:

```yaml
cluster:
  min_wallets: 3
  window_minutes: 30
  speed_bonus_sweet_spot_min: 10
  speed_bonus_sweet_spot_max: 20

gates:
  lp_locked_pct_min: 85
  deployer_max_pct: 5
  top10_max_pct: 70
  token_age_min_sec: 120
  token_age_max_hr: 72

weights:
  wallet_quality: 0.20
  cluster_quality: 0.15
  entry_quality: 0.15
  holder_quality: 0.15
  rug_risk: 0.15
  follow_through: 0.15
  narrative: 0.05

verdict_thresholds:
  watch: 40
  strong_candidate: 60
  probable_runner: 78

position_sizing:
  strong_candidate_sol: 0.25
  probable_runner_sol: 0.375

probe:
  follow_through_minutes: 5

wallet_tier:
  a_tier_win_rate: 0.60
  a_tier_min_trades: 5
  b_tier_win_rate: 0.35
  rebuild_hour_utc: 4
  rolling_window_days: 30

http_rate_limits:
  helius_rps: 10
  rugcheck_rps: 2
  dexscreener_rps: 3
  jupiter_rps: 5
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_weights_loader.py`:

```python
"""Weights loader reads YAML and hot-reloads on mtime change."""
import time
from pathlib import Path

import pytest

from runner.config.weights_loader import WeightsLoader


@pytest.fixture
def yaml_file(tmp_path: Path) -> Path:
    p = tmp_path / "weights.yaml"
    p.write_text(
        """
cluster:
  min_wallets: 3
  window_minutes: 30
weights:
  wallet_quality: 0.20
  rug_risk: 0.15
verdict_thresholds:
  watch: 40
  strong_candidate: 60
"""
    )
    return p


def test_loads_initial_values(yaml_file: Path):
    loader = WeightsLoader(yaml_file)

    assert loader.get("cluster.min_wallets") == 3
    assert loader.get("cluster.window_minutes") == 30
    assert loader.get("weights.wallet_quality") == 0.20
    assert loader.get("verdict_thresholds.watch") == 40


def test_get_with_default(yaml_file: Path):
    loader = WeightsLoader(yaml_file)

    assert loader.get("weights.doesnotexist", default=99) == 99
    assert loader.get("missing.key") is None


def test_reloads_on_mtime_change(yaml_file: Path):
    loader = WeightsLoader(yaml_file)
    assert loader.get("verdict_thresholds.watch") == 40

    time.sleep(0.01)  # ensure mtime changes
    yaml_file.write_text(
        """
verdict_thresholds:
  watch: 50
"""
    )
    # Force mtime bump in case FS resolution is low
    yaml_file.touch()

    loader.check_and_reload()
    assert loader.get("verdict_thresholds.watch") == 50


def test_reload_is_noop_when_mtime_unchanged(yaml_file: Path):
    loader = WeightsLoader(yaml_file)
    before = loader.last_loaded_mtime

    loader.check_and_reload()

    assert loader.last_loaded_mtime == before


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        WeightsLoader(tmp_path / "nope.yaml")
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_weights_loader.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement weights_loader.py**

Create `runner/config/weights_loader.py`:

```python
"""Hot-reloadable YAML weights loader.

Watches file mtime; callers must invoke check_and_reload() periodically
(or on a fixed schedule). We deliberately do not spawn a background thread.
"""
from pathlib import Path
from typing import Any

import yaml


class WeightsLoader:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"weights file not found: {self.path}")
        self._data: dict[str, Any] = {}
        self.last_loaded_mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        self.last_loaded_mtime = self.path.stat().st_mtime

    def check_and_reload(self) -> bool:
        """Reload if the file has been modified since last load.

        Returns True if a reload happened, False otherwise.
        """
        mtime = self.path.stat().st_mtime
        if mtime > self.last_loaded_mtime:
            self._load()
            return True
        return False

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Look up a dotted key like 'weights.wallet_quality'.

        Returns default if any segment is missing.
        """
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data(self) -> dict[str, Any]:
        """Return the whole config tree (read-only snapshot)."""
        return self._data
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_weights_loader.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add meme-trading/runner/config/weights.yaml meme-trading/runner/config/weights_loader.py meme-trading/runner/tests/unit/test_weights_loader.py
git commit -m "runner: hot-reloadable weights YAML loader"
git push
```

---

### Task 4: Structured logging

**Files:**
- Create: `meme-trading/runner/utils/logging.py`
- Create: `meme-trading/runner/tests/unit/test_logging.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_logging.py`:

```python
"""Logging emits JSON lines to stdout."""
import json
import logging

from runner.utils.logging import configure_logging, get_logger


def test_logger_emits_json(capsys):
    configure_logging(level="INFO")
    log = get_logger("test.module")

    log.info("hello_event", token="So111", amount=0.25)

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) >= 1

    payload = json.loads(lines[-1])
    assert payload["event"] == "hello_event"
    assert payload["token"] == "So111"
    assert payload["amount"] == 0.25
    assert payload["level"] == "info"
    assert payload["logger"] == "test.module"


def test_logger_respects_level(capsys):
    configure_logging(level="WARNING")
    log = get_logger("test.quiet")

    log.debug("debug_event")
    log.warning("warn_event")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    events = [p.get("event") for p in payloads]

    assert "debug_event" not in events
    assert "warn_event" in events


def test_configure_is_idempotent(capsys):
    configure_logging(level="INFO")
    configure_logging(level="INFO")
    log = get_logger("test.idem")
    log.info("only_once")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "only_once" in line]
    assert len(lines) == 1
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_logging.py -v
```

- [ ] **Step 3: Implement logging.py**

Create `runner/utils/logging.py`:

```python
"""Structured JSON logging to stdout via structlog."""
import logging
import sys

import structlog

_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Set up structlog + stdlib logging to emit JSON lines to stdout.

    Safe to call multiple times — idempotent.
    """
    global _configured
    if _configured:
        # Allow level updates on re-configure without duplicating handlers.
        logging.getLogger().setLevel(level.upper())
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    root = logging.getLogger()
    # Replace existing handlers to keep output clean during tests.
    root.handlers = [handler]
    root.setLevel(log_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )

    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog bound logger with the given name."""
    return structlog.get_logger(name)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_logging.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/utils/logging.py meme-trading/runner/tests/unit/test_logging.py
git commit -m "runner: structlog JSON logging setup"
git push
```

---

### Task 5: Database schema + aiosqlite singleton

**Files:**
- Create: `meme-trading/runner/db/schema.sql`
- Create: `meme-trading/runner/db/database.py`
- Create: `meme-trading/runner/tests/unit/test_database.py`

- [ ] **Step 1: Create schema.sql**

Create `runner/db/schema.sql`:

```sql
-- Runner intelligence DB schema (Phase 1-3 tables)

-- Raw buy events from wallet monitor.
CREATE TABLE IF NOT EXISTS buy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL UNIQUE,
    wallet_address TEXT NOT NULL,
    token_mint TEXT NOT NULL,
    sol_amount REAL NOT NULL,
    token_amount REAL NOT NULL,
    price_sol REAL NOT NULL,
    block_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_buy_events_mint_time ON buy_events(token_mint, block_time);
CREATE INDEX IF NOT EXISTS idx_buy_events_wallet_time ON buy_events(wallet_address, block_time);

-- Wallet tiers rebuilt nightly.
CREATE TABLE IF NOT EXISTS wallet_tiers (
    wallet_address TEXT PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('A', 'B', 'C', 'U')),
    win_rate REAL,
    trade_count INTEGER DEFAULT 0,
    pnl_sol REAL DEFAULT 0,
    source TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wallet_tiers_tier ON wallet_tiers(tier);

-- Flattened wallet trade history used by tier rebuilder.
CREATE TABLE IF NOT EXISTS wallet_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    token_mint TEXT NOT NULL,
    entry_price_sol REAL NOT NULL,
    exit_price_sol REAL,
    pnl_sol REAL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    is_win INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wallet_trades_wallet ON wallet_trades(wallet_address, entry_time);

-- Detected cluster signals (N A+B wallets within window).
CREATE TABLE IF NOT EXISTS cluster_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    wallet_count INTEGER NOT NULL,
    wallets_json TEXT NOT NULL,           -- JSON array of addresses
    tier_counts_json TEXT NOT NULL,       -- JSON {"A":2,"B":1}
    first_buy_time TIMESTAMP NOT NULL,
    last_buy_time TIMESTAMP NOT NULL,
    convergence_seconds INTEGER NOT NULL,
    mid_price_sol REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cluster_signals_mint ON cluster_signals(token_mint);
CREATE INDEX IF NOT EXISTS idx_cluster_signals_time ON cluster_signals(created_at);

-- Schema migration marker.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_database.py`:

```python
"""Database singleton creates schema, enables WAL, returns aiosqlite connection."""
import asyncio
from pathlib import Path

import pytest

from runner.db.database import Database


@pytest.mark.asyncio
async def test_database_creates_tables(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        rows = await cur.fetchall()
    names = {r[0] for r in rows}

    for expected in [
        "buy_events",
        "wallet_tiers",
        "wallet_trades",
        "cluster_signals",
        "schema_version",
    ]:
        assert expected in names, f"missing table {expected}"

    await db.close()


@pytest.mark.asyncio
async def test_database_enables_wal(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
    assert row[0].lower() == "wal"

    await db.close()


@pytest.mark.asyncio
async def test_insert_and_query_buy_event(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.execute(
        """
        INSERT INTO buy_events
        (signature, wallet_address, token_mint, sol_amount,
         token_amount, price_sol, block_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("sig1", "wallet1", "mint1", 0.5, 1000, 0.0005, "2026-04-11T10:00:00Z"),
    )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT signature, wallet_address FROM buy_events WHERE signature = ?",
        ("sig1",),
    ) as cur:
        row = await cur.fetchone()
    assert row == ("sig1", "wallet1")

    await db.close()


@pytest.mark.asyncio
async def test_database_is_idempotent_on_reconnect(tmp_path: Path):
    p = tmp_path / "r.db"
    db1 = Database(p)
    await db1.connect()
    await db1.close()

    db2 = Database(p)
    await db2.connect()
    async with db2.conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ) as cur:
        count = (await cur.fetchone())[0]
    assert count >= 5
    await db2.close()
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_database.py -v
```

- [ ] **Step 4: Implement database.py**

Create `runner/db/database.py`:

```python
"""aiosqlite singleton for runner.db with WAL mode and schema bootstrap."""
from pathlib import Path

import aiosqlite

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    """Simple async SQLite wrapper.

    Bootstraps the schema on connect, enables WAL mode, exposes the
    underlying aiosqlite connection as `.conn` for callers.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.execute("PRAGMA journal_mode=WAL;")
        await self.conn.execute("PRAGMA synchronous=NORMAL;")
        await self.conn.execute("PRAGMA foreign_keys=ON;")
        await self.conn.commit()
        await self._ensure_schema()

    async def _ensure_schema(self) -> None:
        assert self.conn is not None
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self.conn.executescript(schema_sql)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None


_singleton: Database | None = None


async def get_db(path: Path | str | None = None) -> Database:
    """Return a process-wide Database singleton.

    First call must supply `path`. Subsequent calls can omit it.
    """
    global _singleton
    if _singleton is None:
        if path is None:
            raise RuntimeError("get_db first call requires a path")
        _singleton = Database(path)
        await _singleton.connect()
    return _singleton


async def reset_db_singleton() -> None:
    """Close and clear the singleton — used by tests."""
    global _singleton
    if _singleton is not None:
        await _singleton.close()
        _singleton = None
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_database.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add meme-trading/runner/db/ meme-trading/runner/tests/unit/test_database.py
git commit -m "runner: database singleton with schema and WAL mode"
git push
```

---

### Task 6: Rate-limited HTTP client (token bucket)

**Files:**
- Create: `meme-trading/runner/utils/http.py`
- Create: `meme-trading/runner/tests/unit/test_http.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_http.py`:

```python
"""Token-bucket rate-limited httpx client."""
import asyncio
import time
from urllib.parse import urlparse

import httpx
import pytest
import respx

from runner.utils.http import RateLimitedClient, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_allows_initial_burst():
    bucket = TokenBucket(rate_per_sec=5, capacity=5)

    # Five immediate acquires should not block.
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05   # basically instant


@pytest.mark.asyncio
async def test_token_bucket_throttles_excess():
    bucket = TokenBucket(rate_per_sec=10, capacity=2)

    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start

    # We burned the 2 initial tokens, then must wait for 3 more at 10/s = 0.3s min.
    assert elapsed >= 0.25
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_rate_limited_client_applies_per_host_limit():
    client = RateLimitedClient(
        default_rps=100,
        per_host_rps={"api.slow.test": 5},
        timeout=5.0,
    )

    with respx.mock(base_url="https://api.slow.test") as mock:
        mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))

        start = time.monotonic()
        for _ in range(5):
            r = await client.get("https://api.slow.test/x")
            assert r.status_code == 200
        elapsed = time.monotonic() - start

        # 5 req at 5 RPS, capacity 5 burst → should finish fast
        assert elapsed < 1.0

    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limited_client_queues_excess_without_raising():
    client = RateLimitedClient(
        default_rps=100,
        per_host_rps={"api.slow.test": 5},
        timeout=5.0,
    )

    with respx.mock(base_url="https://api.slow.test") as mock:
        mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))

        start = time.monotonic()
        results = await asyncio.gather(
            *(client.get("https://api.slow.test/x") for _ in range(10))
        )
        elapsed = time.monotonic() - start

        assert all(r.status_code == 200 for r in results)
        # capacity 5 + refill 5/s → 10 requests should take at least ~0.8s
        assert elapsed >= 0.7

    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limited_client_retries_on_429():
    client = RateLimitedClient(default_rps=100, timeout=5.0, max_retries=3)

    with respx.mock(base_url="https://api.ratey.test") as mock:
        route = mock.get("/y").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        r = await client.get("https://api.ratey.test/y")
        assert r.status_code == 200
        assert route.call_count == 3

    await client.aclose()
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_http.py -v
```

- [ ] **Step 3: Implement http.py**

Create `runner/utils/http.py`:

```python
"""Token-bucket rate-limited HTTP client built on httpx.AsyncClient."""
import asyncio
import time
from urllib.parse import urlparse

import httpx

from runner.utils.logging import get_logger

logger = get_logger("runner.utils.http")


class TokenBucket:
    """Simple async token bucket.

    `rate_per_sec` tokens are added to the bucket each second up to `capacity`.
    Each `acquire()` removes one token, blocking until one is available.
    """

    def __init__(self, rate_per_sec: float, capacity: int | None = None):
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else rate_per_sec)
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                deficit = cost - self._tokens
                wait = deficit / self.rate
            # Sleep OUTSIDE the lock so other tasks can refill-check too.
            await asyncio.sleep(wait)


class RateLimitedClient:
    """httpx.AsyncClient wrapper with per-host token buckets + 429 retry."""

    def __init__(
        self,
        default_rps: float = 10.0,
        per_host_rps: dict[str, float] | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ):
        self._client = httpx.AsyncClient(timeout=timeout)
        self._default_rps = default_rps
        self._per_host_rps = per_host_rps or {}
        self._buckets: dict[str, TokenBucket] = {}
        self._buckets_lock = asyncio.Lock()
        self._max_retries = max_retries

    def _host_of(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _bucket_for(self, host: str) -> TokenBucket:
        async with self._buckets_lock:
            if host not in self._buckets:
                rps = self._per_host_rps.get(host, self._default_rps)
                # Capacity matches rate for a 1-second burst allowance.
                self._buckets[host] = TokenBucket(rps, capacity=max(1, int(rps)))
            return self._buckets[host]

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = self._host_of(url)
        bucket = await self._bucket_for(host)

        attempt = 0
        while True:
            await bucket.acquire()
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.HTTPError as e:
                if attempt >= self._max_retries:
                    raise
                backoff = min(2.0 ** attempt, 10.0)
                logger.warning(
                    "http_error_retry",
                    url=url,
                    error=str(e),
                    attempt=attempt,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                attempt += 1
                continue

            if resp.status_code == 429 and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2.0 ** attempt
                except ValueError:
                    wait = 2.0 ** attempt
                wait = max(0.0, min(wait, 10.0))
                logger.warning(
                    "http_429_retry",
                    url=url,
                    attempt=attempt,
                    wait=wait,
                )
                await asyncio.sleep(wait)
                attempt += 1
                continue

            if 500 <= resp.status_code < 600 and attempt < self._max_retries:
                backoff = min(2.0 ** attempt, 10.0)
                logger.warning(
                    "http_5xx_retry",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                attempt += 1
                continue

            return resp

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_http.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/utils/http.py meme-trading/runner/tests/unit/test_http.py
git commit -m "runner: rate-limited HTTP client with token buckets and 429 retry"
git push
```

---

## Phase 2 — Ingest (Tasks 7-10)

### Task 7: BuyEvent dataclass

**Files:**
- Create: `meme-trading/runner/ingest/events.py`
- Create: `meme-trading/runner/tests/unit/test_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_events.py`:

```python
"""BuyEvent dataclass serialization."""
from datetime import datetime, timezone

from runner.ingest.events import BuyEvent


def test_buy_event_fields():
    ev = BuyEvent(
        signature="sigABC",
        wallet_address="Wal1",
        token_mint="Mint1",
        sol_amount=0.5,
        token_amount=1234.5,
        price_sol=0.000405,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert ev.signature == "sigABC"
    assert ev.sol_amount == 0.5
    assert ev.block_time.year == 2026


def test_buy_event_to_db_row_matches_schema_columns():
    ev = BuyEvent(
        signature="sigX",
        wallet_address="W",
        token_mint="M",
        sol_amount=0.25,
        token_amount=500,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 5, 0, tzinfo=timezone.utc),
    )

    row = ev.to_db_row()
    # Must match buy_events schema insert order:
    # signature, wallet_address, token_mint, sol_amount,
    # token_amount, price_sol, block_time
    assert row[0] == "sigX"
    assert row[1] == "W"
    assert row[2] == "M"
    assert row[3] == 0.25
    assert row[4] == 500
    assert row[5] == 0.0005
    assert row[6] == "2026-04-11T10:05:00+00:00"
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_events.py -v
```

- [ ] **Step 3: Implement events.py**

Create `runner/ingest/events.py`:

```python
"""Ingest event dataclasses."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BuyEvent:
    """A single wallet buying a token — the atomic unit of ingest."""

    signature: str
    wallet_address: str
    token_mint: str
    sol_amount: float
    token_amount: float
    price_sol: float
    block_time: datetime

    def to_db_row(self) -> tuple:
        """Return a tuple matching the buy_events insert column order."""
        return (
            self.signature,
            self.wallet_address,
            self.token_mint,
            self.sol_amount,
            self.token_amount,
            self.price_sol,
            self.block_time.isoformat(),
        )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_events.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/ingest/events.py meme-trading/runner/tests/unit/test_events.py
git commit -m "runner: BuyEvent dataclass"
git push
```

---

### Task 8: RPC pool

**Files:**
- Create: `meme-trading/runner/ingest/rpc_pool.py`
- Create: `meme-trading/runner/tests/unit/test_rpc_pool.py`
- Porting reference: `meme-trading/scanner/rpc_pool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rpc_pool.py`:

```python
"""Round-robin RPC pool with health tracking."""
import pytest

from runner.ingest.rpc_pool import RpcPool


def test_pool_rotates_round_robin():
    pool = RpcPool(["a", "b", "c"])
    picks = [pool.next() for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]


def test_marking_unhealthy_skips_url():
    pool = RpcPool(["a", "b", "c"])
    pool.mark_unhealthy("b")
    picks = [pool.next() for _ in range(4)]
    # Only a and c should rotate
    assert set(picks) == {"a", "c"}
    assert "b" not in picks


def test_marking_healthy_restores_url():
    pool = RpcPool(["a", "b"])
    pool.mark_unhealthy("a")
    assert pool.next() == "b"
    pool.mark_healthy("a")

    picks = [pool.next() for _ in range(4)]
    assert set(picks) == {"a", "b"}


def test_empty_pool_raises():
    with pytest.raises(ValueError):
        RpcPool([])


def test_all_unhealthy_falls_back_to_full_list():
    pool = RpcPool(["a", "b"])
    pool.mark_unhealthy("a")
    pool.mark_unhealthy("b")
    # Must still return something — do not deadlock.
    assert pool.next() in {"a", "b"}
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_rpc_pool.py -v
```

- [ ] **Step 3: Implement rpc_pool.py**

Create `runner/ingest/rpc_pool.py`:

```python
"""Round-robin pool of RPC endpoints with health tracking."""
from itertools import cycle


class RpcPool:
    """Holds a list of RPC URLs, rotates through healthy ones.

    If all URLs are marked unhealthy, falls back to rotating the full list
    (so we keep retrying instead of deadlocking).
    """

    def __init__(self, urls: list[str]):
        if not urls:
            raise ValueError("RpcPool requires at least one URL")
        self._urls = list(urls)
        self._unhealthy: set[str] = set()
        self._iter = cycle(self._urls)

    def next(self) -> str:
        healthy = [u for u in self._urls if u not in self._unhealthy]
        if not healthy:
            # Everyone is flagged unhealthy — fall back to the full list
            # so the system keeps trying. Health resets as callers mark them.
            return next(self._iter)

        while True:
            candidate = next(self._iter)
            if candidate in healthy:
                return candidate

    def mark_unhealthy(self, url: str) -> None:
        self._unhealthy.add(url)

    def mark_healthy(self, url: str) -> None:
        self._unhealthy.discard(url)

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    @property
    def healthy_urls(self) -> list[str]:
        return [u for u in self._urls if u not in self._unhealthy]
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_rpc_pool.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/ingest/rpc_pool.py meme-trading/runner/tests/unit/test_rpc_pool.py
git commit -m "runner: RPC pool with round-robin and health tracking"
git push
```

---

### Task 9: Transaction parser (with recorded fixture)

**Files:**
- Create: `meme-trading/runner/ingest/transaction_parser.py`
- Create: `meme-trading/runner/utils/solana.py`
- Create: `meme-trading/runner/tests/fixtures/helius_getTransaction_buy.json`
- Create: `meme-trading/runner/tests/unit/test_transaction_parser.py`
- Porting reference: `meme-trading/scanner/transaction_parser.py`

- [ ] **Step 1: Create Solana helper constants**

Create `runner/utils/solana.py`:

```python
"""Solana constants and helpers."""

SOL_MINT = "So11111111111111111111111111111111111111112"
WSOL_MINT = SOL_MINT

STABLECOIN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

LAMPORTS_PER_SOL = 1_000_000_000


def lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


def is_quote_mint(mint: str) -> bool:
    """True if mint is SOL or a known stablecoin — the 'source' side of a buy."""
    return mint == SOL_MINT or mint in STABLECOIN_MINTS
```

- [ ] **Step 2: Create the fixture file**

Create `tests/fixtures/helius_getTransaction_buy.json`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "blockTime": 1744372800,
    "meta": {
      "err": null,
      "fee": 5000,
      "preTokenBalances": [
        {
          "accountIndex": 2,
          "mint": "So11111111111111111111111111111111111111112",
          "owner": "TestWallet11111111111111111111111111111111",
          "uiTokenAmount": {"uiAmount": 1.0, "decimals": 9}
        },
        {
          "accountIndex": 3,
          "mint": "TestTokenMint111111111111111111111111111111",
          "owner": "TestWallet11111111111111111111111111111111",
          "uiTokenAmount": {"uiAmount": 0.0, "decimals": 6}
        }
      ],
      "postTokenBalances": [
        {
          "accountIndex": 2,
          "mint": "So11111111111111111111111111111111111111112",
          "owner": "TestWallet11111111111111111111111111111111",
          "uiTokenAmount": {"uiAmount": 0.5, "decimals": 9}
        },
        {
          "accountIndex": 3,
          "mint": "TestTokenMint111111111111111111111111111111",
          "owner": "TestWallet11111111111111111111111111111111",
          "uiTokenAmount": {"uiAmount": 1250.0, "decimals": 6}
        }
      ]
    },
    "slot": 100000000,
    "transaction": {
      "signatures": ["TestSig11111111111111111111111111111111111111"]
    }
  }
}
```

- [ ] **Step 3: Write the failing test**

Create `tests/unit/test_transaction_parser.py`:

```python
"""Transaction parser extracts BuyEvent from Helius getTransaction response."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.ingest.rpc_pool import RpcPool
from runner.ingest.transaction_parser import TransactionParser
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "helius_getTransaction_buy.json"


@pytest.mark.asyncio
async def test_parses_buy_event_from_recorded_response():
    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))

        ev = await parser.parse_transaction(
            "TestSig11111111111111111111111111111111111111",
            "TestWallet11111111111111111111111111111111",
        )

    assert ev is not None
    assert ev.signature == "TestSig11111111111111111111111111111111111111"
    assert ev.wallet_address == "TestWallet11111111111111111111111111111111"
    assert ev.token_mint == "TestTokenMint111111111111111111111111111111"
    assert abs(ev.sol_amount - 0.5) < 1e-9         # 1.0 - 0.5 = 0.5 SOL out
    assert abs(ev.token_amount - 1250.0) < 1e-9    # token delta
    assert ev.price_sol == pytest.approx(0.5 / 1250.0)
    assert ev.block_time.year == 2025 or ev.block_time.year == 2026  # 1744372800 → 2025

    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_for_non_buy_transaction():
    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    # No token balance change → not a buy.
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "blockTime": 1744372800,
            "meta": {
                "err": None,
                "fee": 5000,
                "preTokenBalances": [],
                "postTokenBalances": [],
            },
            "transaction": {"signatures": ["sig"]},
        },
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))

        ev = await parser.parse_transaction("sig", "wallet")

    assert ev is None
    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_on_rpc_null_result():
    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": None}))

        ev = await parser.parse_transaction("sig", "wallet")

    assert ev is None
    await client.aclose()
```

- [ ] **Step 4: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_transaction_parser.py -v
```

- [ ] **Step 5: Implement transaction_parser.py**

Create `runner/ingest/transaction_parser.py`:

```python
"""Parse Solana transactions into BuyEvents using Helius RPC getTransaction."""
from datetime import datetime, timezone
from typing import Any

from runner.ingest.events import BuyEvent
from runner.ingest.rpc_pool import RpcPool
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger
from runner.utils.solana import is_quote_mint

logger = get_logger("runner.ingest.parser")


class TransactionParser:
    """Fetch a signed transaction via RPC and, if it's a buy, return a BuyEvent.

    "Buy" = wallet's SOL/stablecoin balance went DOWN and a non-quote token
    balance went UP within the same transaction.
    """

    def __init__(self, rpc_pool: RpcPool, http: RateLimitedClient):
        self.rpc_pool = rpc_pool
        self.http = http

    async def parse_transaction(
        self, signature: str, wallet_address: str
    ) -> BuyEvent | None:
        for attempt in range(3):
            rpc_url = self.rpc_pool.next()
            try:
                resp = await self.http.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            },
                        ],
                    },
                )
                data = resp.json()
                if "result" not in data or data["result"] is None:
                    return None
                self.rpc_pool.mark_healthy(rpc_url)
                return self._extract_buy_event(data["result"], signature, wallet_address)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "parse_transaction_retry",
                    signature=signature,
                    attempt=attempt,
                    error=str(e),
                )
                self.rpc_pool.mark_unhealthy(rpc_url)
        return None

    def _extract_buy_event(
        self, result: dict[str, Any], signature: str, wallet_address: str
    ) -> BuyEvent | None:
        meta = result.get("meta") or {}
        if meta.get("err") is not None:
            return None

        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []

        # Build mint → (pre, post) for this wallet only.
        deltas: dict[str, dict[str, float]] = {}

        for entry in pre:
            if entry.get("owner") != wallet_address:
                continue
            mint = entry.get("mint")
            if not mint:
                continue
            amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            deltas.setdefault(mint, {"pre": 0.0, "post": 0.0})["pre"] = amount

        for entry in post:
            if entry.get("owner") != wallet_address:
                continue
            mint = entry.get("mint")
            if not mint:
                continue
            amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            deltas.setdefault(mint, {"pre": 0.0, "post": 0.0})["post"] = amount

        sol_out: float | None = None
        token_in_mint: str | None = None
        token_in_amount: float | None = None

        for mint, d in deltas.items():
            change = d["post"] - d["pre"]
            if is_quote_mint(mint) and change < 0:
                sol_out = (sol_out or 0.0) + (-change)
            elif not is_quote_mint(mint) and change > 0:
                if token_in_amount is None or change > token_in_amount:
                    token_in_mint = mint
                    token_in_amount = change

        if not sol_out or not token_in_mint or not token_in_amount:
            return None

        price_sol = sol_out / token_in_amount if token_in_amount > 0 else 0.0
        block_time = datetime.fromtimestamp(
            int(result.get("blockTime") or 0), tz=timezone.utc
        )

        return BuyEvent(
            signature=signature,
            wallet_address=wallet_address,
            token_mint=token_in_mint,
            sol_amount=sol_out,
            token_amount=token_in_amount,
            price_sol=price_sol,
            block_time=block_time,
        )
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_transaction_parser.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add meme-trading/runner/ingest/transaction_parser.py meme-trading/runner/utils/solana.py meme-trading/runner/tests/fixtures/helius_getTransaction_buy.json meme-trading/runner/tests/unit/test_transaction_parser.py
git commit -m "runner: transaction parser with BuyEvent extraction"
git push
```

---

### Task 10: Wallet monitor (WS logsSubscribe)

**Files:**
- Create: `meme-trading/runner/ingest/wallet_monitor.py`
- Create: `meme-trading/runner/tests/unit/test_wallet_monitor.py`
- Porting reference: `meme-trading/scanner/wallet_monitor.py`

Wallet monitor receives signatures via Helius WS logsSubscribe, dedups them, and hands each `(signature, wallet)` pair to the parser. The parser's result is placed on the event bus.

We test the monitor's **core logic** — signature dedup, parse dispatch, bus emission — with a fake WS loop, not a real WebSocket. The live WS client is abstracted as a method we override in the test.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wallet_monitor.py`:

```python
"""Wallet monitor: dedup, parse, emit to bus."""
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from runner.ingest.events import BuyEvent
from runner.ingest.wallet_monitor import WalletMonitor


async def _fake_log_stream(messages: list[tuple[str, str]]) -> AsyncIterator[tuple[str, str]]:
    for sig, wallet in messages:
        yield sig, wallet


@pytest.mark.asyncio
async def test_emits_buy_event_when_parser_returns_one():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigA",
        wallet_address="W",
        token_mint="M",
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigA", "W")

    ev = bus.get_nowait()
    assert ev.signature == "sigA"
    assert parser.parse_transaction.call_count == 1


@pytest.mark.asyncio
async def test_ignores_duplicate_signature():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigDup",
        wallet_address="W",
        token_mint="M",
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigDup", "W")
    await monitor.handle_signature("sigDup", "W")
    await monitor.handle_signature("sigDup", "W")

    assert bus.qsize() == 1
    assert parser.parse_transaction.call_count == 1


@pytest.mark.asyncio
async def test_skips_unknown_wallet():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()

    monitor = WalletMonitor(
        wallets={"KnownWallet": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigX", "UnknownWallet")

    assert bus.empty()
    assert parser.parse_transaction.call_count == 0


@pytest.mark.asyncio
async def test_non_buy_transaction_does_not_emit():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = None  # parser says: not a buy

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigN", "W")

    assert bus.empty()


@pytest.mark.asyncio
async def test_seen_cache_is_bounded():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = None

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        max_seen=50,
    )

    for i in range(120):
        await monitor.handle_signature(f"sig{i}", "W")

    assert len(monitor._seen_signatures) <= 50
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_wallet_monitor.py -v
```

- [ ] **Step 3: Implement wallet_monitor.py**

Create `runner/ingest/wallet_monitor.py`:

```python
"""Wallet monitor: WS logsSubscribe + signature dispatch to parser + event bus.

The WS connection + chunked subscribe loop is in `run()`. The core unit
of work — `handle_signature()` — is pure and testable without a real socket.
"""
import asyncio
import json
from typing import Any

import websockets

from runner.ingest.events import BuyEvent
from runner.ingest.transaction_parser import TransactionParser
from runner.utils.logging import get_logger

logger = get_logger("runner.ingest.monitor")

WALLETS_PER_CONNECTION = 25


class WalletMonitor:
    """Monitor a set of wallets via Solana logsSubscribe, emit BuyEvents.

    `wallets` is a dict of wallet_address -> info dict (tier, source, etc).
    Signatures are deduplicated per process lifetime.
    """

    def __init__(
        self,
        wallets: dict[str, dict],
        event_bus: asyncio.Queue,
        parser: TransactionParser,
        ws_url: str = "",
        max_seen: int = 10000,
    ):
        self.wallets = wallets
        self.event_bus = event_bus
        self.parser = parser
        self.ws_url = ws_url
        self._seen_signatures: set[str] = set()
        self._max_seen = max_seen
        self._running = True

    async def handle_signature(self, signature: str, wallet_address: str) -> None:
        """Core per-signature handler — test entry point."""
        if wallet_address not in self.wallets:
            return
        if signature in self._seen_signatures:
            return

        if len(self._seen_signatures) >= self._max_seen:
            # Crude LRU-ish pruning — drop the oldest half.
            keep = list(self._seen_signatures)[-(self._max_seen // 2):]
            self._seen_signatures = set(keep)
        self._seen_signatures.add(signature)

        event = await self.parser.parse_transaction(signature, wallet_address)
        if event is None:
            return
        await self.event_bus.put(event)
        logger.info(
            "buy_event",
            signature=event.signature,
            wallet=event.wallet_address,
            mint=event.token_mint,
            sol=event.sol_amount,
        )

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Top-level WS loop. Chunks wallets across connections.

        Each chunk runs `_run_connection` as its own task.
        """
        addresses = list(self.wallets.keys())
        chunks = [
            addresses[i : i + WALLETS_PER_CONNECTION]
            for i in range(0, len(addresses), WALLETS_PER_CONNECTION)
        ]
        logger.info(
            "monitor_start",
            wallets=len(addresses),
            chunks=len(chunks),
            per_conn=WALLETS_PER_CONNECTION,
        )
        await asyncio.gather(
            *(self._run_connection(chunk) for chunk in chunks),
            return_exceptions=True,
        )

    async def _run_connection(self, chunk: list[str]) -> None:
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=30) as ws:
                    for idx, wallet in enumerate(chunk):
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": idx,
                                    "method": "logsSubscribe",
                                    "params": [
                                        {"mentions": [wallet]},
                                        {"commitment": "confirmed"},
                                    ],
                                }
                            )
                        )
                    backoff = 1.0
                    sub_to_wallet: dict[int, str] = {}

                    async for raw in ws:
                        msg = json.loads(raw)
                        # Subscription confirmation:
                        if "result" in msg and isinstance(msg["result"], int) and "id" in msg:
                            idx = msg["id"]
                            if 0 <= idx < len(chunk):
                                sub_to_wallet[msg["result"]] = chunk[idx]
                            continue
                        # Log notification:
                        if msg.get("method") != "logsNotification":
                            continue
                        params = msg.get("params") or {}
                        sub_id = params.get("subscription")
                        value = (params.get("result") or {}).get("value") or {}
                        sig = value.get("signature")
                        err = value.get("err")
                        wallet = sub_to_wallet.get(sub_id)
                        if not sig or err is not None or wallet is None:
                            continue
                        await self.handle_signature(sig, wallet)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ws_disconnect",
                    error=str(e),
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_wallet_monitor.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/ingest/wallet_monitor.py meme-trading/runner/tests/unit/test_wallet_monitor.py
git commit -m "runner: wallet monitor with WS logsSubscribe and dedup"
git push
```

---

## Phase 3 — Cluster Engine (Tasks 11-13)

### Task 11: Wallet registry (reads shared wallets.json)

**Files:**
- Create: `meme-trading/runner/cluster/wallet_registry.py`
- Create: `meme-trading/runner/tests/fixtures/wallets_sample.json`
- Create: `meme-trading/runner/tests/unit/test_wallet_registry.py`

- [ ] **Step 1: Create fixture**

Create `tests/fixtures/wallets_sample.json`:

```json
{
  "wallets": [
    {
      "address": "WalletA11111111111111111111111111111111111",
      "name": "smart-money-1",
      "source": "nansen",
      "tags": ["smart_money"],
      "active": true,
      "added_at": "2026-03-01T00:00:00Z"
    },
    {
      "address": "WalletB22222222222222222222222222222222222",
      "name": "gmgn-1",
      "source": "gmgn",
      "tags": ["gmgn"],
      "active": true,
      "added_at": "2026-03-01T00:00:00Z"
    },
    {
      "address": "WalletInactive3333333333333333333333333333",
      "name": "disabled",
      "source": "manual",
      "tags": [],
      "active": false,
      "added_at": "2026-03-01T00:00:00Z"
    },
    {
      "address": "WalletC44444444444444444444444444444444444",
      "name": "birdeye-1",
      "source": "birdeye",
      "tags": [],
      "active": true,
      "added_at": "2026-03-01T00:00:00Z"
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_wallet_registry.py`:

```python
"""Wallet registry loads shared wallets.json, filters active."""
from pathlib import Path

import pytest

from runner.cluster.wallet_registry import WalletRegistry

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "wallets_sample.json"


def test_loads_active_wallets_only():
    reg = WalletRegistry(FIX)
    reg.load()

    active = reg.active_addresses()
    assert "WalletA11111111111111111111111111111111111" in active
    assert "WalletB22222222222222222222222222222222222" in active
    assert "WalletC44444444444444444444444444444444444" in active
    assert "WalletInactive3333333333333333333333333333" not in active
    assert len(active) == 3


def test_get_wallet_info():
    reg = WalletRegistry(FIX)
    reg.load()

    info = reg.get("WalletA11111111111111111111111111111111111")
    assert info["source"] == "nansen"
    assert info["name"] == "smart-money-1"
    assert info["active"] is True


def test_unknown_wallet_returns_none():
    reg = WalletRegistry(FIX)
    reg.load()

    assert reg.get("unknown") is None


def test_active_count():
    reg = WalletRegistry(FIX)
    reg.load()
    assert reg.active_count() == 3


def test_missing_file_raises(tmp_path: Path):
    reg = WalletRegistry(tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        reg.load()


def test_reload_picks_up_changes(tmp_path: Path):
    p = tmp_path / "wallets.json"
    p.write_text(
        '{"wallets":[{"address":"A","name":"a","source":"m","tags":[],"active":true,"added_at":"2026-01-01T00:00:00Z"}]}'
    )
    reg = WalletRegistry(p)
    reg.load()
    assert reg.active_count() == 1

    p.write_text(
        '{"wallets":['
        '{"address":"A","name":"a","source":"m","tags":[],"active":true,"added_at":"2026-01-01T00:00:00Z"},'
        '{"address":"B","name":"b","source":"m","tags":[],"active":true,"added_at":"2026-01-01T00:00:00Z"}'
        ']}'
    )
    reg.load()
    assert reg.active_count() == 2
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_wallet_registry.py -v
```

- [ ] **Step 4: Implement wallet_registry.py**

Create `runner/cluster/wallet_registry.py`:

```python
"""Wallet registry — reads the shared meme-trading/config/wallets.json."""
import json
from pathlib import Path


class WalletRegistry:
    """Loads wallet entries from a shared JSON file.

    The file format matches the existing meme-trading/config/wallets.json:
    {
      "wallets": [
        {"address": "...", "name": "...", "source": "...",
         "tags": [...], "active": true, "added_at": "..."},
        ...
      ]
    }
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._wallets: dict[str, dict] = {}

    def load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"wallets file not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._wallets = {
            w["address"]: w for w in (data.get("wallets") or []) if "address" in w
        }

    def active_addresses(self) -> set[str]:
        return {addr for addr, w in self._wallets.items() if w.get("active")}

    def active_count(self) -> int:
        return len(self.active_addresses())

    def get(self, address: str) -> dict | None:
        return self._wallets.get(address)

    def all_addresses(self) -> set[str]:
        return set(self._wallets.keys())
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_wallet_registry.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add meme-trading/runner/cluster/wallet_registry.py meme-trading/runner/tests/fixtures/wallets_sample.json meme-trading/runner/tests/unit/test_wallet_registry.py
git commit -m "runner: wallet registry reads shared wallets.json"
git push
```

---

### Task 12: Wallet tier cache

**Files:**
- Create: `meme-trading/runner/cluster/wallet_tier.py`
- Create: `meme-trading/runner/tests/unit/test_wallet_tier.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_wallet_tier.py`:

```python
"""Wallet tier cache — loads from DB, defaults unknown → U."""
import pytest

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database


@pytest.mark.asyncio
async def test_loads_tiers_from_db(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.executemany(
        "INSERT INTO wallet_tiers (wallet_address, tier, win_rate, trade_count) VALUES (?,?,?,?)",
        [
            ("WA", "A", 0.70, 10),
            ("WB", "B", 0.45, 6),
            ("WC", "C", 0.20, 8),
        ],
    )
    await db.conn.commit()

    cache = WalletTierCache(db)
    await cache.load()

    assert cache.tier_of("WA") == Tier.A
    assert cache.tier_of("WB") == Tier.B
    assert cache.tier_of("WC") == Tier.C
    assert cache.tier_of("unknown") == Tier.U  # default for no record

    await db.close()


@pytest.mark.asyncio
async def test_tier_points_mapping(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()
    cache = WalletTierCache(db)
    await cache.load()

    assert Tier.A.points == 100
    assert Tier.B.points == 60
    assert Tier.C.points == 0
    assert Tier.U.points == 40

    await db.close()


@pytest.mark.asyncio
async def test_reload_picks_up_changes(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    cache = WalletTierCache(db)
    await cache.load()
    assert cache.tier_of("late_wallet") == Tier.U

    await db.conn.execute(
        "INSERT INTO wallet_tiers (wallet_address, tier, win_rate, trade_count) VALUES ('late_wallet','A',0.8,10)"
    )
    await db.conn.commit()

    await cache.load()
    assert cache.tier_of("late_wallet") == Tier.A

    await db.close()


@pytest.mark.asyncio
async def test_counts_a_b_wallets_in_list(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.executemany(
        "INSERT INTO wallet_tiers (wallet_address, tier, win_rate, trade_count) VALUES (?,?,?,?)",
        [("A1", "A", 0.7, 10), ("B1", "B", 0.5, 6), ("C1", "C", 0.2, 8)],
    )
    await db.conn.commit()

    cache = WalletTierCache(db)
    await cache.load()

    count = cache.count_ab(["A1", "B1", "C1", "unknown_wallet"])
    assert count == 2  # A1 + B1 only (C1 and unknown excluded)

    await db.close()
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_wallet_tier.py -v
```

- [ ] **Step 3: Implement wallet_tier.py**

Create `runner/cluster/wallet_tier.py`:

```python
"""Wallet tier enum + in-memory cache loaded from the wallet_tiers DB table."""
from enum import Enum

from runner.db.database import Database


class Tier(Enum):
    A = ("A", 100)
    B = ("B", 60)
    C = ("C", 0)
    U = ("U", 40)

    def __init__(self, label: str, points: int):
        self.label = label
        self.points = points

    @classmethod
    def from_label(cls, label: str) -> "Tier":
        for t in cls:
            if t.label == label:
                return t
        return cls.U


class WalletTierCache:
    """Reads wallet_tiers into an in-memory dict for fast lookups.

    U-tier is the default for wallets with no row (new wallets, pre-bootstrap).
    """

    def __init__(self, db: Database):
        self.db = db
        self._map: dict[str, Tier] = {}

    async def load(self) -> None:
        assert self.db.conn is not None
        new_map: dict[str, Tier] = {}
        async with self.db.conn.execute(
            "SELECT wallet_address, tier FROM wallet_tiers"
        ) as cur:
            async for wallet, tier_label in cur:
                new_map[wallet] = Tier.from_label(tier_label)
        self._map = new_map

    def tier_of(self, wallet_address: str) -> Tier:
        return self._map.get(wallet_address, Tier.U)

    def count_ab(self, wallets: list[str]) -> int:
        return sum(
            1 for w in wallets if self.tier_of(w) in (Tier.A, Tier.B)
        )

    def filter_ab(self, wallets: list[str]) -> list[str]:
        return [w for w in wallets if self.tier_of(w) in (Tier.A, Tier.B)]
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_wallet_tier.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/cluster/wallet_tier.py meme-trading/runner/tests/unit/test_wallet_tier.py
git commit -m "runner: wallet tier enum and cache"
git push
```

---

### Task 13: Convergence detector (A+B-only cluster)

**Files:**
- Create: `meme-trading/runner/cluster/convergence.py`
- Create: `meme-trading/runner/tests/unit/test_convergence.py`
- Porting reference: `meme-trading/engine/convergence.py` (don't copy — design is different)

The detector listens to `BuyEvent` on an event bus, maintains a per-token sliding window of A+B-tier buys, and emits a `ClusterSignal` when `min_wallets` distinct A+B wallets appear within `window_minutes`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_convergence.py`:

```python
"""Convergence detector with A+B tier gating."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal, ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping: dict[str, Tier]):
        self._map = mapping

    async def load(self):
        pass


def _ev(sig: str, wallet: str, mint: str, t: datetime) -> BuyEvent:
    return BuyEvent(
        signature=sig,
        wallet_address=wallet,
        token_mint=mint,
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=t,
    )


@pytest.mark.asyncio
async def test_emits_signal_when_three_ab_wallets_within_window(tmp_path):
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=8)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=14)))

    signal: ClusterSignal = sig_bus.get_nowait()
    assert signal.token_mint == "TOKEN"
    assert signal.wallet_count == 3
    assert set(signal.wallets) == {"A1", "A2", "B1"}
    assert signal.tier_counts == {"A": 2, "B": 1}
    assert 0 <= signal.convergence_seconds <= 30 * 60


@pytest.mark.asyncio
async def test_does_not_count_c_tier_wallets(tmp_path):
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "C1": Tier.C, "C2": Tier.C}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "C1", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "C2", "TOKEN", base + timedelta(minutes=10)))

    assert sig_bus.empty()


@pytest.mark.asyncio
async def test_window_expires_old_events():
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    # 40 minutes later (beyond 30m window)
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=40)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=45)))

    assert sig_bus.empty()   # only 2 remain in window


@pytest.mark.asyncio
async def test_same_wallet_twice_counts_once():
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A})
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A1", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "A1", "TOKEN", base + timedelta(minutes=10)))
    await det._process(_ev("s4", "A2", "TOKEN", base + timedelta(minutes=15)))

    # Only 2 distinct A+B wallets — not enough for min_wallets=3
    assert sig_bus.empty()


@pytest.mark.asyncio
async def test_does_not_signal_same_cluster_twice():
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    # First signal fires
    sig_bus.get_nowait()

    # Same cluster again — additional buys from the same set should not double-fire
    await det._process(_ev("s4", "A1", "TOKEN", base + timedelta(minutes=11)))
    await det._process(_ev("s5", "A2", "TOKEN", base + timedelta(minutes=12)))

    assert sig_bus.empty()


@pytest.mark.asyncio
async def test_mid_price_is_mean_of_cluster_prices():
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)

    def price_ev(sig, w, price):
        return BuyEvent(
            signature=sig,
            wallet_address=w,
            token_mint="TOKEN",
            sol_amount=0.25,
            token_amount=1000,
            price_sol=price,
            block_time=base,
        )

    await det._process(price_ev("s1", "A1", 0.0001))
    await det._process(price_ev("s2", "A2", 0.0002))
    await det._process(price_ev("s3", "B1", 0.0003))

    sig = sig_bus.get_nowait()
    assert abs(sig.mid_price_sol - 0.0002) < 1e-9
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest runner/tests/unit/test_convergence.py -v
```

- [ ] **Step 3: Implement convergence.py**

Create `runner/cluster/convergence.py`:

```python
"""Convergence detector: per-token sliding window, A+B-only counting.

Consumes BuyEvents from event_bus, emits ClusterSignals to signal_bus
when min_wallets distinct A+B-tier wallets buy the same token within
window_minutes.
"""
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.ingest.events import BuyEvent
from runner.utils.logging import get_logger

logger = get_logger("runner.cluster.convergence")


@dataclass(frozen=True)
class ClusterSignal:
    """Emitted when enough A+B wallets converge on a token."""

    token_mint: str
    wallets: list[str]
    wallet_count: int
    tier_counts: dict[str, int]
    first_buy_time: datetime
    last_buy_time: datetime
    convergence_seconds: int
    mid_price_sol: float


class ConvergenceDetector:
    def __init__(
        self,
        event_bus: asyncio.Queue,
        signal_bus: asyncio.Queue,
        tier_cache: WalletTierCache,
        min_wallets: int = 3,
        window_minutes: int = 30,
    ):
        self.event_bus = event_bus
        self.signal_bus = signal_bus
        self.tier_cache = tier_cache
        self.min_wallets = min_wallets
        self.window_minutes = window_minutes
        # per-token: list of BuyEvents inside the window
        self._window: dict[str, list[BuyEvent]] = defaultdict(list)
        # per-token: set of frozensets of wallet-address combinations we already signaled
        self._signaled: dict[str, set[frozenset[str]]] = defaultdict(set)

    async def run(self) -> None:
        logger.info(
            "convergence_start",
            min_wallets=self.min_wallets,
            window_minutes=self.window_minutes,
        )
        while True:
            event: BuyEvent = await self.event_bus.get()
            await self._process(event)

    async def _process(self, event: BuyEvent) -> None:
        # Reject C-tier immediately — they do not contribute to the cluster.
        tier = self.tier_cache.tier_of(event.wallet_address)
        if tier == Tier.C:
            return

        token = event.token_mint
        self._prune_expired(token, event.block_time)
        self._window[token].append(event)

        ab_events = [
            e
            for e in self._window[token]
            if self.tier_cache.tier_of(e.wallet_address) in (Tier.A, Tier.B)
        ]
        distinct_wallets = {e.wallet_address for e in ab_events}

        if len(distinct_wallets) < self.min_wallets:
            return

        cluster_key = frozenset(distinct_wallets)
        if cluster_key in self._signaled[token]:
            return
        self._signaled[token].add(cluster_key)

        wallet_events_by_addr: dict[str, BuyEvent] = {}
        for e in ab_events:
            # Keep earliest event per wallet for ordering/mid price.
            if (
                e.wallet_address not in wallet_events_by_addr
                or e.block_time < wallet_events_by_addr[e.wallet_address].block_time
            ):
                wallet_events_by_addr[e.wallet_address] = e
        picked = sorted(wallet_events_by_addr.values(), key=lambda x: x.block_time)

        tier_counts: dict[str, int] = {"A": 0, "B": 0}
        for e in picked:
            t = self.tier_cache.tier_of(e.wallet_address)
            if t == Tier.A:
                tier_counts["A"] += 1
            elif t == Tier.B:
                tier_counts["B"] += 1

        first_t = picked[0].block_time
        last_t = picked[-1].block_time
        mid_price = mean(e.price_sol for e in picked)

        signal = ClusterSignal(
            token_mint=token,
            wallets=[e.wallet_address for e in picked],
            wallet_count=len(picked),
            tier_counts=tier_counts,
            first_buy_time=first_t,
            last_buy_time=last_t,
            convergence_seconds=int((last_t - first_t).total_seconds()),
            mid_price_sol=mid_price,
        )
        logger.info(
            "cluster_signal",
            mint=token,
            wallets=signal.wallet_count,
            tier_counts=signal.tier_counts,
            convergence_seconds=signal.convergence_seconds,
        )
        await self.signal_bus.put(signal)

    def _prune_expired(self, token: str, now: datetime) -> None:
        cutoff = now - timedelta(minutes=self.window_minutes)
        self._window[token] = [
            e for e in self._window[token] if e.block_time >= cutoff
        ]
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest runner/tests/unit/test_convergence.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/cluster/convergence.py meme-trading/runner/tests/unit/test_convergence.py
git commit -m "runner: convergence detector with A+B wallet tier gating"
git push
```

---

### Task 14: Main wiring + smoke test

**Files:**
- Create: `meme-trading/runner/main.py`
- Create: `meme-trading/runner/utils/time.py`
- Create: `meme-trading/runner/tests/integration/test_ingest_to_cluster.py`

This task wires ingest → cluster into a runnable `main()` and adds an integration test that feeds a fake buy event through the whole pipeline.

- [ ] **Step 1: Create time helper**

Create `runner/utils/time.py`:

```python
"""Time helpers — exists so tests can monkey-patch utc_now() if needed."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

- [ ] **Step 2: Write the failing integration test**

Create `tests/integration/test_ingest_to_cluster.py`:

```python
"""End-to-end: BuyEvent through event_bus → cluster detector → signal_bus."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal, ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping):
        self._map = mapping

    async def load(self):
        pass


@pytest.mark.asyncio
async def test_end_to_end_event_to_signal():
    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()

    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    runner_task = asyncio.create_task(detector.run())

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    for i, (sig, wallet) in enumerate(
        [("s1", "A1"), ("s2", "A2"), ("s3", "B1")]
    ):
        await event_bus.put(
            BuyEvent(
                signature=sig,
                wallet_address=wallet,
                token_mint="MEME",
                sol_amount=0.25,
                token_amount=1000,
                price_sol=0.00025,
                block_time=base + timedelta(minutes=i * 5),
            )
        )

    signal: ClusterSignal = await asyncio.wait_for(signal_bus.get(), timeout=2.0)
    assert signal.token_mint == "MEME"
    assert signal.wallet_count == 3

    runner_task.cancel()
    try:
        await runner_task
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 3: Run test — expect FAIL initially (imports) then PASS after main.py**

```bash
pytest runner/tests/integration/test_ingest_to_cluster.py -v
```

This should actually pass even without main.py if the modules all import cleanly — the test is on the detector directly. We include it here as the milestone check for Phase 3.

- [ ] **Step 4: Create main.py wiring**

Create `runner/main.py`:

```python
"""Runner intelligence entrypoint — wires ingest + cluster into asyncio.gather."""
import asyncio

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_registry import WalletRegistry
from runner.cluster.wallet_tier import WalletTierCache
from runner.config.settings import get_settings
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.ingest.rpc_pool import RpcPool
from runner.ingest.transaction_parser import TransactionParser
from runner.ingest.wallet_monitor import WalletMonitor
from runner.utils.http import RateLimitedClient
from runner.utils.logging import configure_logging, get_logger


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("runner.main")
    logger.info("starting", log_level=settings.log_level)

    db = Database(settings.db_path)
    await db.connect()

    weights = WeightsLoader(settings.weights_yaml_path)

    registry = WalletRegistry(settings.wallets_json_path)
    registry.load()

    tier_cache = WalletTierCache(db)
    await tier_cache.load()

    http = RateLimitedClient(
        default_rps=weights.get("http_rate_limits.helius_rps", 10),
        per_host_rps={
            # configured per host as Phase 4+ adds more endpoints
        },
        timeout=15.0,
    )

    rpc_pool = RpcPool([settings.helius_rpc_url])
    parser = TransactionParser(rpc_pool, http)

    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()

    active = registry.active_addresses()
    wallets_map = {addr: registry.get(addr) for addr in active}

    monitor = WalletMonitor(
        wallets=wallets_map,
        event_bus=event_bus,
        parser=parser,
        ws_url=settings.helius_ws_url,
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        min_wallets=weights.get("cluster.min_wallets", 3),
        window_minutes=weights.get("cluster.window_minutes", 30),
    )

    logger.info(
        "wired",
        active_wallets=len(active),
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
    )

    try:
        await asyncio.gather(
            monitor.run(),
            detector.run(),
            # Placeholder: signal consumer for Phase 4+.
            _drain(signal_bus, logger),
        )
    finally:
        await http.aclose()
        await db.close()


async def _drain(signal_bus: asyncio.Queue, logger) -> None:
    """Phase 3 sink: log every signal. Phase 4 replaces this with enrichment."""
    while True:
        signal = await signal_bus.get()
        logger.info(
            "signal_drained",
            mint=signal.token_mint,
            wallets=signal.wallet_count,
            tier_counts=signal.tier_counts,
        )


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 5: Run the integration test — expect PASS**

```bash
pytest runner/tests/integration/test_ingest_to_cluster.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Run the full test suite**

```bash
pytest runner/tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Run ruff lint**

```bash
cd runner && ruff check . && ruff format --check .
```

Fix anything it complains about. If format is off:

```bash
ruff format .
```

Re-run tests and lint.

- [ ] **Step 8: Commit**

```bash
git add meme-trading/runner/main.py meme-trading/runner/utils/time.py meme-trading/runner/tests/integration/test_ingest_to_cluster.py
git commit -m "runner: wire ingest + cluster in main and add e2e integration test"
git push
```

---

## End-of-plan verification

- [ ] **Step 1: Full test run**

```bash
cd meme-trading
pytest runner/tests/ -v --tb=short
```

Expected: all tests pass, no warnings about missing fixtures.

- [ ] **Step 2: Lint pass**

```bash
cd meme-trading/runner
ruff check .
```

Expected: all checks passed.

- [ ] **Step 3: Sanity check the module layout**

```bash
cd meme-trading/runner
python -c "import runner.main; print('main importable')"
python -c "import runner.cluster.convergence; import runner.ingest.wallet_monitor; print('ok')"
```

Expected: prints without errors (it won't run main because env vars aren't set, but the import must succeed).

- [ ] **Step 4: Push final commit**

```bash
git status        # should be clean
git log --oneline -15   # verify our commits are all present
```

---

## What's next (Plan 2 preview)

Plan 1 gives us: a running scaffold that can receive wallet events and produce cluster signals, with tiering gating C-tier wallets out of the count. It does not yet enrich, filter, score, or trade.

**Plan 2** will cover Phases 4-6:
- Enrichment layer (Helius DAS token metadata, DexScreener price/liquidity, Jupiter quotes, Helius deployer history)
- Filter pipeline (RugCheck rug gate, holder filter, insider graph, entry quality, follow-through probe)
- Scoring engine (factor scorer, runner scorer, verdict assigner, explainer)

Plan 2 will be written *after* Plan 1 is merged, so it can reference real module shapes rather than planned ones.
