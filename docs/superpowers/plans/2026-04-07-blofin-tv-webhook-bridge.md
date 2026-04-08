# BloFin × TradingView Webhook Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI webhook bridge that receives SMRT Algo Pro V3 TradingView alerts and executes them as SOL-USDT perpetual trades on BloFin via ccxt, with P2 step-stop SL management.

**Architecture:** Single-process FastAPI service on the Hostinger VPS, backed by SQLite for position state, ccxt for BloFin REST calls, pluggable SLPolicy interface (P2 step-stop is the v1 default), Telegram notifications. Credentials live in a gitignored `.env`. Demo environment first, live after validation.

**Tech Stack:** Python 3.11 · FastAPI · uvicorn · ccxt · pydantic-settings · sqlite3 (stdlib) · httpx · pytest · pytest-asyncio · PyYAML · Docker

**Spec reference:** `docs/superpowers/specs/2026-04-07-blofin-tv-webhook-bridge-design.md`

---

## File Structure (target end state)

```
scripts/blofin-bridge/
├── .env                      # gitignored, user-managed secrets (exists)
├── .env.example              # template (exists)
├── pyproject.toml            # Task 1
├── README.md                 # Task 2
├── config/
│   └── blofin_bridge.yaml    # Task 6
├── src/blofin_bridge/
│   ├── __init__.py           # Task 1
│   ├── main.py               # Task 16 (FastAPI app)
│   ├── config.py             # Task 6 (pydantic-settings loader)
│   ├── sizing.py             # Task 3 (margin→contracts math)
│   ├── state.py              # Task 7 (SQLite DAO)
│   ├── blofin_client.py      # Tasks 8–9 (ccxt wrapper)
│   ├── notify.py             # Task 10 (Telegram)
│   ├── router.py             # Task 15 (action dispatch)
│   ├── policies/
│   │   ├── __init__.py
│   │   ├── base.py           # Task 4 (SLPolicy Protocol + types)
│   │   ├── p2_step_stop.py   # Task 4
│   │   ├── p1_breakeven.py   # Task 5 (stub)
│   │   ├── p3_trail.py       # Task 5 (stub)
│   │   └── p4_hybrid.py      # Task 5 (stub)
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── entry.py          # Task 11
│   │   ├── tp.py             # Task 12
│   │   ├── sl.py             # Task 13
│   │   └── reversal.py       # Task 14
│   └── db/
│       └── schema.sql        # Task 7
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Task 3
│   ├── test_sizing.py        # Task 3
│   ├── test_policies.py      # Task 4
│   ├── test_state.py         # Task 7
│   ├── test_blofin_client.py # Tasks 8–9
│   ├── test_handlers.py      # Tasks 11–14
│   ├── test_router.py        # Task 15
│   └── test_webhook_e2e.py   # Task 16
├── Dockerfile                # Task 19
├── docker-compose.yml        # Task 19
└── data/                     # runtime, gitignored (bridge.db lives here)
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `scripts/blofin-bridge/pyproject.toml`
- Create: `scripts/blofin-bridge/src/blofin_bridge/__init__.py`
- Create: `scripts/blofin-bridge/src/blofin_bridge/policies/__init__.py`
- Create: `scripts/blofin-bridge/src/blofin_bridge/handlers/__init__.py`
- Create: `scripts/blofin-bridge/src/blofin_bridge/db/__init__.py`
- Create: `scripts/blofin-bridge/tests/__init__.py`
- Modify: `Leverage/.gitignore` (add bridge data dir)

- [ ] **Step 1: Create pyproject.toml with pinned deps**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "blofin-bridge"
version = "0.1.0"
description = "TradingView Pro V3 -> BloFin perpetual futures webhook bridge"
requires-python = ">=3.11"
dependencies = [
    "fastapi==0.115.0",
    "uvicorn[standard]==0.32.0",
    "ccxt==4.4.50",
    "pydantic==2.9.2",
    "pydantic-settings==2.6.1",
    "httpx==0.27.2",
    "PyYAML==6.0.2",
]

[project.optional-dependencies]
dev = [
    "pytest==8.3.3",
    "pytest-asyncio==0.24.0",
    "pytest-mock==3.14.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create empty `__init__.py` files**

Create these as zero-byte files:
- `src/blofin_bridge/__init__.py`
- `src/blofin_bridge/policies/__init__.py`
- `src/blofin_bridge/handlers/__init__.py`
- `src/blofin_bridge/db/__init__.py`
- `tests/__init__.py`

- [ ] **Step 3: Append bridge data dir to Leverage/.gitignore**

Add these lines to the END of `C:/Users/rakai/Leverage/.gitignore`:

```
# blofin-bridge runtime
scripts/blofin-bridge/data/
scripts/blofin-bridge/*.db
scripts/blofin-bridge/*.db-journal
scripts/blofin-bridge/__pycache__/
scripts/blofin-bridge/src/**/__pycache__/
```

- [ ] **Step 4: Install dev deps in a venv**

Run from `scripts/blofin-bridge/`:

```bash
python -m venv venv
source venv/Scripts/activate  # Windows bash
pip install -e ".[dev]"
```

Expected: `Successfully installed blofin-bridge-0.1.0 ...`

- [ ] **Step 5: Commit**

```bash
cd C:/Users/rakai/Leverage
git add scripts/blofin-bridge/pyproject.toml scripts/blofin-bridge/src scripts/blofin-bridge/tests .gitignore
git commit -m "blofin-bridge: scaffold project with pinned deps"
```

---

## Task 2: README stub

**Files:**
- Create: `scripts/blofin-bridge/README.md`

- [ ] **Step 1: Write README**

```markdown
# BloFin × TradingView Webhook Bridge

Receives SMRT Algo Pro V3 TradingView alerts and executes them as SOL-USDT
perpetual futures trades on BloFin.

**Spec:** `docs/superpowers/specs/2026-04-07-blofin-tv-webhook-bridge-design.md`

## Quick start (dev)

1. Copy `.env.example` to `.env` and fill in BloFin API credentials.
2. Create venv: `python -m venv venv && source venv/Scripts/activate`
3. Install: `pip install -e ".[dev]"`
4. Run tests: `pytest`
5. Run locally: `uvicorn blofin_bridge.main:app --reload --port 8787`

## Environments

- `BLOFIN_ENV=demo` → `demo-trading-openapi.blofin.com` (paper funds)
- `BLOFIN_ENV=live` → `openapi.blofin.com` (real funds)

## Deploy

See `Dockerfile` and `docker-compose.yml`. Designed to run on the Hostinger
VPS under Traefik alongside the existing `openclaw-wmo9` stack.
```

- [ ] **Step 2: Commit**

```bash
git add scripts/blofin-bridge/README.md
git commit -m "blofin-bridge: add README stub"
```

---

## Task 3: Sizing math (pure function, TDD)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/sizing.py`
- Create: `scripts/blofin-bridge/tests/conftest.py`
- Create: `scripts/blofin-bridge/tests/test_sizing.py`

**Context:** BloFin's `size` field is contracts, not base tokens. For SOL-USDT, 1 contract ≈ 1 SOL with lot size 0.01 (confirm per instrument at runtime). This module does pure math: given margin/leverage/price/instrument metadata, return an integer-lot-multiple contract count.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import pytest


@pytest.fixture
def sol_instrument():
    """BloFin SOL-USDT instrument metadata (sampled)."""
    return {
        "instId": "SOL-USDT",
        "contractValue": 1.0,   # 1 contract = 1 SOL
        "minSize": 1.0,          # min 1 contract
        "lotSize": 1.0,          # increments of 1 contract
        "tickSize": 0.001,
    }
```

`tests/test_sizing.py`:

```python
import pytest
from blofin_bridge.sizing import contracts_for_margin, SizingError


def test_basic_margin_at_10x(sol_instrument):
    # $100 margin * 10x = $1000 notional
    # at $80/SOL = 12.5 SOL = 12 contracts (rounded down to lot)
    size = contracts_for_margin(
        margin_usdt=100,
        leverage=10,
        last_price=80.0,
        instrument=sol_instrument,
    )
    assert size == 12


def test_rounds_down_to_lot(sol_instrument):
    # margin that produces fractional contracts must floor
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=83.45,
        instrument=sol_instrument,
    )
    # 1000 / 83.45 = 11.98 -> 11
    assert size == 11


def test_below_min_size_raises(sol_instrument):
    # $5 margin * 10x / $80 = 0.625 SOL, below minSize 1.0
    with pytest.raises(SizingError, match="below minSize"):
        contracts_for_margin(
            margin_usdt=5, leverage=10, last_price=80.0,
            instrument=sol_instrument,
        )


def test_zero_leverage_raises(sol_instrument):
    with pytest.raises(SizingError, match="leverage must be positive"):
        contracts_for_margin(
            margin_usdt=100, leverage=0, last_price=80.0,
            instrument=sol_instrument,
        )


def test_partial_close_rounds_down(sol_instrument):
    from blofin_bridge.sizing import close_fraction_to_contracts
    # 40% of 12 contracts = 4.8 -> 4
    assert close_fraction_to_contracts(12, 0.40, sol_instrument) == 4


def test_partial_close_returns_zero_if_below_lot(sol_instrument):
    from blofin_bridge.sizing import close_fraction_to_contracts
    # 10% of 2 contracts = 0.2 -> 0
    assert close_fraction_to_contracts(2, 0.10, sol_instrument) == 0
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_sizing.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'blofin_bridge.sizing'`

- [ ] **Step 3: Implement `sizing.py`**

```python
"""Position sizing math: margin & leverage -> BloFin contracts."""
from __future__ import annotations
import math
from typing import TypedDict


class Instrument(TypedDict):
    instId: str
    contractValue: float
    minSize: float
    lotSize: float
    tickSize: float


class SizingError(ValueError):
    """Raised when requested size cannot be fulfilled (below min, zero, etc)."""


def contracts_for_margin(
    *,
    margin_usdt: float,
    leverage: float,
    last_price: float,
    instrument: Instrument,
) -> int:
    """Return integer contract count for the given margin/leverage/price.

    Rounds DOWN to the nearest lot-size multiple so BloFin cannot reject for
    lot increment violations. Raises SizingError if the result is below
    the instrument's minSize.
    """
    if leverage <= 0:
        raise SizingError("leverage must be positive")
    if margin_usdt <= 0:
        raise SizingError("margin_usdt must be positive")
    if last_price <= 0:
        raise SizingError("last_price must be positive")

    notional = margin_usdt * leverage
    base_qty = notional / last_price                    # e.g. SOL count
    raw_contracts = base_qty / instrument["contractValue"]

    lot = instrument["lotSize"]
    floored = math.floor(raw_contracts / lot) * lot

    if floored < instrument["minSize"]:
        raise SizingError(
            f"computed size {floored} is below minSize {instrument['minSize']}"
        )
    return int(floored)


def close_fraction_to_contracts(
    open_contracts: int,
    fraction: float,
    instrument: Instrument,
) -> int:
    """Return contract count to close for a fractional TP (e.g. 0.40 for TP1).

    Rounds DOWN to lot size. Returns 0 if the result is below one lot —
    caller is responsible for handling the "nothing to close" case.
    """
    if not 0 < fraction <= 1:
        raise SizingError("fraction must be in (0, 1]")
    raw = open_contracts * fraction
    lot = instrument["lotSize"]
    floored = math.floor(raw / lot) * lot
    return int(max(0, floored))
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/test_sizing.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/sizing.py scripts/blofin-bridge/tests/
git commit -m "blofin-bridge: add sizing math with TDD"
```

---

## Task 4: SLPolicy interface + P2 step-stop (TDD)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/policies/base.py`
- Create: `scripts/blofin-bridge/src/blofin_bridge/policies/p2_step_stop.py`
- Create: `scripts/blofin-bridge/tests/test_policies.py`

**Context:** A policy is a small stateless object that answers "given the position's state, what SL order should be live right now?" It does not touch BloFin directly — handlers call the policy and then issue BloFin orders. Keeping it stateless makes it trivially testable.

- [ ] **Step 1: Write the failing tests**

`tests/test_policies.py`:

```python
import pytest
from blofin_bridge.policies.base import Position, SLOrder
from blofin_bridge.policies.p2_step_stop import P2StepStop


@pytest.fixture
def long_position():
    return Position(
        symbol="SOL-USDT",
        side="long",
        entry_price=80.0,
        initial_size=12,
        current_size=12,
        tp_stage=0,
        tp1_fill_price=None,
        tp2_fill_price=None,
    )


@pytest.fixture
def short_position(long_position):
    return long_position._replace(side="short")


def test_p2_entry_long_places_safety_sl_below_entry(long_position):
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_entry(long_position)
    assert sl.trigger_price == pytest.approx(76.0)   # 80 * 0.95
    assert sl.side == "sell"
    assert sl.size == -1                              # entire position


def test_p2_entry_short_places_safety_sl_above_entry(short_position):
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_entry(short_position)
    assert sl.trigger_price == pytest.approx(84.0)   # 80 * 1.05
    assert sl.side == "buy"
    assert sl.size == -1


def test_p2_on_tp1_moves_long_sl_to_entry(long_position):
    pos_after_tp1 = long_position._replace(
        tp_stage=1, tp1_fill_price=82.0, current_size=7,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_tp(pos_after_tp1, tp_stage=1, tp_fill_price=82.0)
    assert sl.trigger_price == 80.0                   # entry (breakeven)
    assert sl.side == "sell"


def test_p2_on_tp2_moves_long_sl_to_tp1_price(long_position):
    pos_after_tp2 = long_position._replace(
        tp_stage=2, tp1_fill_price=82.0, tp2_fill_price=84.0, current_size=4,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_tp(pos_after_tp2, tp_stage=2, tp_fill_price=84.0)
    assert sl.trigger_price == 82.0                   # TP1 fill price
    assert sl.side == "sell"


def test_p2_on_tp3_returns_none_no_sl_needed(long_position):
    pos_after_tp3 = long_position._replace(
        tp_stage=3, tp1_fill_price=82.0, tp2_fill_price=84.0, current_size=0,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    # On TP3, position is fully closed — no new SL to set
    assert policy.on_tp(pos_after_tp3, tp_stage=3, tp_fill_price=86.0) is None


def test_p2_short_on_tp1_moves_sl_to_entry_from_above(short_position):
    pos_after_tp1 = short_position._replace(
        tp_stage=1, tp1_fill_price=78.0, current_size=7,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_tp(pos_after_tp1, tp_stage=1, tp_fill_price=78.0)
    assert sl.trigger_price == 80.0                   # entry
    assert sl.side == "buy"                           # short SL is a buy


def test_p2_on_tick_is_noop(long_position):
    policy = P2StepStop(safety_sl_pct=0.05)
    assert policy.on_tick(long_position, last_price=100.0) is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_policies.py -v
```

Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `policies/base.py`**

```python
"""SLPolicy interface and shared data types."""
from __future__ import annotations
from typing import NamedTuple, Optional, Protocol, Literal

Side = Literal["long", "short"]
OrderSide = Literal["buy", "sell"]


class Position(NamedTuple):
    symbol: str
    side: Side
    entry_price: float
    initial_size: int
    current_size: int
    tp_stage: int                         # 0, 1, 2, 3
    tp1_fill_price: Optional[float]
    tp2_fill_price: Optional[float]


class SLOrder(NamedTuple):
    symbol: str
    side: OrderSide                       # opposite of position side
    trigger_price: float
    size: int                             # -1 means "entire remaining position"


class SLPolicy(Protocol):
    def on_entry(self, position: Position) -> SLOrder: ...
    def on_tp(
        self,
        position: Position,
        tp_stage: int,
        tp_fill_price: float,
    ) -> Optional[SLOrder]: ...
    def on_tick(
        self,
        position: Position,
        last_price: float,
    ) -> Optional[SLOrder]: ...
```

- [ ] **Step 4: Implement `policies/p2_step_stop.py`**

```python
"""P2 step-stop policy: hard SL -> breakeven on TP1 -> TP1-price on TP2."""
from __future__ import annotations
from typing import Optional

from .base import Position, SLOrder, SLPolicy


class P2StepStop:
    """Default v1 policy.

    - Entry: hard SL at safety_sl_pct from entry (5% default)
    - TP1 hit: move SL to entry price (breakeven)
    - TP2 hit: move SL to TP1 fill price (locks >= TP1 profit)
    - TP3 hit: no new SL (position fully closed by caller)
    """

    def __init__(self, safety_sl_pct: float) -> None:
        if not 0 < safety_sl_pct < 1:
            raise ValueError("safety_sl_pct must be in (0, 1)")
        self.safety_sl_pct = safety_sl_pct

    def on_entry(self, position: Position) -> SLOrder:
        if position.side == "long":
            trigger = position.entry_price * (1 - self.safety_sl_pct)
            closing_side = "sell"
        else:
            trigger = position.entry_price * (1 + self.safety_sl_pct)
            closing_side = "buy"
        return SLOrder(
            symbol=position.symbol,
            side=closing_side,
            trigger_price=round(trigger, 8),
            size=-1,
        )

    def on_tp(
        self,
        position: Position,
        tp_stage: int,
        tp_fill_price: float,
    ) -> Optional[SLOrder]:
        closing_side = "sell" if position.side == "long" else "buy"
        if tp_stage == 1:
            return SLOrder(
                symbol=position.symbol,
                side=closing_side,
                trigger_price=position.entry_price,
                size=-1,
            )
        if tp_stage == 2:
            if position.tp1_fill_price is None:
                raise ValueError("tp2 fired without a stored tp1_fill_price")
            return SLOrder(
                symbol=position.symbol,
                side=closing_side,
                trigger_price=position.tp1_fill_price,
                size=-1,
            )
        # TP3: position is fully closed by the handler, no SL to set.
        return None

    def on_tick(
        self,
        position: Position,
        last_price: float,
    ) -> Optional[SLOrder]:
        # P2 is event-driven (on_tp only). No per-tick updates.
        return None


# Type check: make sure P2StepStop satisfies SLPolicy
_: SLPolicy = P2StepStop(0.05)
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
pytest tests/test_policies.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/policies/ scripts/blofin-bridge/tests/test_policies.py
git commit -m "blofin-bridge: add SLPolicy interface and P2 step-stop"
```

---

## Task 5: Policy stubs (P1, P3, P4)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/policies/p1_breakeven.py`
- Create: `scripts/blofin-bridge/src/blofin_bridge/policies/p3_trail.py`
- Create: `scripts/blofin-bridge/src/blofin_bridge/policies/p4_hybrid.py`

**Context:** These are placeholder classes so the config value `sl_policy: p1_breakeven` etc. resolves to a real class that raises NotImplementedError. Keeps the policy registry clean.

- [ ] **Step 1: Create `p1_breakeven.py`**

```python
"""P1 breakeven policy — STUB. Implement when needed."""
from __future__ import annotations
from typing import Optional
from .base import Position, SLOrder


class P1Breakeven:
    def __init__(self, safety_sl_pct: float) -> None:
        self.safety_sl_pct = safety_sl_pct

    def on_entry(self, position: Position) -> SLOrder:
        raise NotImplementedError("P1 breakeven not implemented yet")

    def on_tp(self, position, tp_stage, tp_fill_price) -> Optional[SLOrder]:
        raise NotImplementedError("P1 breakeven not implemented yet")

    def on_tick(self, position, last_price) -> Optional[SLOrder]:
        return None
```

- [ ] **Step 2: Create `p3_trail.py` with the same stub shape**

```python
"""P3 trail policy — STUB."""
from __future__ import annotations
from typing import Optional
from .base import Position, SLOrder


class P3Trail:
    def __init__(self, safety_sl_pct: float, trail_pct: float) -> None:
        self.safety_sl_pct = safety_sl_pct
        self.trail_pct = trail_pct

    def on_entry(self, position: Position) -> SLOrder:
        raise NotImplementedError("P3 trail not implemented yet")

    def on_tp(self, position, tp_stage, tp_fill_price) -> Optional[SLOrder]:
        raise NotImplementedError("P3 trail not implemented yet")

    def on_tick(self, position, last_price) -> Optional[SLOrder]:
        raise NotImplementedError("P3 trail not implemented yet")
```

- [ ] **Step 3: Create `p4_hybrid.py` with the same stub shape**

```python
"""P4 hybrid policy — STUB."""
from __future__ import annotations
from typing import Optional
from .base import Position, SLOrder


class P4Hybrid:
    def __init__(self, safety_sl_pct: float, trail_pct: float) -> None:
        self.safety_sl_pct = safety_sl_pct
        self.trail_pct = trail_pct

    def on_entry(self, position: Position) -> SLOrder:
        raise NotImplementedError("P4 hybrid not implemented yet")

    def on_tp(self, position, tp_stage, tp_fill_price) -> Optional[SLOrder]:
        raise NotImplementedError("P4 hybrid not implemented yet")

    def on_tick(self, position, last_price) -> Optional[SLOrder]:
        raise NotImplementedError("P4 hybrid not implemented yet")
```

- [ ] **Step 4: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/policies/
git commit -m "blofin-bridge: add P1/P3/P4 policy stubs"
```

---

## Task 6: Config loader

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/config.py`
- Create: `scripts/blofin-bridge/config/blofin_bridge.yaml`
- Create: `scripts/blofin-bridge/tests/test_config.py`

**Context:** pydantic-settings reads the `.env` file for secrets and the YAML file for tunables. Environment variables override YAML. The config object is the single source of truth for all runtime parameters.

- [ ] **Step 1: Create `config/blofin_bridge.yaml`**

```yaml
defaults:
  margin_usdt: 100
  leverage: 10
  margin_mode: isolated
  position_mode: net
  safety_sl_pct: 0.05
  tp_split: [0.40, 0.30, 0.30]
  sl_policy: p2_step_stop

symbols:
  SOL-USDT:
    enabled: true
    margin_usdt: 100
    leverage: 10
    margin_mode: isolated
    sl_policy: p2_step_stop
```

- [ ] **Step 2: Write failing tests**

`tests/test_config.py`:

```python
import os
from pathlib import Path

import pytest
import yaml

from blofin_bridge.config import Settings, load_config, SymbolConfig


def test_load_config_from_yaml(tmp_path, monkeypatch):
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": {
            "margin_usdt": 50, "leverage": 5, "margin_mode": "isolated",
            "position_mode": "net", "safety_sl_pct": 0.04,
            "tp_split": [0.5, 0.3, 0.2], "sl_policy": "p2_step_stop",
        },
        "symbols": {
            "SOL-USDT": {"enabled": True, "margin_usdt": 50, "leverage": 5,
                         "margin_mode": "isolated", "sl_policy": "p2_step_stop"},
        },
    }))
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "demo-k")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "demo-s")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "demo-p")
    monkeypatch.setenv("BLOFIN_LIVE_API_KEY", "live-k")
    monkeypatch.setenv("BLOFIN_LIVE_API_SECRET", "live-s")
    monkeypatch.setenv("BLOFIN_LIVE_PASSPHRASE", "live-p")
    monkeypatch.setenv("BRIDGE_SECRET", "x" * 20)
    monkeypatch.setenv("BLOFIN_ENV", "demo")

    cfg = load_config(yaml_path)

    assert cfg.blofin.env == "demo"
    assert cfg.blofin.api_key == "demo-k"        # demo keys selected
    assert cfg.defaults.margin_usdt == 50
    assert cfg.defaults.tp_split == [0.5, 0.3, 0.2]
    assert "SOL-USDT" in cfg.symbols
    assert cfg.symbols["SOL-USDT"].enabled is True


def test_missing_required_env_raises(tmp_path):
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text("defaults: {}\nsymbols: {}\n")
    # No env vars set — clear all bridge-relevant ones
    for k in (
        "BLOFIN_DEMO_API_KEY", "BLOFIN_DEMO_API_SECRET", "BLOFIN_DEMO_PASSPHRASE",
        "BLOFIN_LIVE_API_KEY", "BLOFIN_LIVE_API_SECRET", "BLOFIN_LIVE_PASSPHRASE",
        "BRIDGE_SECRET",
    ):
        os.environ.pop(k, None)
    with pytest.raises(Exception):
        load_config(yaml_path)


def test_live_env_with_missing_live_keys_raises(tmp_path, monkeypatch):
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": {
            "margin_usdt": 100, "leverage": 10, "margin_mode": "isolated",
            "position_mode": "net", "safety_sl_pct": 0.05,
            "tp_split": [0.4, 0.3, 0.3], "sl_policy": "p2_step_stop",
        },
        "symbols": {},
    }))
    monkeypatch.setenv("BLOFIN_ENV", "live")
    # Only demo keys present; live are empty
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "d")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "d")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "d")
    for k in ("BLOFIN_LIVE_API_KEY", "BLOFIN_LIVE_API_SECRET", "BLOFIN_LIVE_PASSPHRASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BRIDGE_SECRET", "x" * 20)
    with pytest.raises(ValueError, match="BLOFIN_ENV=live requires"):
        load_config(yaml_path)


def test_tp_split_must_sum_to_one(tmp_path, monkeypatch):
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": {
            "margin_usdt": 100, "leverage": 10, "margin_mode": "isolated",
            "position_mode": "net", "safety_sl_pct": 0.05,
            "tp_split": [0.5, 0.3, 0.3],          # sums to 1.1 - invalid
            "sl_policy": "p2_step_stop",
        },
        "symbols": {},
    }))
    for k, v in [
        ("BLOFIN_DEMO_API_KEY", "k"), ("BLOFIN_DEMO_API_SECRET", "s"),
        ("BLOFIN_DEMO_PASSPHRASE", "p"), ("BRIDGE_SECRET", "x" * 20),
        ("BLOFIN_ENV", "demo"),
    ]:
        monkeypatch.setenv(k, v)

    with pytest.raises(ValueError, match="tp_split must sum to 1.0"):
        load_config(yaml_path)
```

- [ ] **Step 3: Run tests, verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL — `blofin_bridge.config` not found.

- [ ] **Step 4: Implement `config.py`**

```python
"""Runtime configuration: pydantic-settings + YAML."""
from __future__ import annotations
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SLPolicyName = Literal["p1_breakeven", "p2_step_stop", "p3_trail", "p4_hybrid"]
MarginMode = Literal["isolated", "cross"]
PositionMode = Literal["net", "long_short"]


class _RawBloFinEnv(BaseSettings):
    """Raw env vars for both demo and live BloFin credentials."""
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )
    live_api_key: str = Field(alias="BLOFIN_LIVE_API_KEY", default="")
    live_api_secret: str = Field(alias="BLOFIN_LIVE_API_SECRET", default="")
    live_passphrase: str = Field(alias="BLOFIN_LIVE_PASSPHRASE", default="")
    demo_api_key: str = Field(alias="BLOFIN_DEMO_API_KEY", default="")
    demo_api_secret: str = Field(alias="BLOFIN_DEMO_API_SECRET", default="")
    demo_passphrase: str = Field(alias="BLOFIN_DEMO_PASSPHRASE", default="")
    env: Literal["demo", "live"] = Field(alias="BLOFIN_ENV", default="demo")


class BloFinCreds(BaseModel):
    """Resolved credentials — picks demo vs live based on env."""
    api_key: str
    api_secret: str
    passphrase: str
    env: Literal["demo", "live"]

    @classmethod
    def from_environment(cls) -> "BloFinCreds":
        raw = _RawBloFinEnv()
        if raw.env == "demo":
            if not (raw.demo_api_key and raw.demo_api_secret and raw.demo_passphrase):
                raise ValueError(
                    "BLOFIN_ENV=demo requires BLOFIN_DEMO_API_KEY / "
                    "BLOFIN_DEMO_API_SECRET / BLOFIN_DEMO_PASSPHRASE to be set"
                )
            return cls(
                api_key=raw.demo_api_key, api_secret=raw.demo_api_secret,
                passphrase=raw.demo_passphrase, env="demo",
            )
        if not (raw.live_api_key and raw.live_api_secret and raw.live_passphrase):
            raise ValueError(
                "BLOFIN_ENV=live requires BLOFIN_LIVE_API_KEY / "
                "BLOFIN_LIVE_API_SECRET / BLOFIN_LIVE_PASSPHRASE to be set"
            )
        return cls(
            api_key=raw.live_api_key, api_secret=raw.live_api_secret,
            passphrase=raw.live_passphrase, env="live",
        )


class BridgeCreds(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )
    shared_secret: str = Field(alias="BRIDGE_SECRET")
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN", default="")
    telegram_chat_id: str = Field(alias="TELEGRAM_CHAT_ID", default="")


class Defaults(BaseModel):
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    position_mode: PositionMode
    safety_sl_pct: float
    tp_split: list[float]
    sl_policy: SLPolicyName

    @field_validator("tp_split")
    @classmethod
    def _split_sums_to_one(cls, v: list[float]) -> list[float]:
        if len(v) != 3:
            raise ValueError("tp_split must have exactly 3 values")
        if abs(sum(v) - 1.0) > 1e-6:
            raise ValueError(f"tp_split must sum to 1.0, got {sum(v)}")
        return v


class SymbolConfig(BaseModel):
    enabled: bool
    margin_usdt: float
    leverage: float
    margin_mode: MarginMode
    sl_policy: SLPolicyName


class Settings(BaseModel):
    blofin: BloFinCreds
    bridge: BridgeCreds
    defaults: Defaults
    symbols: dict[str, SymbolConfig]


def load_config(yaml_path: Path) -> Settings:
    raw = yaml.safe_load(yaml_path.read_text())
    return Settings(
        blofin=BloFinCreds.from_environment(),
        bridge=BridgeCreds(),
        defaults=Defaults(**raw["defaults"]),
        symbols={
            name: SymbolConfig(**body)
            for name, body in (raw.get("symbols") or {}).items()
        },
    )
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/config.py scripts/blofin-bridge/config/ scripts/blofin-bridge/tests/test_config.py
git commit -m "blofin-bridge: add YAML + env config loader"
```

---

## Task 7: SQLite schema + DAO (TDD)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/db/schema.sql`
- Create: `scripts/blofin-bridge/src/blofin_bridge/state.py`
- Create: `scripts/blofin-bridge/tests/test_state.py`

**Context:** SQLite stores one row per open position and an event log. Synchronous `sqlite3` is fine for this throughput (~20 webhooks/day). FastAPI runs sync handlers in a threadpool.

- [ ] **Step 1: Create schema.sql**

```sql
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('long','short')),
    entry_price     REAL NOT NULL,
    initial_size    INTEGER NOT NULL,
    current_size    INTEGER NOT NULL,
    tp_stage        INTEGER NOT NULL DEFAULT 0,
    tp1_fill_price  REAL,
    tp2_fill_price  REAL,
    sl_order_id     TEXT,
    sl_policy       TEXT NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    realized_pnl    REAL,
    source          TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol_open
    ON positions (symbol) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER REFERENCES positions(id),
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    handled_at  TEXT,
    outcome     TEXT,
    error_msg   TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_received
    ON events (received_at);
```

- [ ] **Step 2: Write failing tests**

`tests/test_state.py`:

```python
from datetime import datetime, timezone

import pytest

from blofin_bridge.state import Store, PositionRow


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def test_create_and_fetch_open_position(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    assert pid > 0

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.id == pid
    assert row.symbol == "SOL-USDT"
    assert row.side == "long"
    assert row.entry_price == 80.0
    assert row.initial_size == 12
    assert row.current_size == 12
    assert row.tp_stage == 0
    assert row.closed_at is None


def test_get_open_position_returns_none_when_flat(store):
    assert store.get_open_position("SOL-USDT") is None


def test_record_tp_fill_updates_stage_and_size(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_tp_fill(pid, stage=1, fill_price=82.0, closed_contracts=4)
    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.tp1_fill_price == 82.0
    assert row.current_size == 8


def test_close_position_sets_closed_at(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.close_position(pid, realized_pnl=42.5)
    assert store.get_open_position("SOL-USDT") is None
    closed = store.get_position(pid)
    assert closed.closed_at is not None
    assert closed.realized_pnl == 42.5


def test_record_sl_order_id(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "algo-123")
    row = store.get_position(pid)
    assert row.sl_order_id == "algo-123"


def test_append_event_and_update_outcome(store):
    eid = store.append_event(
        position_id=None, event_type="buy",
        payload='{"action":"buy"}',
    )
    store.mark_event_handled(eid, outcome="ok", error_msg=None)
    events = store.recent_events(limit=10)
    assert len(events) == 1
    assert events[0]["outcome"] == "ok"
```

- [ ] **Step 3: Run tests, verify they fail**

```bash
pytest tests/test_state.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `state.py`**

```python
"""SQLite-backed position & event store."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

SCHEMA_FILE = Path(__file__).parent / "db" / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PositionRow:
    id: int
    symbol: str
    side: str
    entry_price: float
    initial_size: int
    current_size: int
    tp_stage: int
    tp1_fill_price: Optional[float]
    tp2_fill_price: Optional[float]
    sl_order_id: Optional[str]
    sl_policy: str
    opened_at: str
    closed_at: Optional[str]
    realized_pnl: Optional[float]
    source: Optional[str]


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA_FILE.read_text())

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # -------- positions --------

    def create_position(
        self, *, symbol: str, side: str, entry_price: float,
        initial_size: int, sl_policy: str, source: str,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO positions
                  (symbol, side, entry_price, initial_size, current_size,
                   tp_stage, sl_policy, opened_at, source)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (symbol, side, entry_price, initial_size, initial_size,
                 sl_policy, _now_iso(), source),
            )
            return cur.lastrowid

    def get_position(self, pid: int) -> Optional[PositionRow]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM positions WHERE id = ?", (pid,)
            ).fetchone()
        return self._row_to_position(row) if row else None

    def get_open_position(self, symbol: str) -> Optional[PositionRow]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM positions WHERE symbol = ? AND closed_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        return self._row_to_position(row) if row else None

    def record_tp_fill(
        self, pid: int, *, stage: int, fill_price: float, closed_contracts: int,
    ) -> None:
        col = "tp1_fill_price" if stage == 1 else "tp2_fill_price" if stage == 2 else None
        with self._conn() as c:
            if col:
                c.execute(
                    f"UPDATE positions SET tp_stage = ?, {col} = ?, "
                    f"current_size = current_size - ? WHERE id = ?",
                    (stage, fill_price, closed_contracts, pid),
                )
            else:
                c.execute(
                    "UPDATE positions SET tp_stage = ?, "
                    "current_size = current_size - ? WHERE id = ?",
                    (stage, closed_contracts, pid),
                )

    def record_sl_order_id(self, pid: int, order_id: Optional[str]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET sl_order_id = ? WHERE id = ?",
                (order_id, pid),
            )

    def close_position(self, pid: int, *, realized_pnl: Optional[float]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET closed_at = ?, realized_pnl = ? WHERE id = ?",
                (_now_iso(), realized_pnl, pid),
            )

    def list_open_positions(self) -> list[PositionRow]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM positions WHERE closed_at IS NULL"
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    # -------- events --------

    def append_event(
        self, *, position_id: Optional[int], event_type: str, payload: str,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (position_id, event_type, payload, received_at) "
                "VALUES (?, ?, ?, ?)",
                (position_id, event_type, payload, _now_iso()),
            )
            return cur.lastrowid

    def mark_event_handled(
        self, eid: int, *, outcome: str, error_msg: Optional[str],
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE events SET handled_at = ?, outcome = ?, error_msg = ? "
                "WHERE id = ?",
                (_now_iso(), outcome, error_msg, eid),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> PositionRow:
        return PositionRow(
            id=row["id"], symbol=row["symbol"], side=row["side"],
            entry_price=row["entry_price"], initial_size=row["initial_size"],
            current_size=row["current_size"], tp_stage=row["tp_stage"],
            tp1_fill_price=row["tp1_fill_price"],
            tp2_fill_price=row["tp2_fill_price"],
            sl_order_id=row["sl_order_id"], sl_policy=row["sl_policy"],
            opened_at=row["opened_at"], closed_at=row["closed_at"],
            realized_pnl=row["realized_pnl"], source=row["source"],
        )
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
pytest tests/test_state.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/db/ scripts/blofin-bridge/src/blofin_bridge/state.py scripts/blofin-bridge/tests/test_state.py
git commit -m "blofin-bridge: add SQLite state store"
```

---

## Task 8: BloFin client — init, instruments, leverage

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/blofin_client.py`
- Create: `scripts/blofin-bridge/tests/test_blofin_client.py`

**Context:** ccxt handles BloFin's signing quirk. We wrap ccxt in a thin class so tests can swap it with a mock, and so we control which ccxt methods we depend on. The wrapper only exposes the methods the bridge actually uses.

- [ ] **Step 1: Write failing tests**

`tests/test_blofin_client.py`:

```python
from unittest.mock import MagicMock

import pytest

from blofin_bridge.blofin_client import BloFinClient, Instrument


@pytest.fixture
def mock_ccxt():
    m = MagicMock()
    m.load_markets.return_value = {
        "SOL/USDT:USDT": {
            "id": "SOL-USDT",
            "symbol": "SOL/USDT:USDT",
            "contractSize": 1.0,
            "limits": {"amount": {"min": 1.0}},
            "precision": {"amount": 1.0, "price": 0.001},
        }
    }
    return m


def test_client_loads_instruments(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    inst = client.get_instrument("SOL-USDT")
    assert isinstance(inst, dict)
    assert inst["instId"] == "SOL-USDT"
    assert inst["contractValue"] == 1.0
    assert inst["minSize"] == 1.0


def test_client_set_leverage_isolated_long(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.set_leverage("SOL-USDT", leverage=10, margin_mode="isolated")
    mock_ccxt.set_leverage.assert_called_once()
    args, kwargs = mock_ccxt.set_leverage.call_args
    assert args[0] == 10
    assert args[1] == "SOL/USDT:USDT"
    assert kwargs.get("params", {}).get("marginMode") == "isolated"


def test_get_unknown_instrument_raises(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    with pytest.raises(KeyError):
        client.get_instrument("DOGE-USDT")
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_blofin_client.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the client (init, instruments, leverage)**

```python
"""BloFin REST client wrapper (ccxt under the hood)."""
from __future__ import annotations
from typing import Any, Optional, TypedDict

import ccxt


class Instrument(TypedDict):
    instId: str
    contractValue: float
    minSize: float
    lotSize: float
    tickSize: float


def _instid_to_ccxt(inst_id: str) -> str:
    """'SOL-USDT' -> 'SOL/USDT:USDT' (ccxt's linear-swap symbol shape)."""
    base, quote = inst_id.split("-")
    return f"{base}/{quote}:{quote}"


def build_ccxt_client(
    *, api_key: str, secret: str, passphrase: str, env: str,
) -> ccxt.Exchange:
    cls = ccxt.blofin
    client = cls({
        "apiKey": api_key,
        "secret": secret,
        "password": passphrase,           # ccxt maps 'password' to BloFin passphrase
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    if env == "demo":
        client.set_sandbox_mode(True)
    return client


class BloFinClient:
    def __init__(self, *, ccxt_client: ccxt.Exchange) -> None:
        self._ccxt = ccxt_client
        self._instruments: dict[str, Instrument] = {}

    def load_instruments(self) -> None:
        markets = self._ccxt.load_markets()
        self._instruments.clear()
        for ccxt_sym, m in markets.items():
            inst_id = m.get("id")
            if not inst_id or "-" not in inst_id:
                continue
            limits_amt = (m.get("limits") or {}).get("amount") or {}
            precision = m.get("precision") or {}
            self._instruments[inst_id] = Instrument(
                instId=inst_id,
                contractValue=float(m.get("contractSize") or 1.0),
                minSize=float(limits_amt.get("min") or 1.0),
                lotSize=float(precision.get("amount") or 1.0),
                tickSize=float(precision.get("price") or 0.001),
            )

    def get_instrument(self, inst_id: str) -> Instrument:
        if inst_id not in self._instruments:
            raise KeyError(f"instrument {inst_id} not loaded")
        return self._instruments[inst_id]

    def set_leverage(
        self, inst_id: str, *, leverage: int, margin_mode: str,
    ) -> None:
        ccxt_sym = _instid_to_ccxt(inst_id)
        self._ccxt.set_leverage(
            leverage, ccxt_sym,
            params={"marginMode": margin_mode, "positionSide": "net"},
        )
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/test_blofin_client.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/blofin_client.py scripts/blofin-bridge/tests/test_blofin_client.py
git commit -m "blofin-bridge: add ccxt client wrapper (init/instruments/leverage)"
```

---

## Task 9: BloFin client — place order, TP/SL, close

**Files:**
- Modify: `scripts/blofin-bridge/src/blofin_bridge/blofin_client.py`
- Modify: `scripts/blofin-bridge/tests/test_blofin_client.py`

**Context:** Extend the wrapper with the four trade-path methods the handlers need: `place_market_entry` (with attached SL), `place_tpsl_order` (standalone SL after TP), `cancel_tpsl`, `close_position_market` (reduce-only market close).

- [ ] **Step 1: Add failing tests**

Append to `tests/test_blofin_client.py`:

```python
def test_place_market_entry_with_attached_sl(mock_ccxt):
    mock_ccxt.create_order.return_value = {
        "id": "ord-1", "average": 80.12, "filled": 12,
    }
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    result = client.place_market_entry(
        inst_id="SOL-USDT", side="buy", contracts=12,
        safety_sl_trigger=76.0,
    )
    assert result["orderId"] == "ord-1"
    assert result["fill_price"] == 80.12

    mock_ccxt.create_order.assert_called_once()
    _, kwargs = mock_ccxt.create_order.call_args
    params = kwargs.get("params") or {}
    # Check that SL was attached
    assert params.get("slTriggerPrice") == 76.0
    assert params.get("slOrderPrice") == "-1"


def test_place_tpsl_order_returns_id(mock_ccxt):
    # ccxt doesn't have a dedicated tpsl method on BloFin; use privatePostTrade... style
    mock_ccxt.private_post_trade_order_tpsl = MagicMock(return_value={
        "code": "0",
        "data": [{"tpslId": "tpsl-42"}],
    })
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    tpsl_id = client.place_sl_order(
        inst_id="SOL-USDT", side="sell", trigger_price=80.0, margin_mode="isolated",
    )
    assert tpsl_id == "tpsl-42"


def test_cancel_tpsl_calls_correct_endpoint(mock_ccxt):
    mock_ccxt.private_post_trade_cancel_tpsl = MagicMock(return_value={"code": "0"})
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.cancel_tpsl("SOL-USDT", "tpsl-42")
    mock_ccxt.private_post_trade_cancel_tpsl.assert_called_once()


def test_close_position_market_uses_reduce_only(mock_ccxt):
    mock_ccxt.create_order.return_value = {"id": "close-1", "average": 83.5}
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.close_position_market(
        inst_id="SOL-USDT", side="sell", contracts=8,
    )
    _, kwargs = mock_ccxt.create_order.call_args
    assert kwargs["params"]["reduceOnly"] == "true"
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_blofin_client.py -v
```

Expected: 4 new tests fail with `AttributeError: 'BloFinClient' object has no attribute 'place_market_entry'`.

- [ ] **Step 3: Implement the four methods on `BloFinClient`**

Add to `blofin_client.py` inside the `BloFinClient` class:

```python
    def place_market_entry(
        self, *, inst_id: str, side: str, contracts: int,
        safety_sl_trigger: float,
    ) -> dict[str, Any]:
        """Market entry with an attached safety SL (OCO-style)."""
        ccxt_sym = _instid_to_ccxt(inst_id)
        params = {
            "marginMode": "isolated",
            "positionSide": "net",
            "slTriggerPrice": safety_sl_trigger,
            "slOrderPrice": "-1",         # -1 => market execution of SL
        }
        order = self._ccxt.create_order(
            symbol=ccxt_sym, type="market", side=side,
            amount=contracts, price=None, params=params,
        )
        return {
            "orderId": order.get("id"),
            "fill_price": float(order.get("average") or order.get("price") or 0),
            "filled": float(order.get("filled") or 0),
        }

    def place_sl_order(
        self, *, inst_id: str, side: str, trigger_price: float,
        margin_mode: str,
    ) -> str:
        """Standalone SL on the entire position. Returns tpslId."""
        resp = self._ccxt.private_post_trade_order_tpsl({
            "instId": inst_id,
            "marginMode": margin_mode,
            "positionSide": "net",
            "side": side,
            "slTriggerPrice": str(trigger_price),
            "slOrderPrice": "-1",
            "size": "-1",                  # -1 = full position
            "reduceOnly": "true",
        })
        if resp.get("code") != "0":
            raise RuntimeError(f"place_sl_order failed: {resp}")
        return resp["data"][0]["tpslId"]

    def cancel_tpsl(self, inst_id: str, tpsl_id: str) -> None:
        resp = self._ccxt.private_post_trade_cancel_tpsl({
            "tpslId": tpsl_id, "instId": inst_id,
        })
        if resp.get("code") not in ("0", 0):
            raise RuntimeError(f"cancel_tpsl failed: {resp}")

    def close_position_market(
        self, *, inst_id: str, side: str, contracts: int,
    ) -> dict[str, Any]:
        """Reduce-only market order to close N contracts."""
        ccxt_sym = _instid_to_ccxt(inst_id)
        params = {
            "marginMode": "isolated",
            "positionSide": "net",
            "reduceOnly": "true",
        }
        order = self._ccxt.create_order(
            symbol=ccxt_sym, type="market", side=side,
            amount=contracts, price=None, params=params,
        )
        return {
            "orderId": order.get("id"),
            "fill_price": float(order.get("average") or order.get("price") or 0),
        }

    def fetch_last_price(self, inst_id: str) -> float:
        ccxt_sym = _instid_to_ccxt(inst_id)
        ticker = self._ccxt.fetch_ticker(ccxt_sym)
        return float(ticker.get("last") or ticker.get("close"))

    def fetch_positions(self) -> list[dict[str, Any]]:
        return self._ccxt.fetch_positions()
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
pytest tests/test_blofin_client.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/blofin_client.py scripts/blofin-bridge/tests/test_blofin_client.py
git commit -m "blofin-bridge: add place_market_entry, tpsl, close, ticker methods"
```

---

## Task 10: Telegram notifier

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/notify.py`
- Create: `scripts/blofin-bridge/tests/test_notify.py`

**Context:** Simple httpx POST to `https://api.telegram.org/bot<token>/sendMessage`. If token/chat_id are empty (v1 optional), the notifier is a no-op. Messages are prefixed with `FROM: BLOFIN_BRIDGE` per house convention.

- [ ] **Step 1: Write failing tests**

`tests/test_notify.py`:

```python
from unittest.mock import MagicMock, patch

from blofin_bridge.notify import Notifier


def test_notifier_noop_when_unconfigured():
    n = Notifier(bot_token="", chat_id="")
    n.send("hello")  # should not raise, should not call httpx


def test_notifier_posts_to_telegram():
    with patch("blofin_bridge.notify.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        n = Notifier(bot_token="tok", chat_id="123")
        n.send("hello world")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "tok" in args[0]
        assert kwargs["json"]["chat_id"] == "123"
        assert kwargs["json"]["text"].startswith("FROM: BLOFIN_BRIDGE")
        assert "hello world" in kwargs["json"]["text"]


def test_notifier_swallows_http_error():
    with patch("blofin_bridge.notify.httpx.post", side_effect=Exception("boom")):
        n = Notifier(bot_token="tok", chat_id="123")
        n.send("test")  # must not raise
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_notify.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `notify.py`**

```python
"""Telegram notifier. No-op when unconfigured."""
from __future__ import annotations
import logging

import httpx

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        body = {
            "chat_id": self.chat_id,
            "text": f"FROM: BLOFIN_BRIDGE\n{text}",
        }
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            httpx.post(url, json=body, timeout=5.0)
        except Exception as exc:
            log.warning("telegram send failed: %s", exc)
```

- [ ] **Step 4: Verify tests pass**

```bash
pytest tests/test_notify.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/notify.py scripts/blofin-bridge/tests/test_notify.py
git commit -m "blofin-bridge: add Telegram notifier (no-op when unconfigured)"
```

---

## Task 11: Entry handler (buy / sell)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/handlers/entry.py`
- Create: `scripts/blofin-bridge/tests/test_handlers.py`

**Context:** The entry handler is the first handler that orchestrates sizing + state + BloFin client + policy. It's the template other handlers copy. It uses dependency injection so tests can pass mocks.

- [ ] **Step 1: Write failing tests**

`tests/test_handlers.py`:

```python
from unittest.mock import MagicMock

import pytest

from blofin_bridge.handlers.entry import handle_entry
from blofin_bridge.policies.p2_step_stop import P2StepStop
from blofin_bridge.state import Store


@pytest.fixture
def sol_instrument():
    return {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def blofin(sol_instrument):
    m = MagicMock()
    m.get_instrument.return_value = sol_instrument
    m.fetch_last_price.return_value = 80.0
    m.place_market_entry.return_value = {
        "orderId": "ord-1", "fill_price": 80.12, "filled": 12,
    }
    m.place_sl_order.return_value = "tpsl-1"
    return m


def test_buy_opens_long_and_sets_safety_sl(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    assert result["opened"] is True
    assert result["side"] == "long"

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.side == "long"
    assert row.initial_size == 12
    assert row.entry_price == 80.12
    assert row.sl_order_id is None    # attached SL, not standalone

    blofin.place_market_entry.assert_called_once()
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["side"] == "buy"
    assert kwargs["contracts"] == 12
    # safety_sl_trigger = 80 * 0.95 = 76.0
    assert kwargs["safety_sl_trigger"] == pytest.approx(76.0, rel=1e-3)


def test_sell_opens_short_with_sl_above_entry(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    handle_entry(
        action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    row = store.get_open_position("SOL-USDT")
    assert row.side == "short"
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["side"] == "sell"
    assert kwargs["safety_sl_trigger"] == pytest.approx(84.0, rel=1e-3)


def test_entry_rejected_if_position_already_open(store, blofin):
    store.create_position(
        symbol="SOL-USDT", side="long", entry_price=75.0,
        initial_size=5, sl_policy="p2_step_stop", source="pro_v3",
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    assert result["opened"] is False
    assert "already open" in result["reason"].lower()
    blofin.place_market_entry.assert_not_called()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_handlers.py -v
```

Expected: FAIL — handler not found.

- [ ] **Step 3: Implement `handlers/entry.py`**

```python
"""Entry handler: buy / sell."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..policies.base import Position, SLPolicy
from ..sizing import contracts_for_margin, SizingError
from ..state import Store


def handle_entry(
    *,
    action: str,                    # "buy" or "sell"
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    margin_usdt: float,
    leverage: float,
    margin_mode: str,
    sl_policy_name: str,
) -> dict[str, Any]:
    """Open a new long (buy) or short (sell) position with attached safety SL."""
    existing = store.get_open_position(symbol)
    if existing is not None:
        return {
            "opened": False,
            "reason": f"position already open on {symbol} (id={existing.id})",
        }

    instrument = blofin.get_instrument(symbol)
    last_price = blofin.fetch_last_price(symbol)

    try:
        contracts = contracts_for_margin(
            margin_usdt=margin_usdt,
            leverage=leverage,
            last_price=last_price,
            instrument=instrument,
        )
    except SizingError as exc:
        return {"opened": False, "reason": f"sizing error: {exc}"}

    side: str = "long" if action == "buy" else "short"

    # Compute safety SL trigger from policy (uses last_price as entry proxy).
    proxy_position = Position(
        symbol=symbol, side=side, entry_price=last_price,
        initial_size=contracts, current_size=contracts,
        tp_stage=0, tp1_fill_price=None, tp2_fill_price=None,
    )
    sl_plan = policy.on_entry(proxy_position)

    # Place the entry with the attached SL in one call.
    fill = blofin.place_market_entry(
        inst_id=symbol,
        side=action,
        contracts=contracts,
        safety_sl_trigger=sl_plan.trigger_price,
    )

    pid = store.create_position(
        symbol=symbol, side=side, entry_price=fill["fill_price"],
        initial_size=contracts, sl_policy=sl_policy_name, source="pro_v3",
    )

    return {
        "opened": True,
        "side": side,
        "position_id": pid,
        "entry_price": fill["fill_price"],
        "size": contracts,
        "safety_sl_trigger": sl_plan.trigger_price,
    }
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/test_handlers.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/handlers/entry.py scripts/blofin-bridge/tests/test_handlers.py
git commit -m "blofin-bridge: add entry (buy/sell) handler"
```

---

## Task 12: TP handler (tp1 / tp2 / tp3)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/handlers/tp.py`
- Modify: `scripts/blofin-bridge/tests/test_handlers.py`

**Context:** TP handler closes `tp_split[stage-1]` of the ORIGINAL size, cancels the old SL order, installs a new one per policy, and advances the stage in SQLite. TP3 closes the remainder and archives the position.

- [ ] **Step 1: Append failing tests to `tests/test_handlers.py`**

```python
from blofin_bridge.handlers.tp import handle_tp


@pytest.fixture
def long_position_row(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "tpsl-initial")
    return store.get_position(pid)


def test_tp1_closes_40pct_and_sets_new_sl_at_entry(
    store, blofin, long_position_row
):
    policy = P2StepStop(safety_sl_pct=0.05)
    blofin.close_position_market.return_value = {
        "orderId": "close-1", "fill_price": 82.0,
    }
    blofin.place_sl_order.return_value = "tpsl-be"

    result = handle_tp(
        tp_stage=1, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated",
        tp_split=[0.40, 0.30, 0.30],
    )
    assert result["closed_contracts"] == 4
    assert result["new_sl_trigger"] == 80.0

    # Old SL cancelled
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "tpsl-initial")
    # New SL placed at entry (breakeven)
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == 80.0

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.current_size == 8
    assert row.tp1_fill_price == 82.0
    assert row.sl_order_id == "tpsl-be"


def test_tp2_moves_sl_to_tp1_price(store, blofin, long_position_row):
    policy = P2StepStop(safety_sl_pct=0.05)
    # Simulate TP1 already happened
    store.record_tp_fill(long_position_row.id, stage=1, fill_price=82.0,
                         closed_contracts=4)
    store.record_sl_order_id(long_position_row.id, "tpsl-be")

    blofin.close_position_market.return_value = {
        "orderId": "close-2", "fill_price": 84.0,
    }
    blofin.place_sl_order.return_value = "tpsl-tp1"

    handle_tp(
        tp_stage=2, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 2
    # 30% of ORIGINAL 12 = 3.6 -> floored to 3 contracts
    assert row.current_size == 5
    # New SL is at tp1 fill price (82.0)
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == 82.0


def test_tp3_closes_remainder_and_archives(store, blofin, long_position_row):
    policy = P2StepStop(safety_sl_pct=0.05)
    # Simulate through TP2
    store.record_tp_fill(long_position_row.id, stage=1, fill_price=82.0, closed_contracts=4)
    store.record_tp_fill(long_position_row.id, stage=2, fill_price=84.0, closed_contracts=3)
    store.record_sl_order_id(long_position_row.id, "tpsl-tp1")

    blofin.close_position_market.return_value = {
        "orderId": "close-3", "fill_price": 86.0,
    }

    handle_tp(
        tp_stage=3, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    # Position should be closed
    assert store.get_open_position("SOL-USDT") is None
    # SL cancelled
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "tpsl-tp1")
    # No new SL placed
    blofin.place_sl_order.assert_not_called()


def test_tp_discarded_when_no_open_position(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_tp(
        tp_stage=1, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    assert result["handled"] is False
    assert "no open position" in result["reason"].lower()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/test_handlers.py -v
```

Expected: 4 new tests fail — handler not found.

- [ ] **Step 3: Implement `handlers/tp.py`**

```python
"""TP handler: tp1, tp2, tp3."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..policies.base import Position, SLPolicy
from ..sizing import close_fraction_to_contracts
from ..state import Store


def handle_tp(
    *,
    tp_stage: int,                # 1, 2, or 3
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    margin_mode: str,
    tp_split: list[float],
) -> dict[str, Any]:
    if tp_stage not in (1, 2, 3):
        raise ValueError(f"invalid tp_stage {tp_stage}")

    row = store.get_open_position(symbol)
    if row is None:
        return {"handled": False, "reason": "no open position; stale tp alert"}

    instrument = blofin.get_instrument(symbol)

    # Fraction of ORIGINAL initial_size to close at this stage
    fraction = tp_split[tp_stage - 1]

    if tp_stage == 3:
        # Close entire remainder regardless of split math.
        to_close = row.current_size
    else:
        to_close = close_fraction_to_contracts(
            row.initial_size, fraction, instrument,
        )
        to_close = min(to_close, row.current_size)

    if to_close <= 0:
        return {"handled": False, "reason": "nothing to close (below lot size)"}

    close_side = "sell" if row.side == "long" else "buy"
    fill = blofin.close_position_market(
        inst_id=symbol, side=close_side, contracts=to_close,
    )

    # Cancel the current SL regardless of stage
    if row.sl_order_id:
        blofin.cancel_tpsl(symbol, row.sl_order_id)
        store.record_sl_order_id(row.id, None)

    store.record_tp_fill(
        row.id, stage=tp_stage, fill_price=fill["fill_price"],
        closed_contracts=to_close,
    )

    # Reload the updated row to pass to the policy
    updated = store.get_open_position(symbol) if tp_stage < 3 else row
    if tp_stage == 3 or (updated and updated.current_size == 0):
        store.close_position(row.id, realized_pnl=None)
        return {
            "handled": True, "tp_stage": tp_stage,
            "closed_contracts": to_close,
            "archived": True,
        }

    # Compute new SL via the policy
    pos_for_policy = Position(
        symbol=symbol, side=updated.side, entry_price=updated.entry_price,
        initial_size=updated.initial_size, current_size=updated.current_size,
        tp_stage=updated.tp_stage,
        tp1_fill_price=updated.tp1_fill_price,
        tp2_fill_price=updated.tp2_fill_price,
    )
    new_sl = policy.on_tp(pos_for_policy, tp_stage=tp_stage,
                          tp_fill_price=fill["fill_price"])
    if new_sl is None:
        return {
            "handled": True, "tp_stage": tp_stage,
            "closed_contracts": to_close, "new_sl_trigger": None,
        }

    new_sl_id = blofin.place_sl_order(
        inst_id=symbol, side=new_sl.side,
        trigger_price=new_sl.trigger_price, margin_mode=margin_mode,
    )
    store.record_sl_order_id(row.id, new_sl_id)

    return {
        "handled": True, "tp_stage": tp_stage,
        "closed_contracts": to_close,
        "new_sl_trigger": new_sl.trigger_price,
        "new_sl_id": new_sl_id,
    }
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/test_handlers.py -v
```

Expected: 7 total passing (3 from entry + 4 from tp).

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/handlers/tp.py scripts/blofin-bridge/tests/test_handlers.py
git commit -m "blofin-bridge: add TP handler (tp1/tp2/tp3)"
```

---

## Task 13: SL handler (Pro V3 exit)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/handlers/sl.py`
- Modify: `scripts/blofin-bridge/tests/test_handlers.py`

**Context:** Pro V3's own SL alert is a "force exit now" signal. Handler cancels any outstanding SL algo and market-closes the full remaining position.

- [ ] **Step 1: Append failing test**

```python
from blofin_bridge.handlers.sl import handle_sl


def test_sl_force_closes_and_cancels_tpsl(store, blofin, long_position_row):
    blofin.close_position_market.return_value = {
        "orderId": "force-1", "fill_price": 78.0,
    }
    result = handle_sl(
        symbol="SOL-USDT", store=store, blofin=blofin,
    )
    assert result["closed"] is True
    assert store.get_open_position("SOL-USDT") is None
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "tpsl-initial")
    _, kwargs = blofin.close_position_market.call_args
    assert kwargs["side"] == "sell"
    assert kwargs["contracts"] == 12


def test_sl_noop_when_flat(store, blofin):
    result = handle_sl(symbol="SOL-USDT", store=store, blofin=blofin)
    assert result["closed"] is False
    blofin.close_position_market.assert_not_called()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_handlers.py -v
```

Expected: 2 new tests fail.

- [ ] **Step 3: Implement `handlers/sl.py`**

```python
"""SL handler: Pro V3 forced exit."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..state import Store


def handle_sl(
    *, symbol: str, store: Store, blofin: BloFinClient,
) -> dict[str, Any]:
    row = store.get_open_position(symbol)
    if row is None:
        return {"closed": False, "reason": "no open position"}

    close_side = "sell" if row.side == "long" else "buy"
    fill = blofin.close_position_market(
        inst_id=symbol, side=close_side, contracts=row.current_size,
    )

    if row.sl_order_id:
        try:
            blofin.cancel_tpsl(symbol, row.sl_order_id)
        except Exception:
            pass   # If the SL order already triggered, cancel will fail; safe to ignore
        store.record_sl_order_id(row.id, None)

    store.close_position(row.id, realized_pnl=None)
    return {
        "closed": True,
        "exit_price": fill["fill_price"],
        "closed_contracts": row.current_size,
    }
```

- [ ] **Step 4: Run, verify passing**

```bash
pytest tests/test_handlers.py -v
```

Expected: 9 passed (3 entry + 4 tp + 2 sl).

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/handlers/sl.py scripts/blofin-bridge/tests/test_handlers.py
git commit -m "blofin-bridge: add SL (Pro V3 forced exit) handler"
```

---

## Task 14: Reversal handler

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/handlers/reversal.py`
- Modify: `scripts/blofin-bridge/tests/test_handlers.py`

**Context:** Close the current (if any) + open the opposite. Uses `handle_sl` style close + `handle_entry` style open internally, but in a single transition so the two events are logged together.

- [ ] **Step 1: Append failing test**

```python
from blofin_bridge.handlers.reversal import handle_reversal


def test_reversal_buy_closes_short_and_opens_long(store, blofin):
    # Start with an open short
    pid = store.create_position(
        symbol="SOL-USDT", side="short", entry_price=85.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "tpsl-short")

    blofin.close_position_market.return_value = {
        "orderId": "close-1", "fill_price": 80.0,
    }
    blofin.place_market_entry.return_value = {
        "orderId": "open-1", "fill_price": 80.12, "filled": 12,
    }

    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_reversal(
        new_action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    assert result["closed_previous"] is True
    assert result["opened_new"] is True

    row = store.get_open_position("SOL-USDT")
    assert row.side == "long"
    # previous close happened AND a new entry happened
    assert blofin.cancel_tpsl.call_count == 1
    assert blofin.close_position_market.call_count == 1
    assert blofin.place_market_entry.call_count == 1


def test_reversal_with_no_prior_position_just_opens(store, blofin):
    blofin.place_market_entry.return_value = {
        "orderId": "open-1", "fill_price": 80.12, "filled": 12,
    }
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_reversal(
        new_action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    assert result["closed_previous"] is False
    assert result["opened_new"] is True
    blofin.close_position_market.assert_not_called()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_handlers.py -v
```

- [ ] **Step 3: Implement `handlers/reversal.py`**

```python
"""Reversal handler: close current + open opposite in one transition."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..policies.base import SLPolicy
from ..state import Store
from .entry import handle_entry
from .sl import handle_sl


def handle_reversal(
    *,
    new_action: str,                  # "buy" or "sell"
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    margin_usdt: float,
    leverage: float,
    margin_mode: str,
    sl_policy_name: str,
) -> dict[str, Any]:
    closed = handle_sl(symbol=symbol, store=store, blofin=blofin)
    opened = handle_entry(
        action=new_action, symbol=symbol,
        store=store, blofin=blofin, policy=policy,
        margin_usdt=margin_usdt, leverage=leverage,
        margin_mode=margin_mode, sl_policy_name=sl_policy_name,
    )
    return {
        "closed_previous": closed.get("closed", False),
        "opened_new": opened.get("opened", False),
        "close_result": closed,
        "open_result": opened,
    }
```

- [ ] **Step 4: Run, verify passing**

```bash
pytest tests/test_handlers.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/handlers/reversal.py scripts/blofin-bridge/tests/test_handlers.py
git commit -m "blofin-bridge: add reversal handler (close + re-open)"
```

---

## Task 15: Router (action dispatch)

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/router.py`
- Create: `scripts/blofin-bridge/tests/test_router.py`

**Context:** The router maps the `action` string from a webhook payload to the right handler, with per-symbol config resolution and policy instantiation. Keeping it in one place makes adding new actions trivial.

- [ ] **Step 1: Write failing tests**

`tests/test_router.py`:

```python
from unittest.mock import MagicMock

import pytest

from blofin_bridge.router import dispatch, UnknownAction
from blofin_bridge.state import Store
from blofin_bridge.policies.p2_step_stop import P2StepStop


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def blofin():
    m = MagicMock()
    m.get_instrument.return_value = {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }
    m.fetch_last_price.return_value = 80.0
    m.place_market_entry.return_value = {
        "orderId": "o1", "fill_price": 80.0, "filled": 12,
    }
    return m


@pytest.fixture
def cfg():
    return {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 10,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "safety_sl_pct": 0.05, "tp_split": [0.4, 0.3, 0.3],
        },
    }


def test_dispatch_buy_calls_entry_handler(store, blofin, cfg):
    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert result["opened"] is True


def test_dispatch_unknown_action_raises(store, blofin, cfg):
    with pytest.raises(UnknownAction):
        dispatch(
            action="wat", symbol="SOL-USDT",
            store=store, blofin=blofin, symbol_configs=cfg,
        )


def test_dispatch_disabled_symbol_rejected(store, blofin, cfg):
    cfg["SOL-USDT"]["enabled"] = False
    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert result["opened"] is False
    assert "disabled" in result["reason"].lower()


def test_dispatch_unknown_symbol_rejected(store, blofin, cfg):
    result = dispatch(
        action="buy", symbol="DOGE-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert "unknown symbol" in result["reason"].lower()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_router.py -v
```

- [ ] **Step 3: Implement `router.py`**

```python
"""Action dispatch: webhook payload -> correct handler."""
from __future__ import annotations
from typing import Any

from .blofin_client import BloFinClient
from .handlers.entry import handle_entry
from .handlers.reversal import handle_reversal
from .handlers.sl import handle_sl
from .handlers.tp import handle_tp
from .policies.p1_breakeven import P1Breakeven
from .policies.p2_step_stop import P2StepStop
from .policies.p3_trail import P3Trail
from .policies.p4_hybrid import P4Hybrid
from .state import Store


class UnknownAction(ValueError):
    pass


POLICY_REGISTRY = {
    "p1_breakeven": P1Breakeven,
    "p2_step_stop": P2StepStop,
    "p3_trail": P3Trail,
    "p4_hybrid": P4Hybrid,
}

VALID_ACTIONS = {
    "buy", "sell", "tp1", "tp2", "tp3", "sl",
    "reversal_buy", "reversal_sell",
}


def _build_policy(name: str, safety_sl_pct: float):
    cls = POLICY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown sl_policy {name}")
    return cls(safety_sl_pct=safety_sl_pct)


def dispatch(
    *,
    action: str,
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    symbol_configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise UnknownAction(action)

    sym_cfg = symbol_configs.get(symbol)
    if sym_cfg is None:
        return {"opened": False, "handled": False,
                "reason": f"unknown symbol {symbol}"}
    if not sym_cfg.get("enabled", False):
        return {"opened": False, "handled": False,
                "reason": f"symbol {symbol} disabled in config"}

    policy = _build_policy(
        sym_cfg["sl_policy"], safety_sl_pct=sym_cfg["safety_sl_pct"],
    )

    if action in ("buy", "sell"):
        return handle_entry(
            action=action, symbol=symbol, store=store, blofin=blofin,
            policy=policy, margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"], margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
        )

    if action in ("tp1", "tp2", "tp3"):
        stage = int(action[-1])
        return handle_tp(
            tp_stage=stage, symbol=symbol, store=store, blofin=blofin,
            policy=policy, margin_mode=sym_cfg["margin_mode"],
            tp_split=sym_cfg["tp_split"],
        )

    if action == "sl":
        return handle_sl(symbol=symbol, store=store, blofin=blofin)

    if action.startswith("reversal_"):
        new_action = action.split("_", 1)[1]
        return handle_reversal(
            new_action=new_action, symbol=symbol, store=store, blofin=blofin,
            policy=policy, margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"], margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
        )

    raise UnknownAction(action)   # unreachable
```

- [ ] **Step 4: Run, verify passing**

```bash
pytest tests/test_router.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/router.py scripts/blofin-bridge/tests/test_router.py
git commit -m "blofin-bridge: add action dispatch router"
```

---

## Task 16: FastAPI app + webhook endpoint

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/main.py`
- Create: `scripts/blofin-bridge/tests/test_webhook_e2e.py`

**Context:** This is where HTTP lives. Single POST endpoint, pydantic model validates the body, shared-secret check, then hand off to `dispatch`. Keep the route thin.

- [ ] **Step 1: Write failing tests**

`tests/test_webhook_e2e.py`:

```python
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Write a minimal yaml config and point the app at it
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(
        "defaults:\n"
        "  margin_usdt: 100\n  leverage: 10\n  margin_mode: isolated\n"
        "  position_mode: net\n  safety_sl_pct: 0.05\n"
        "  tp_split: [0.4, 0.3, 0.3]\n  sl_policy: p2_step_stop\n"
        "symbols:\n"
        "  SOL-USDT:\n"
        "    enabled: true\n    margin_usdt: 100\n    leverage: 10\n"
        "    margin_mode: isolated\n    sl_policy: p2_step_stop\n"
    )
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "k")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "s")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "p")
    monkeypatch.setenv("BRIDGE_SECRET", "topsecret" * 3)
    monkeypatch.setenv("BLOFIN_ENV", "demo")
    monkeypatch.setenv("BLOFIN_BRIDGE_CONFIG", str(yaml_path))
    monkeypatch.setenv("BLOFIN_BRIDGE_DB", str(tmp_path / "bridge.db"))

    from blofin_bridge import main as main_mod
    # Replace BloFin client builder with a mock
    mock_blofin = MagicMock()
    mock_blofin.get_instrument.return_value = {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }
    mock_blofin.fetch_last_price.return_value = 80.0
    mock_blofin.place_market_entry.return_value = {
        "orderId": "ord-1", "fill_price": 80.12, "filled": 12,
    }
    monkeypatch.setattr(main_mod, "_build_blofin_client", lambda _: mock_blofin)
    return main_mod.create_app()


def test_webhook_rejects_wrong_secret(app):
    client = TestClient(app)
    r = client.post("/webhook/pro-v3", json={
        "secret": "wrong", "symbol": "SOL-USDT", "action": "buy",
    })
    assert r.status_code == 401


def test_webhook_buy_opens_position(app):
    client = TestClient(app)
    r = client.post("/webhook/pro-v3", json={
        "secret": "topsecret" * 3, "symbol": "SOL-USDT", "action": "buy",
        "source": "pro_v3",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["opened"] is True


def test_webhook_unknown_action_400(app):
    client = TestClient(app)
    r = client.post("/webhook/pro-v3", json={
        "secret": "topsecret" * 3, "symbol": "SOL-USDT", "action": "potato",
    })
    assert r.status_code == 400


def test_health_endpoint(app):
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_webhook_e2e.py -v
```

- [ ] **Step 3: Implement `main.py`**

```python
"""FastAPI app: webhook entry point, health, status."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .blofin_client import BloFinClient, build_ccxt_client
from .config import Settings, load_config
from .notify import Notifier
from .router import dispatch, UnknownAction
from .state import Store

log = logging.getLogger(__name__)


class WebhookPayload(BaseModel):
    secret: str
    symbol: str
    action: Literal[
        "buy", "sell", "tp1", "tp2", "tp3", "sl",
        "reversal_buy", "reversal_sell",
    ]
    source: str = Field(default="pro_v3")


def _build_blofin_client(settings: Settings) -> BloFinClient:
    """Factory kept as a module-level function so tests can monkeypatch it."""
    ccxt_client = build_ccxt_client(
        api_key=settings.blofin.api_key,
        secret=settings.blofin.api_secret,
        passphrase=settings.blofin.passphrase,
        env=settings.blofin.env,
    )
    client = BloFinClient(ccxt_client=ccxt_client)
    client.load_instruments()
    return client


def create_app() -> FastAPI:
    config_path = Path(
        os.environ.get("BLOFIN_BRIDGE_CONFIG")
        or (Path(__file__).resolve().parents[3] / "config" / "blofin_bridge.yaml")
    )
    db_path = Path(
        os.environ.get("BLOFIN_BRIDGE_DB")
        or (Path(__file__).resolve().parents[3] / "data" / "bridge.db")
    )
    settings = load_config(config_path)
    store = Store(db_path)
    blofin = _build_blofin_client(settings)
    notifier = Notifier(
        bot_token=settings.bridge.telegram_bot_token,
        chat_id=settings.bridge.telegram_chat_id,
    )

    app = FastAPI(title="BloFin × TradingView Bridge", version="0.1.0")

    symbol_configs = {
        name: {
            **sc.model_dump(),
            "safety_sl_pct": settings.defaults.safety_sl_pct,
            "tp_split": settings.defaults.tp_split,
        }
        for name, sc in settings.symbols.items()
    }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "env": settings.blofin.env,
            "enabled_symbols": [
                s for s, c in settings.symbols.items() if c.enabled
            ],
            "open_positions": len(store.list_open_positions()),
        }

    @app.post("/webhook/pro-v3")
    async def pro_v3(request: Request) -> dict[str, Any]:
        raw = await request.body()
        try:
            payload = WebhookPayload(**json.loads(raw or b"{}"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"bad payload: {exc}")

        if payload.secret != settings.bridge.shared_secret:
            raise HTTPException(status_code=401, detail="invalid secret")

        event_id = store.append_event(
            position_id=None, event_type=payload.action,
            payload=raw.decode("utf-8"),
        )

        try:
            result = dispatch(
                action=payload.action, symbol=payload.symbol,
                store=store, blofin=blofin, symbol_configs=symbol_configs,
            )
            store.mark_event_handled(event_id, outcome="ok", error_msg=None)
            notifier.send(f"{payload.action.upper()} {payload.symbol}: {result}")
            return {"result": result}
        except UnknownAction as exc:
            store.mark_event_handled(event_id, outcome="error",
                                     error_msg=f"unknown action {exc}")
            raise HTTPException(status_code=400, detail=f"unknown action {exc}")
        except Exception as exc:
            log.exception("handler failed")
            store.mark_event_handled(event_id, outcome="error",
                                     error_msg=str(exc))
            notifier.send(f"ERROR: {payload.action} {payload.symbol}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    return app


app = create_app() if os.environ.get("BLOFIN_BRIDGE_EAGER") else None
```

Note: the `app = create_app() if ...` guard at the bottom keeps test-time imports from constructing the real app. In deployment (uvicorn), we call `create_app` via a simple runner instead.

- [ ] **Step 4: Add a runner entrypoint**

Append to `main.py`:

```python
def run() -> None:
    import uvicorn
    uvicorn.run(
        create_app(),
        host=os.environ.get("BLOFIN_BRIDGE_HOST", "0.0.0.0"),
        port=int(os.environ.get("BLOFIN_BRIDGE_PORT", "8787")),
    )


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
pytest tests/test_webhook_e2e.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/main.py scripts/blofin-bridge/tests/test_webhook_e2e.py
git commit -m "blofin-bridge: FastAPI app, /webhook/pro-v3, /health"
```

---

## Task 17: Status endpoint (auth-gated)

**Files:**
- Modify: `scripts/blofin-bridge/src/blofin_bridge/main.py`
- Modify: `scripts/blofin-bridge/tests/test_webhook_e2e.py`

**Context:** `/status` gives you a snapshot of open positions and last events, protected by the same shared secret via query string.

- [ ] **Step 1: Append failing test**

```python
def test_status_rejects_no_secret(app):
    client = TestClient(app)
    r = client.get("/status")
    assert r.status_code == 401


def test_status_returns_state(app):
    client = TestClient(app)
    r = client.get("/status", params={"secret": "topsecret" * 3})
    assert r.status_code == 200
    body = r.json()
    assert "open_positions" in body
    assert "recent_events" in body
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_webhook_e2e.py::test_status_returns_state -v
```

- [ ] **Step 3: Add `/status` route inside `create_app`**

Add this route inside `create_app` next to `/health`:

```python
    @app.get("/status")
    def status(secret: str = "") -> dict[str, Any]:
        if secret != settings.bridge.shared_secret:
            raise HTTPException(status_code=401, detail="invalid secret")
        return {
            "open_positions": [
                {
                    "id": p.id, "symbol": p.symbol, "side": p.side,
                    "entry_price": p.entry_price,
                    "current_size": p.current_size,
                    "tp_stage": p.tp_stage,
                    "sl_order_id": p.sl_order_id,
                }
                for p in store.list_open_positions()
            ],
            "recent_events": store.recent_events(limit=20),
        }
```

- [ ] **Step 4: Run, verify passing**

```bash
pytest tests/test_webhook_e2e.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/main.py scripts/blofin-bridge/tests/test_webhook_e2e.py
git commit -m "blofin-bridge: add /status endpoint"
```

---

## Task 18: Startup reconciliation

**Files:**
- Create: `scripts/blofin-bridge/src/blofin_bridge/reconcile.py`
- Modify: `scripts/blofin-bridge/src/blofin_bridge/main.py`
- Create: `scripts/blofin-bridge/tests/test_reconcile.py`

**Context:** Before accepting webhooks on startup, compare SQLite's open positions against `fetch_positions()` from BloFin. Any mismatch (BloFin has one we don't, or vice versa) logs + Telegram-alerts and marks the symbol as frozen. Catches crash-in-middle-of-trade scenarios.

- [ ] **Step 1: Write failing tests**

`tests/test_reconcile.py`:

```python
from unittest.mock import MagicMock

import pytest

from blofin_bridge.reconcile import reconcile, ReconcileReport
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "rec.db")


def test_clean_state_returns_no_drift(store):
    blofin = MagicMock()
    blofin.fetch_positions.return_value = []
    rep = reconcile(store=store, blofin=blofin)
    assert rep.frozen_symbols == []
    assert rep.drift_count == 0


def test_sqlite_has_position_blofin_doesnt(store):
    store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    blofin = MagicMock()
    blofin.fetch_positions.return_value = []    # BloFin is flat
    rep = reconcile(store=store, blofin=blofin)
    assert "SOL-USDT" in rep.frozen_symbols
    assert rep.drift_count == 1


def test_blofin_has_position_sqlite_doesnt(store):
    blofin = MagicMock()
    blofin.fetch_positions.return_value = [{
        "symbol": "SOL/USDT:USDT",
        "info": {"instId": "SOL-USDT"},
        "contracts": 12,
        "side": "long",
    }]
    rep = reconcile(store=store, blofin=blofin)
    assert "SOL-USDT" in rep.frozen_symbols
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/test_reconcile.py -v
```

- [ ] **Step 3: Implement `reconcile.py`**

```python
"""Startup reconciliation between SQLite and BloFin."""
from __future__ import annotations
from dataclasses import dataclass, field

from .blofin_client import BloFinClient
from .state import Store


@dataclass
class ReconcileReport:
    frozen_symbols: list[str] = field(default_factory=list)
    drift_count: int = 0
    details: list[str] = field(default_factory=list)


def _extract_inst_id(pos: dict) -> str:
    info = pos.get("info") or {}
    return info.get("instId") or pos.get("symbol", "").replace("/", "-").split(":")[0]


def reconcile(*, store: Store, blofin: BloFinClient) -> ReconcileReport:
    report = ReconcileReport()

    sqlite_open = {p.symbol: p for p in store.list_open_positions()}
    blofin_raw = blofin.fetch_positions()
    blofin_open = {
        _extract_inst_id(p): p
        for p in blofin_raw
        if float(p.get("contracts") or 0) != 0
    }

    # SQLite says open, BloFin says flat -> drift
    for sym in sqlite_open:
        if sym not in blofin_open:
            report.frozen_symbols.append(sym)
            report.drift_count += 1
            report.details.append(
                f"{sym}: SQLite has open position, BloFin flat"
            )

    # BloFin says open, SQLite says flat -> drift
    for sym in blofin_open:
        if sym not in sqlite_open:
            report.frozen_symbols.append(sym)
            report.drift_count += 1
            report.details.append(
                f"{sym}: BloFin has open position, SQLite flat"
            )

    return report
```

- [ ] **Step 4: Wire reconciliation into `main.create_app()`**

Add inside `create_app()`, after `blofin = _build_blofin_client(settings)`:

```python
    from .reconcile import reconcile
    rec_report = reconcile(store=store, blofin=blofin)
    frozen: set[str] = set(rec_report.frozen_symbols)
    if rec_report.drift_count > 0:
        notifier.send(
            "RECONCILE DRIFT on startup: "
            + "; ".join(rec_report.details)
            + " — frozen: " + ", ".join(rec_report.frozen_symbols)
        )
```

Then, inside the webhook handler, reject trades for frozen symbols with 423:

Modify the webhook handler body, right after the secret check:

```python
        if payload.symbol in frozen:
            store.mark_event_handled(event_id, outcome="skipped",
                                     error_msg="symbol frozen after reconcile drift")
            raise HTTPException(status_code=423, detail="symbol frozen")
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_reconcile.py tests/test_webhook_e2e.py -v
```

Expected: all pass. If any webhook test fails because of the new freeze logic, add `monkeypatch.setattr(main_mod, "reconcile", lambda **_: ReconcileReport())` — but prefer leaving the reconcile path untouched so it runs on every test and confirms "clean" works end-to-end.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/src/blofin_bridge/reconcile.py scripts/blofin-bridge/src/blofin_bridge/main.py scripts/blofin-bridge/tests/test_reconcile.py
git commit -m "blofin-bridge: add startup reconciliation + frozen-symbol gate"
```

---

## Task 19: Dockerfile + docker-compose

**Files:**
- Create: `scripts/blofin-bridge/Dockerfile`
- Create: `scripts/blofin-bridge/docker-compose.yml`
- Create: `scripts/blofin-bridge/.dockerignore`

**Context:** Small Python 3.11-slim image, non-root user, reads `.env` at runtime. Compose exposes port 8787 (local) and will be fronted by Traefik on the VPS (Task 20).

- [ ] **Step 1: Create `.dockerignore`**

```
venv/
__pycache__/
*.pyc
.pytest_cache/
data/
*.db
.env
```

- [ ] **Step 2: Create `Dockerfile`**

```dockerfile
FROM python:3.11-slim

RUN useradd --create-home --shell /bin/bash bridge
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY src ./src
COPY config ./config

RUN mkdir -p /app/data && chown -R bridge:bridge /app
USER bridge

ENV PYTHONUNBUFFERED=1
ENV BLOFIN_BRIDGE_CONFIG=/app/config/blofin_bridge.yaml
ENV BLOFIN_BRIDGE_DB=/app/data/bridge.db

EXPOSE 8787

CMD ["python", "-m", "blofin_bridge.main"]
```

- [ ] **Step 3: Create `docker-compose.yml`**

```yaml
services:
  blofin-bridge:
    build: .
    container_name: blofin-bridge
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./config:/app/config:ro
    ports:
      - "127.0.0.1:8787:8787"
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8787/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
```

- [ ] **Step 4: Build and smoke test locally**

```bash
cd scripts/blofin-bridge
docker compose build
docker compose up -d
sleep 3
curl -s http://localhost:8787/health
docker compose down
```

Expected: a JSON response with `"status": "ok"`. If BloFin connection fails during startup, that's OK for this step — the test just proves the container starts and `/health` responds.

Note: if the build fails on `pip install -e .` because setuptools can't find packages, ensure `pyproject.toml` has `[tool.setuptools.packages.find] where = ["src"]` (Task 1 step 1 already sets this).

- [ ] **Step 5: Commit**

```bash
git add scripts/blofin-bridge/Dockerfile scripts/blofin-bridge/docker-compose.yml scripts/blofin-bridge/.dockerignore
git commit -m "blofin-bridge: add Dockerfile + compose + dockerignore"
```

---

## Task 20: Deploy to Hostinger VPS (behind Traefik)

**Files:**
- Modify: `scripts/blofin-bridge/docker-compose.yml` (add Traefik labels)
- Create: manual docs in `scripts/blofin-bridge/DEPLOY.md`

**Context:** The VPS already runs Traefik (per `reference_vps_layout.md`). We add the container alongside `/docker/openclaw-wmo9/` at `/docker/blofin-bridge/`, with Traefik labels for TLS.

- [ ] **Step 1: Add Traefik labels to compose**

Replace `docker-compose.yml` with:

```yaml
services:
  blofin-bridge:
    build: .
    container_name: blofin-bridge
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./config:/app/config:ro
    networks:
      - web
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.blofin-bridge.rule=Host(`blofin-bridge.srv1370094.hstgr.cloud`)"
      - "traefik.http.routers.blofin-bridge.entrypoints=websecure"
      - "traefik.http.routers.blofin-bridge.tls.certresolver=letsencrypt"
      - "traefik.http.services.blofin-bridge.loadbalancer.server.port=8787"
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8787/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3

networks:
  web:
    external: true
```

Note: the `web` network name must match the existing Traefik network on the VPS. If the Traefik compose there uses a different name (e.g. `traefik`, `proxy`), update both the service's `networks` list and the `networks:` block to match.

- [ ] **Step 2: Create `DEPLOY.md` with VPS steps**

```markdown
# Deploying blofin-bridge to the Hostinger VPS

## Prereqs
- VPS: `46.202.146.30`, Ubuntu 24.04, Traefik already running under `/docker/traefik/`
- SSH access via `ssh root@46.202.146.30`
- Real `.env` file populated locally

## One-time setup

```bash
# On your laptop
cd C:/Users/rakai/Leverage/scripts/blofin-bridge
scp -r . root@46.202.146.30:/docker/blofin-bridge/

# On the VPS
ssh root@46.202.146.30
cd /docker/blofin-bridge
docker compose build
docker compose up -d
docker logs -f blofin-bridge
```

## Traefik network

If `docker compose up` errors with "network web not found", find the existing Traefik network and update compose:

```bash
docker network ls | grep -i -E 'traefik|web|proxy'
```

Update the `networks:` block in `docker-compose.yml` to match.

## Updates

After code changes:

```bash
cd C:/Users/rakai/Leverage/scripts/blofin-bridge
scp -r src config root@46.202.146.30:/docker/blofin-bridge/
ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose up -d --build"
```

## Rollback

```bash
ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose down"
# Restore previous version from git + scp again
```

## Verify

```bash
curl https://blofin-bridge.srv1370094.hstgr.cloud/health
```

Should return `{"status": "ok", "env": "demo", ...}`.
```

- [ ] **Step 3: Transfer `.env` securely (one-time)**

From your laptop (NOT committed):

```bash
scp C:/Users/rakai/Leverage/scripts/blofin-bridge/.env root@46.202.146.30:/docker/blofin-bridge/.env
```

The `.env` is gitignored so it's not in the git repo — this `scp` is the only way it reaches the VPS.

- [ ] **Step 4: Transfer the rest and start**

```bash
# Everything except .env (which is already there)
ssh root@46.202.146.30 "mkdir -p /docker/blofin-bridge"
cd C:/Users/rakai/Leverage/scripts/blofin-bridge
scp -r Dockerfile docker-compose.yml pyproject.toml README.md src config DEPLOY.md root@46.202.146.30:/docker/blofin-bridge/
ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose up -d --build && docker logs --tail 100 blofin-bridge"
```

Expected: container comes up, `/health` returns OK, logs show `reconcile` report with `drift_count=0`.

- [ ] **Step 5: Verify HTTPS endpoint**

```bash
curl https://blofin-bridge.srv1370094.hstgr.cloud/health
```

Expected: `{"status":"ok","env":"demo",...}`. If TLS cert isn't issued yet, wait 60 seconds for Traefik + Let's Encrypt.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/docker-compose.yml scripts/blofin-bridge/DEPLOY.md
git commit -m "blofin-bridge: add Traefik labels + DEPLOY.md"
```

---

## Task 21: Demo-env end-to-end smoke via curl

**Files:**
- Create: `scripts/blofin-bridge/smoke/test_smoke.sh`

**Context:** Before pointing TradingView at the live URL, fire each action via curl against the demo environment and watch the demo-trading BloFin UI. This catches signing issues, symbol-format issues, and handler logic issues in one shot.

- [ ] **Step 1: Confirm the bridge is running on demo env**

On the VPS:

```bash
ssh root@46.202.146.30 "cat /docker/blofin-bridge/.env | grep BLOFIN_ENV"
```

Should show `BLOFIN_ENV=demo`.

- [ ] **Step 2: Create demo BloFin account funds**

In the BloFin web UI, switch to Demo Trading mode and "Reset Demo Assets" to load ~10,000 USDT of fake margin.

- [ ] **Step 3: Create smoke script**

```bash
#!/usr/bin/env bash
# scripts/blofin-bridge/smoke/test_smoke.sh
#
# Fires each webhook action against the deployed bridge. Requires two env vars:
#   BRIDGE_URL (e.g. https://blofin-bridge.srv1370094.hstgr.cloud)
#   BRIDGE_SECRET
#
# Usage:
#   BRIDGE_URL=https://blofin-bridge.srv1370094.hstgr.cloud \
#   BRIDGE_SECRET=xxx \
#   ./smoke/test_smoke.sh

set -euo pipefail
: "${BRIDGE_URL:?must be set}"
: "${BRIDGE_SECRET:?must be set}"

fire() {
  local action=$1
  echo "==> $action"
  curl -sS -X POST "$BRIDGE_URL/webhook/pro-v3" \
    -H 'Content-Type: application/json' \
    -d "{\"secret\":\"$BRIDGE_SECRET\",\"symbol\":\"SOL-USDT\",\"action\":\"$action\",\"source\":\"smoke\"}"
  echo
  sleep 2
}

echo "HEALTH CHECK"
curl -sS "$BRIDGE_URL/health"
echo

echo
echo "== LONG LIFECYCLE: buy -> tp1 -> tp2 -> tp3 =="
fire buy
fire tp1
fire tp2
fire tp3

echo
echo "== SHORT LIFECYCLE: sell -> sl =="
fire sell
fire sl

echo
echo "== REVERSAL: buy -> reversal_sell =="
fire buy
fire reversal_sell
fire sl

echo
echo "STATUS:"
curl -sS "$BRIDGE_URL/status?secret=$BRIDGE_SECRET" | head -c 2000
echo
```

- [ ] **Step 4: Run the smoke script**

From your laptop:

```bash
cd C:/Users/rakai/Leverage/scripts/blofin-bridge
chmod +x smoke/test_smoke.sh
BRIDGE_URL=https://blofin-bridge.srv1370094.hstgr.cloud \
BRIDGE_SECRET=<paste your BRIDGE_SECRET> \
./smoke/test_smoke.sh
```

Expected output: each call returns `{"result": {...}}` with `opened: true` / `handled: true`. On the BloFin demo UI, you should see positions open/close in sequence.

- [ ] **Step 5: Inspect `/status`**

The final `status` section should show zero open positions (everything closed after sl/tp3). If positions are stuck, inspect `docker logs blofin-bridge` and fix before proceeding.

- [ ] **Step 6: Commit**

```bash
git add scripts/blofin-bridge/smoke/test_smoke.sh
git commit -m "blofin-bridge: add demo-env smoke test script"
```

---

## Task 22: TradingView alert setup (manual, user-driven)

**Files:**
- Create: `scripts/blofin-bridge/docs/TV_ALERTS.md`

**Context:** TradingView alerts are user-configured via the web UI; no code runs here. This task documents the exact strings to paste so Rich can set them up in under 10 minutes.

- [ ] **Step 1: Write `docs/TV_ALERTS.md`**

```markdown
# TradingView alert setup for Pro V3 -> BloFin bridge

You need 8 alerts per symbol. For v1 that means 8 alerts on `BLOFIN:SOLUSDT.P`.

## Prereqs
- Chart: `BLOFIN:SOLUSDT.P`
- Indicator `Pro V3 [SMRT Algo]` applied
- TradingView plan with webhook alerts (Essential or higher)

## Webhook URL (all alerts)
```
https://blofin-bridge.srv1370094.hstgr.cloud/webhook/pro-v3
```

## For each alert

1. Right-click chart -> "Add alert"
2. **Condition:** `Pro V3 [SMRT Algo]` → pick the trigger from the dropdown (see table below)
3. **Options:** `Once Per Bar Close`
4. **Expiration:** "Open-ended"
5. **Notifications tab:**
   - Check "Webhook URL"
   - Paste the URL above
   - Paste the message body from the table
6. Click "Create"

## Alerts to create (8 total)

| Pro V3 dropdown | Message body (copy verbatim, one JSON line) |
|---|---|
| Buy | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"buy","source":"pro_v3"}` |
| Sell | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"sell","source":"pro_v3"}` |
| TP1 | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"tp1","source":"pro_v3"}` |
| TP2 | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"tp2","source":"pro_v3"}` |
| TP3 | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"tp3","source":"pro_v3"}` |
| SL | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"sl","source":"pro_v3"}` |
| Reversal Buy | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"reversal_buy","source":"pro_v3"}` |
| Reversal Sell | `{"secret":"<BRIDGE_SECRET>","symbol":"SOL-USDT","action":"reversal_sell","source":"pro_v3"}` |

Replace `<BRIDGE_SECRET>` with the value from your local `.env`.

## After creation

- Tail the bridge logs: `ssh root@46.202.146.30 "docker logs -f blofin-bridge"`
- Wait for Pro V3 to fire a Buy or Sell alert in real market conditions
- Watch: webhook arrives, bridge logs `opened: true`, position appears on BloFin demo UI
```

- [ ] **Step 2: Commit**

```bash
git add scripts/blofin-bridge/docs/TV_ALERTS.md
git commit -m "blofin-bridge: document TradingView alert setup"
```

- [ ] **Step 3: Push everything**

```bash
cd C:/Users/rakai/Leverage && git push
```

---

## Acceptance criteria

The plan is complete when all of these are true:

- [ ] `pytest` passes green for every test file in `scripts/blofin-bridge/tests/`
- [ ] `docker compose up` on the VPS starts the container and `/health` returns `status=ok, env=demo`
- [ ] `smoke/test_smoke.sh` runs full long lifecycle, short lifecycle, and reversal cleanly against BloFin demo
- [ ] 8 TradingView alerts for SOLUSDT.P are configured and one real Pro V3 Buy has been observed reaching the bridge, opening a demo position, hitting at least TP1, and landing in `/status`
- [ ] `docs/superpowers/plans/2026-04-07-blofin-tv-webhook-bridge.md` all task checkboxes are checked

Live graduation (after acceptance):
1. Flip `BLOFIN_ENV=demo` → `live` on the VPS `.env`
2. Override `margin_usdt: 10` in `config/blofin_bridge.yaml` on the VPS
3. Restart: `ssh root@46.202.146.30 "cd /docker/blofin-bridge && docker compose up -d"`
4. Wait for one full live cycle (Buy → TP1 → TP2 → TP3 or SL). Verify PnL on BloFin live account.
5. After two clean live cycles, raise `margin_usdt` back to 100 and push the config.
