# Apex Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up "Apex" — Rich's personal V3 — as a standalone, fully isolated copy of the Reclaim Lighter bridge, reverted to plain V3.1 entry (SMRT Pro V3 webhook → 9 EMA retest), with a simplified 3-stage exit ladder, a 3-loss/60-min cooldown breaker, and Telegram pause/stop control.

**Architecture:** Reclaim (`scripts/reclaim-bridge/`) is V3.1's pipeline already running on Lighter — it has the Pro V3 webhook listener, the EMA9-retest entry gate, the trail state machine, sizing, db, notify, and Telegram control, all selected by config. Apex copies that tree into `scripts/apex/`, renames the Python package `lighter_bridge` → `apex_bridge` for hard isolation, and changes behavior through (a) a new `config.apex.yaml`, (b) a 3-state rewrite of `state_machine.py`, and (c) one wiring fix so trail-mode closes feed the cooldown breaker. No other strategy code is touched.

**Tech Stack:** Python 3.11+, asyncio, FastAPI/uvicorn (webhook), `lighter` SDK (PaperClient), pandas, SQLite, pytest, Docker Compose, Traefik.

## Global Constraints

- **Isolation (hard rule):** `scripts/apex/` imports nothing from `scripts/scalping/`, `scripts/reclaim-bridge/`, or any other bridge. The Lighter execution layer is copied, never imported. Package name is `apex_bridge` (NOT `lighter_bridge`). Own config, DB (`apex.db`), container (`apex-bridge`), bot (`@apexbot`), dashboard dir.
- **Venue:** Lighter mainnet paper (`PaperClient`), zero-fee. Never BloFin.
- **Coins:** HYPE (`market_id: 24`), SOL (`market_id: 2`), ZEC (`market_id: 90`) — ids verified live in the Reclaim config 2026-06-20.
- **Sizing:** fixed $250 margin × 30 leverage = $7,500 notional per coin; account $3,000.
- **Entry:** SMRT Pro V3 webhook → 9 EMA (5m) retest. `require_retest: true`, `require_reclaim: false`, `max_gap_pct: 0` (plain V3.1 retest, no reclaim, no gap filter). Slope gate `min_abs_slope_pct: 0.15`. ATR body-band `block_body_band: [0.3, 0.5]` ON. `block_weekdays: []` (Apex trades Sundays).
- **Exit ladder (3 stages):** initial SL −$30; at peak +$20 → SL to breakeven (entry); at peak +$35 → SL jumps to +$20 locked and trails $15 behind each new favorable extreme. Hard TP ceiling at `tp_ceiling_pct: 2.0` (= $500, a near-never safety cap).
- **Cooldown:** 3 consecutive losing closes (basket-wide) → block all entries 60 minutes → auto-resume.
- **Telegram:** `@apexbot`, `control.telegram_enabled: true`. Token via `TELEGRAM_BOT_TOKEN` env only — NEVER in any committed file.
- **Domain / webhook:** `apex.agentneo.cloud`, path `/webhook/apex`, secret via `BRIDGE_SECRET` env.
- **TDD:** write the failing test first for every logic change; mechanical copy/rename steps are verified by running the existing suite.

---

### Task 1: Scaffold Apex by copying Reclaim and renaming the package

**Files:**
- Create (copy of tree): `scripts/apex/` ← `scripts/reclaim-bridge/`
- Rename dir: `scripts/apex/src/lighter_bridge/` → `scripts/apex/src/apex_bridge/`
- Modify: `scripts/apex/run_bridge.py` (import path)
- Modify: `scripts/apex/tests/conftest.py` (import path)
- Delete: `scripts/apex/docker-compose.reclaim.yml`, `scripts/apex/data/` (any copied `reclaim.db`), `scripts/apex/.env` (if copied)
- Keep for now: `scripts/apex/config.reclaim.yaml` — `tests/test_reclaim_gap.py` loads it as a fixture. It is moved into `tests/fixtures/` and removed from the Apex root in Task 3 (where the config work lives), so Task 1 stays green.

**Interfaces:**
- Produces: importable package `apex_bridge` with all of Reclaim's modules under `scripts/apex/src/apex_bridge/`. Intra-package imports are relative (`.config`, `from . import notify`) so they survive the directory rename untouched; only absolute `lighter_bridge` references need fixing.

- [ ] **Step 1: Copy the tree (excluding runtime junk)**

```bash
cd /c/Users/rakai/Leverage
mkdir -p scripts/apex
cp -r scripts/reclaim-bridge/. scripts/apex/
# remove copied runtime + secrets + reclaim-specific deploy files
rm -rf scripts/apex/data scripts/apex/.env scripts/apex/__pycache__
find scripts/apex -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -f scripts/apex/config.reclaim.yaml scripts/apex/docker-compose.reclaim.yml
```

- [ ] **Step 2: Rename the package directory**

```bash
git mv scripts/apex/src/lighter_bridge scripts/apex/src/apex_bridge 2>/dev/null \
  || mv scripts/apex/src/lighter_bridge scripts/apex/src/apex_bridge
```

- [ ] **Step 3: Fix the only two absolute imports of the old package name**

In `scripts/apex/run_bridge.py`, change:
```python
from lighter_bridge.main import main
```
to:
```python
from apex_bridge.main import main
```

In `scripts/apex/tests/conftest.py`, find the `sys.path` insert and any `import lighter_bridge...` / `from lighter_bridge...` lines and replace `lighter_bridge` with `apex_bridge`. (Run the grep in Step 4 to find every occurrence; relative imports inside the package do NOT need changing.)

- [ ] **Step 4: Verify no absolute `lighter_bridge` references remain**

Run:
```bash
grep -rn "lighter_bridge" scripts/apex --include=*.py
```
Expected: NO matches. If any remain (e.g. in tests), change them to `apex_bridge`.

- [ ] **Step 5: Verify the suite collects and passes against the renamed package**

Run:
```bash
cd scripts/apex && python -m pytest -q
```
Expected: tests collect and pass (same set Reclaim shipped). If collection fails with `ModuleNotFoundError: lighter_bridge`, a Step 3/4 reference was missed — fix it.

- [ ] **Step 6: Commit**

```bash
git add scripts/apex
git commit -m "feat(apex): scaffold standalone bridge as apex_bridge (copy of Reclaim)"
```

---

### Task 2: Simplify the exit ladder to 3 stages (state machine + ExitConfig together)

These two changes are atomic: the 3-state machine reads a trimmed `ExitConfig`, and trimming `ExitConfig` would break the old 4-state code. Do them in one task so the suite ends green.

**Files:**
- Modify: `scripts/apex/src/apex_bridge/state_machine.py` (rewrite to 3 states)
- Modify: `scripts/apex/src/apex_bridge/config.py` (trim `ExitConfig` to 5 fields)
- Modify: `scripts/apex/src/apex_bridge/main.py` (startup log line that prints removed fields)
- Create: `scripts/apex/tests/test_state_machine.py`

**Interfaces:**
- Consumes: `OpenPosition` (from `executor.py`) with fields `side` ("long"|"short"), `entry_price`, `sl_price`, `state` (int), `trail_high`, `max_state`, `base_amount`, `notional`, `margin_usdt`, `symbol`.
- Produces: `ExitConfig(sl_loss_usdt, breakeven_usdt, trail_activate_usdt, trail_distance_usdt, tp_ceiling_pct)` — exactly five float fields. The yaml `exits:` block must contain exactly these five keys (the loader does `ExitConfig(**{k: float(v) for k, v in raw["exits"].items()})`, so any extra key raises `TypeError`).
- Produces: `step(pos, mark_price, cfg) -> StateMachineDecision` (signature unchanged, mutates `pos`). States: `0=initial`, `1=breakeven`, `2=trailing`. `initial_sl(pos, cfg) -> float` unchanged signature.

- [ ] **Step 1: Trim the `ExitConfig` dataclass**

In `scripts/apex/src/apex_bridge/config.py`, replace the `ExitConfig` definition:

```python
@dataclass
class ExitConfig:
    sl_loss_usdt: float
    breakeven_usdt: float
    lock_profit_activate_usdt: float
    lock_profit_usdt: float
    trail_activate_usdt: float
    trail_start_usdt: float
    trail_distance_usdt: float
    tp_ceiling_pct: float
```

with:

```python
@dataclass
class ExitConfig:
    sl_loss_usdt: float
    breakeven_usdt: float
    trail_activate_usdt: float
    trail_distance_usdt: float
    tp_ceiling_pct: float
```

- [ ] **Step 2: Write the failing tests**

Create `scripts/apex/tests/test_state_machine.py`:

```python
"""Apex 3-stage exit ladder: SL -$30 -> BE at +$20 -> at +$35 lock +$20 & trail $15."""
from dataclasses import dataclass

import pytest

from apex_bridge.state_machine import step, initial_sl
from apex_bridge.config import ExitConfig


@dataclass
class FakePos:
    side: str
    entry_price: float
    base_amount: float
    notional: float
    margin_usdt: float = 250.0
    symbol: str = "SOL"
    sl_price: float = 0.0
    state: int = 0
    trail_high: float = 0.0
    max_state: int = 0


def cfg():
    # $250 margin x 30x = $7,500 notional
    return ExitConfig(
        sl_loss_usdt=30.0, breakeven_usdt=20.0,
        trail_activate_usdt=35.0, trail_distance_usdt=15.0,
        tp_ceiling_pct=2.0,
    )


def _pos(side="long", entry=100.0):
    # notional 7500 at entry 100 -> base_amount 75; $1 PnL = 0.01333 price move
    return FakePos(side=side, entry_price=entry, base_amount=75.0, notional=7500.0,
                   trail_high=entry)


def _price_for_pnl(pos, usd):
    move = (usd / pos.notional) * pos.entry_price
    return pos.entry_price + move if pos.side == "long" else pos.entry_price - move


def test_initial_sl_is_minus_30():
    pos = _pos()
    px = initial_sl(pos, cfg())
    # -$30 on $7,500 notional at entry 100 = -0.4% = 99.6
    assert px == pytest.approx(99.6, abs=1e-6)


def test_breakeven_at_plus_20():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    mark = _price_for_pnl(pos, 20.0)
    step(pos, mark, c)
    assert pos.state == 1
    assert pos.sl_price == pytest.approx(pos.entry_price, abs=1e-6)


def test_below_35_stays_breakeven():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 25.0), c)
    assert pos.state == 1
    assert pos.sl_price == pytest.approx(pos.entry_price, abs=1e-6)


def test_at_35_locks_plus_20_and_trails():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 35.0), c)
    assert pos.state == 2
    # SL now locks +$20 (= +$35 peak minus $15 trail)
    assert pos.sl_price == pytest.approx(_price_for_pnl(pos, 20.0), abs=1e-4)


def test_trailing_ratchets_15_behind_new_high():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 35.0), c)      # enter trailing
    step(pos, _price_for_pnl(pos, 60.0), c)      # new high +$60
    # SL trails $15 behind the +$60 peak -> +$45
    assert pos.sl_price == pytest.approx(_price_for_pnl(pos, 45.0), abs=1e-4)


def test_trailing_does_not_lower_sl_on_pullback():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 60.0), c)      # jump straight to trailing, SL ~ +$45
    locked = pos.sl_price
    step(pos, _price_for_pnl(pos, 50.0), c)      # pull back to +$50 (no new high)
    assert pos.sl_price == pytest.approx(locked, abs=1e-9)


def test_sl_hit_reason_by_state():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    d = step(pos, _price_for_pnl(pos, -30.0), c)   # straight to initial SL
    assert d.close and d.reason == "sl"


def test_short_symmetry_at_35():
    pos, c = _pos(side="short"), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 35.0), c)
    assert pos.state == 2
    assert pos.sl_price == pytest.approx(_price_for_pnl(pos, 20.0), abs=1e-4)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```bash
cd scripts/apex && python -m pytest tests/test_state_machine.py -q
```
Expected: FAIL — the copied `step()` still uses the 4-state ladder and reads the now-removed `lock_profit_*` / `trail_start_*` fields, so it raises `AttributeError` on the trimmed `ExitConfig`.

- [ ] **Step 4: Rewrite `state_machine.py` to 3 states**

Replace the body of `scripts/apex/src/apex_bridge/state_machine.py` with:

```python
"""Apex 3-stage live trail-SL state machine.

Per open position, evaluated each tick:
  0 = initial  -> SL at entry -/+ (sl_loss_usdt / notional) * entry
  1 = BE       -> SL = entry              (after peak PnL >= breakeven_usdt)
  2 = trailing -> SL jumps to entry +/- ((trail_activate - trail_distance)/notional)*entry,
                  then trails the favorable extreme by trail_distance_usdt
                  (after peak PnL >= trail_activate_usdt)

At trail_activate $35 with trail_distance $15 the jump locks +$20, and the trail
keeps the SL $15 behind each new favorable extreme — never lowering it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import ExitConfig
from .executor import OpenPosition

log = logging.getLogger(__name__)


@dataclass
class StateMachineDecision:
    close: bool = False
    reason: str = ""


def _dollars_to_price_distance(usd: float, notional: float, ref_price: float) -> float:
    """Convert a $ amount of PnL into a price distance from ref_price."""
    if notional <= 0:
        return 0.0
    return (usd / notional) * ref_price


def _pnl_at(side: str, entry: float, price: float, base_amount: float) -> float:
    if side == "long":
        return (price - entry) * base_amount
    return (entry - price) * base_amount


def initial_sl(pos: OpenPosition, cfg: ExitConfig) -> float:
    """Compute the initial SL price at position open time."""
    sl_dist = _dollars_to_price_distance(cfg.sl_loss_usdt, pos.notional, pos.entry_price)
    return pos.entry_price - sl_dist if pos.side == "long" else pos.entry_price + sl_dist


def step(pos: OpenPosition, mark_price: float, cfg: ExitConfig) -> StateMachineDecision:
    """Evaluate one tick. Mutates `pos` (sl_price, state, trail_high) and returns
    a decision (whether to close, with reason)."""
    if pos.sl_price == 0.0:
        pos.sl_price = initial_sl(pos, cfg)
        log.info("%s: initial SL set @ $%.4f (entry=$%.4f, side=%s)",
                 pos.symbol, pos.sl_price, pos.entry_price, pos.side)

    # 1) Hard TP ceiling (favorable side) — a near-never safety cap.
    ceiling_pnl = pos.margin_usdt * cfg.tp_ceiling_pct
    pnl = _pnl_at(pos.side, pos.entry_price, mark_price, pos.base_amount)
    if pnl >= ceiling_pnl:
        return StateMachineDecision(close=True, reason="tp_ceiling")

    # 2) Update favorable extreme.
    better = (pos.side == "long" and mark_price > pos.trail_high) or \
             (pos.side == "short" and mark_price < pos.trail_high)
    if better:
        pos.trail_high = mark_price

    peak_pnl = _pnl_at(pos.side, pos.entry_price, pos.trail_high, pos.base_amount)

    # 3) State advancement (sequential, so a single large tick can cascade 0->1->2).
    if pos.state == 0 and peak_pnl >= cfg.breakeven_usdt:
        pos.sl_price = pos.entry_price
        pos.state = 1
        log.info("%s: state 0->1 (BE). SL=$%.4f", pos.symbol, pos.sl_price)
    if pos.state == 1 and peak_pnl >= cfg.trail_activate_usdt:
        jl = cfg.trail_activate_usdt - cfg.trail_distance_usdt   # locked profit at activation
        jd = _dollars_to_price_distance(jl, pos.notional, pos.entry_price)
        pos.sl_price = pos.entry_price + jd if pos.side == "long" else pos.entry_price - jd
        pos.state = 2
        log.info("%s: state 1->2 (TRAILING, locked $%.0f). SL=$%.4f",
                 pos.symbol, jl, pos.sl_price)

    # 4) If trailing, ratchet SL toward the favorable extreme (never lower it).
    if pos.state == 2:
        td = _dollars_to_price_distance(cfg.trail_distance_usdt, pos.notional, pos.trail_high)
        new_sl = pos.trail_high - td if pos.side == "long" else pos.trail_high + td
        if pos.side == "long":
            pos.sl_price = max(pos.sl_price, new_sl)
        else:
            pos.sl_price = min(pos.sl_price, new_sl)

    pos.max_state = max(pos.max_state, pos.state)

    # 5) SL hit?
    sl_hit = (pos.side == "long" and mark_price <= pos.sl_price) or \
             (pos.side == "short" and mark_price >= pos.sl_price)
    if sl_hit:
        if pos.state >= 2:
            reason = "trail_sl"
        elif pos.state == 1:
            reason = "sl_be"
        else:
            reason = "sl"
        return StateMachineDecision(close=True, reason=reason)

    return StateMachineDecision(close=False)
```

- [ ] **Step 5: Fix the startup log that prints removed fields**

In `scripts/apex/src/apex_bridge/main.py`, find the trail-exit startup log (the `else:` branch around the bridge banner that prints `lock_act=$%.0f`):

```python
            log.info("Exits: SL=$%.0f BE=$%.0f lock_act=$%.0f trail_act=$%.0f trail_dist=$%.0f",
                     self.cfg.exits.sl_loss_usdt, self.cfg.exits.breakeven_usdt,
                     self.cfg.exits.lock_profit_activate_usdt,
                     self.cfg.exits.trail_activate_usdt, self.cfg.exits.trail_distance_usdt)
```

replace with:

```python
            log.info("Exits: SL=$%.0f BE=$%.0f trail_act=$%.0f trail_dist=$%.0f tp_ceiling=%.1fx",
                     self.cfg.exits.sl_loss_usdt, self.cfg.exits.breakeven_usdt,
                     self.cfg.exits.trail_activate_usdt, self.cfg.exits.trail_distance_usdt,
                     self.cfg.exits.tp_ceiling_pct)
```

- [ ] **Step 6: Confirm no other references to the removed fields**

Run:
```bash
grep -rn "lock_profit\|trail_start_usdt" scripts/apex/src
```
Expected: NO matches. If any remain, remove/replace them (they belong to the old 4-state ladder, now deleted).

- [ ] **Step 7: Run the state-machine tests, then the full suite**

Run:
```bash
cd scripts/apex && python -m pytest tests/test_state_machine.py -q && python -m pytest -q
```
Expected: state-machine tests PASS (8), then the whole suite PASS. Reclaim's `test_reclaim_gap.py` exercises the reclaim ENTRY path (still present, just unused by Apex's config) and should still pass.

- [ ] **Step 8: Commit**

```bash
git add scripts/apex/src/apex_bridge/state_machine.py scripts/apex/src/apex_bridge/config.py scripts/apex/src/apex_bridge/main.py scripts/apex/tests/test_state_machine.py
git commit -m "feat(apex): collapse exit ladder to 3 stages (SL -30 -> BE +20 -> trail @+35 lock +20, $15)"
```

---

### Task 3: Write `config.apex.yaml` and its load test

**Files:**
- Create: `scripts/apex/config.apex.yaml`
- Create: `scripts/apex/tests/test_config_exit.py`
- Move: `scripts/apex/config.reclaim.yaml` → `scripts/apex/tests/fixtures/reclaim_gap_config.yaml`
- Modify: `scripts/apex/tests/test_reclaim_gap.py` (repoint the fixture path)

**Interfaces:**
- Consumes: the trimmed `ExitConfig` and `load_config` schema from Task 2. Produces the live Apex config consumed by `run_bridge.py --config config.apex.yaml`.

This task also finishes the isolation cleanup deferred from Task 1: the inherited
`config.reclaim.yaml` (a different strategy's deployable config sitting at the Apex
root) becomes a pure test fixture so the Apex root holds only `config.apex.yaml`.

- [ ] **Step 1: Write the config file**

Create `scripts/apex/config.apex.yaml`:

```yaml
# Apex — Rich's personal V3 on Lighter (zero-fee). PAPER.
# SMRT Pro V3 TV alert -> 9 EMA (5m) retest -> 3-stage trail exit.
# Standalone: own package (apex_bridge), DB (apex.db), container, bot (@apexbot).
#
# Deploy:  docker compose -f docker-compose.apex.yml up -d --build

connection:
  host: "https://mainnet.zklighter.elliot.ai"
  initial_collateral_usdc: 3000

signal_source: webhook        # SMRT Pro V3 alert -> /webhook/apex
exit_model: trail             # 3-stage state_machine.step

# HYPE / SOL / ZEC — market_ids verified live 2026-06-20. Fixed $250 @ 30x = $7,500 notional.
symbols:
  SOL:  { market_id: 2,  enabled: true, margin_usdt: 250, leverage: 30 }
  HYPE: { market_id: 24, enabled: true, margin_usdt: 250, leverage: 30 }
  ZEC:  { market_id: 90, enabled: true, margin_usdt: 250, leverage: 30 }

pine:                         # SMRT Pro V3 / HA-V3 params (used to enrich bars; entry comes from webhook)
  sensitivity: 8
  noise: 0.0
  fakeout: 0.2
  range_filter: 0.2

entry:
  timeframe: "5m"
  ema_period: 9
  slope_lookback_bars: 3
  retest_overshoot_pct: 0.2   # wick may break EMA9 by <=0.2% and still count as a retest
  retest_timeout_bars: 6      # pending dies if no retest within 6 bars
  require_retest: true        # V3.1 plain retest
  require_reclaim: false      # NOT the V3.2 reclaim close-back
  max_gap_pct: 0.0            # no gap filter
  min_abs_slope_pct: 0.15     # slope gate (the proven V3.1 value)
  block_body_band: [0.3, 0.5] # ATR body-band chop filter ON
  block_weekdays: []          # Apex trades Sundays

exits:                        # 3-stage ladder ($250 margin @ 30x)
  sl_loss_usdt: 30.0          # initial hard stop -$30
  breakeven_usdt: 20.0        # at +$20 -> SL to entry
  trail_activate_usdt: 35.0   # at +$35 -> lock +$20 and start trailing
  trail_distance_usdt: 15.0   # trail $15 behind the favorable extreme
  tp_ceiling_pct: 2.0         # hard TP cap = 2x margin = $500 (near-never safety)

sizing:
  mode: fixed                 # fixed $250/coin

cooldown:
  enabled: true               # 3-loss breaker
  consec_losses: 3
  minutes: 60                 # block all entries 60 min, then auto-resume

control:
  telegram_enabled: true      # @apexbot /off /on /close /status + kill-all

webhook:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  path: "/webhook/apex"
  # secret comes from BRIDGE_SECRET env (never commit it)

notify:
  startup: true
  open: true
  close: true
  daily: true

loop:
  bar_poll_interval_s: 30
  position_check_interval_s: 5
  mark_poll_interval_s: 3

log:
  level: INFO
  db_path: data/apex.db
```

- [ ] **Step 2: Write the load test**

Create `scripts/apex/tests/test_config_exit.py`:

```python
from apex_bridge.config import load_config

CONFIG = "config.apex.yaml"   # tests run from scripts/apex/


def test_apex_exit_config_has_exactly_three_stage_fields():
    cfg = load_config(CONFIG)
    e = cfg.exits
    assert (e.sl_loss_usdt, e.breakeven_usdt, e.trail_activate_usdt,
            e.trail_distance_usdt, e.tp_ceiling_pct) == (30.0, 20.0, 35.0, 15.0, 2.0)
    assert not hasattr(e, "lock_profit_usdt")


def test_apex_entry_sizing_cooldown_control_loaded():
    cfg = load_config(CONFIG)
    assert cfg.signal_source == "webhook"
    assert list(cfg.symbols) == ["SOL", "HYPE", "ZEC"]
    assert cfg.symbols["SOL"].margin_usdt == 250 and cfg.symbols["SOL"].leverage == 30
    assert cfg.entry.require_retest is True
    assert cfg.entry.require_reclaim is False
    assert cfg.entry.max_gap_pct == 0.0
    assert cfg.entry.min_abs_slope_pct == 0.15
    assert cfg.entry.block_body_band == (0.3, 0.5)
    assert cfg.entry.block_weekdays == []
    assert cfg.cooldown.enabled and cfg.cooldown.consec_losses == 3 and cfg.cooldown.minutes == 60
    assert cfg.control.telegram_enabled is True
    assert cfg.webhook.enabled and cfg.webhook.path == "/webhook/apex"
```

- [ ] **Step 3: Run the load test**

Run:
```bash
cd scripts/apex && python -m pytest tests/test_config_exit.py -q
```
Expected: PASS (2 tests). If `load_config` cannot find `config.apex.yaml`, confirm the test runs with cwd `scripts/apex` (it does under the command above).

- [ ] **Step 4: Sanity-print the loaded config**

Run:
```bash
cd scripts/apex && python -c "import sys; sys.path.insert(0,'src'); from apex_bridge.config import load_config; c=load_config('config.apex.yaml'); print(c.signal_source, list(c.symbols), c.exits.sl_loss_usdt, c.cooldown.minutes, c.control.telegram_enabled)"
```
Expected: `webhook ['SOL', 'HYPE', 'ZEC'] 30.0 60 True`

- [ ] **Step 5: Move the inherited reclaim config into a test fixture (isolation cleanup)**

`tests/test_reclaim_gap.py` loads `config.reclaim.yaml` from the Apex root (it tests the
reclaim-entry code, which still ships but is disabled by Apex's config). Make it a fixture
so the Apex root holds only `config.apex.yaml`:

```bash
cd /c/Users/rakai/Leverage
mkdir -p scripts/apex/tests/fixtures
git mv scripts/apex/config.reclaim.yaml scripts/apex/tests/fixtures/reclaim_gap_config.yaml \
  || mv scripts/apex/config.reclaim.yaml scripts/apex/tests/fixtures/reclaim_gap_config.yaml
```

In `scripts/apex/tests/test_reclaim_gap.py`, repoint the load (the line near the bottom that reads `config.reclaim.yaml`):

```python
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.reclaim.yaml")
```
to:
```python
    cfg = load_config(Path(__file__).resolve().parent / "fixtures" / "reclaim_gap_config.yaml")
```

Confirm the Apex root no longer carries a foreign deployable config:
```bash
ls scripts/apex/*.yaml
```
Expected: only `config.apex.yaml`.

- [ ] **Step 6: Run the full suite**

Run (substitute the venv python from the dispatch):
```bash
cd scripts/apex && "<venv python>" -m pytest -q
```
Expected: PASS (config + reclaim-gap via the fixture path + all inherited tests).

- [ ] **Step 7: Commit**

```bash
git add scripts/apex/config.apex.yaml scripts/apex/tests/test_config_exit.py scripts/apex/tests/fixtures/reclaim_gap_config.yaml scripts/apex/tests/test_reclaim_gap.py
git commit -m "feat(apex): config.apex.yaml + move reclaim config to test fixture (isolation)"
```

---

### Task 4: Wire trail-mode closes into the cooldown breaker

**Files:**
- Modify: `scripts/apex/src/apex_bridge/main.py` (trail close block in `position_check_loop`, ~lines 1100-1129)
- Create: `scripts/apex/tests/test_cooldown_trail.py`

**Interfaces:**
- Consumes: existing `Bridge._register_close(reason: str, pnl: float)` and `Bridge._cooldown_active()` (already defined; currently only called from `_close_regime`).
- Produces: after any trail-mode close, `_register_close(decision.reason, pnl)` is called so 3 consecutive losing closes arm the cooldown.

- [ ] **Step 1: Write the failing test**

Create `scripts/apex/tests/test_cooldown_trail.py`:

```python
"""3 consecutive losing trail closes arm the cooldown (basket-wide, auto-resume)."""
import inspect
import types

from apex_bridge.config import CooldownConfig
from apex_bridge.main import Bridge


def _make_bridge(consec=3, minutes=60):
    b = object.__new__(Bridge)               # skip __init__ (needs a full cfg)
    b.cfg = types.SimpleNamespace(
        cooldown=CooldownConfig(enabled=True, consec_losses=consec, minutes=minutes),
        notify=types.SimpleNamespace(close=False),
    )
    b._cd_consec = 0
    b._cd_until = 0.0
    b._cd_armed = False
    return b


def test_three_losses_arm_cooldown():
    b = _make_bridge()
    assert not b._cooldown_active()
    b._register_close("sl", -12.0)
    b._register_close("sl_be", -3.0)
    assert not b._cooldown_active()          # 2 losses, not yet
    b._register_close("trail_sl", -8.0)
    assert b._cooldown_active()              # 3rd loss arms it


def test_a_win_resets_the_streak():
    b = _make_bridge()
    b._register_close("sl", -12.0)
    b._register_close("trail_sl", +25.0)     # win resets
    b._register_close("sl", -5.0)
    b._register_close("sl", -5.0)
    assert not b._cooldown_active()          # only 2 in a row after the win


def test_trail_close_path_feeds_the_breaker():
    """The trail close block in position_check_loop must call _register_close."""
    src = inspect.getsource(Bridge.position_check_loop)
    assert "_register_close(decision.reason" in src, \
        "trail close path must feed the cooldown breaker"
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
cd scripts/apex && python -m pytest tests/test_cooldown_trail.py -q
```
Expected: the two `_register_close` behavior tests PASS (the method already exists); `test_trail_close_path_feeds_the_breaker` FAILS (the trail close block does not yet call `_register_close`).

- [ ] **Step 3: Call `_register_close` from the trail close path**

In `scripts/apex/src/apex_bridge/main.py`, inside `position_check_loop`, locate the trail close block where `pnl` is computed and the trade is booked (right after `del self.trade_ids[symbol]`, before the `notify.notify_close` call):

```python
                        del self.trade_ids[symbol]
                        # Telegram close alert
                        if self.cfg.notify.close:
                            asyncio.create_task(notify.notify_close(
```

insert the cooldown feed so it runs on every trail close:

```python
                        del self.trade_ids[symbol]
                        # Feed the 3-loss cooldown breaker (trail mode).
                        self._register_close(decision.reason, pnl)
                        # Telegram close alert
                        if self.cfg.notify.close:
                            asyncio.create_task(notify.notify_close(
```

- [ ] **Step 4: Run the tests**

Run:
```bash
cd scripts/apex && python -m pytest tests/test_cooldown_trail.py -q
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/apex/src/apex_bridge/main.py scripts/apex/tests/test_cooldown_trail.py
git commit -m "feat(apex): feed trail-mode closes into the 3-loss cooldown breaker"
```

---

### Task 5: Rebrand Reclaim → Apex in operator-facing strings

**Files:**
- Modify: `scripts/apex/src/apex_bridge/main.py` (startup banner, status header)
- Modify: `scripts/apex/src/apex_bridge/__init__.py`
- Modify: `scripts/apex/src/apex_bridge/notify.py` (any "Reclaim" in messages)
- Modify: `scripts/apex/src/apex_bridge/telegram_control.py` (any "Reclaim" header)
- Modify: `scripts/apex/src/apex_bridge/signals.py` (module DOCSTRING mentions only — do NOT rename the `check_reclaim` function)

**Interfaces:**
- Produces: operator-facing strings (logs, Telegram startup/status) read "Apex". The `check_reclaim` function name, the `require_reclaim` config field, and docstrings describing that disabled entry mechanic are unchanged.

- [ ] **Step 1: Find every operator-facing "Reclaim" string**

Run:
```bash
grep -rn "Reclaim\|RECLAIM" scripts/apex/src/apex_bridge --include=*.py
```
Note each hit. Classify: STRING literal in a log/notify/status (rename to "Apex"/"APEX") vs the `check_reclaim` function name / `require_reclaim` field / docstring describing the disabled feature (LEAVE these — renaming them would break the entry code or misdescribe it).

- [ ] **Step 2: Rename the operator-facing strings**

For each STRING-literal hit (e.g. the startup banner `"RECLAIM PAPER BRIDGE ..."`, the `on_status` header `"📋 <b>Reclaim status</b>"`, `notify.notify_error("Reclaim startup aborted...")`, any `"Reclaim starting up…"`), change the visible word to `Apex` / `APEX`. Example:

```python
        log.info("RECLAIM PAPER BRIDGE — HA-V3 flip · EMA9 reclaim-retest · 0.05pct gap · trail exit")
```
becomes:
```python
        log.info("APEX PAPER BRIDGE — SMRT Pro V3 webhook · EMA9 retest · 3-stage trail exit")
```
and:
```python
        lines = ["📋 <b>Reclaim status</b>"]
```
becomes:
```python
        lines = ["📋 <b>Apex status</b>"]
```

- [ ] **Step 3: Verify only intended references remain**

Run:
```bash
grep -rn "Reclaim\|RECLAIM" scripts/apex/src/apex_bridge --include=*.py
```
Expected: only `check_reclaim` (function), `require_reclaim` (config field), and docstrings that describe the disabled reclaim ENTRY mechanic. No operator-facing "Reclaim" branding.

- [ ] **Step 4: Run the suite (no behavior change expected)**

Run:
```bash
cd scripts/apex && python -m pytest -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/apex/src/apex_bridge
git commit -m "chore(apex): rebrand operator-facing strings Reclaim -> Apex"
```

---

### Task 6: Deploy scaffolding (compose, env, Traefik, TV alert doc)

**Files:**
- Create: `scripts/apex/docker-compose.apex.yml`
- Create: `scripts/apex/.env.example`
- Modify: `scripts/apex/Dockerfile` (only if it hard-codes the reclaim config name; otherwise leave)
- Create: `scripts/apex/TV_ALERTS.md`
- Verify: repo `.gitignore` ignores `scripts/apex/.env` and `scripts/apex/data/`

**Interfaces:**
- Produces: a buildable container `apex-bridge` exposing the webhook, routed at `apex.agentneo.cloud/webhook/apex`, with `@apexbot` Telegram control. (Live deploy to the VPS is a manual operator step performed later under Rich's explicit go — this task only produces the artifacts and verifies them locally.)

- [ ] **Step 1: Write `docker-compose.apex.yml`**

Create `scripts/apex/docker-compose.apex.yml` (webhook port routed via Traefik; based on the Reclaim compose but with the inbound HTTP route the native-signal Reclaim did not need):

```yaml
# Apex PAPER bridge — SMRT Pro V3 webhook -> EMA9 retest -> 3-stage trail, on Lighter.
# Own image, DB (data/apex.db), container, Telegram bot (@apexbot). Webhook at
# apex.agentneo.cloud/webhook/apex. Isolated from every other bridge.
#
# Deploy:  docker compose -f docker-compose.apex.yml up -d --build
services:
  apex-bridge:
    build: .
    image: apex-bridge:latest
    container_name: apex-bridge
    restart: unless-stopped
    env_file:
      - .env                         # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (@apexbot), BRIDGE_SECRET
    environment:
      TELEGRAM_SENDER_TAG: "APEX"
    volumes:
      - ./data:/app/data
      - ./src:/app/src
      - ./config.apex.yaml:/app/config.apex.yaml:ro
    command: ["python", "-u", "run_bridge.py", "--config", "config.apex.yaml"]
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.apex.rule=Host(`apex.agentneo.cloud`)"
      - "traefik.http.routers.apex.entrypoints=websecure"
      - "traefik.http.routers.apex.tls.certresolver=le"
      - "traefik.http.services.apex.loadbalancer.server.port=8080"
    healthcheck:
      test: ["CMD-SHELL", "find /app/data/apex.db /app/data/apex.db-wal -mmin -15 -type f 2>/dev/null | grep -q . || exit 1"]
      interval: 5m
      timeout: 15s
      start_period: 3m
      retries: 3
    networks:
      - default
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

> NOTE for the operator: confirm the Traefik network name and certresolver match the other bridges on srv1370094 (the existing reclaim/scalper routers are the reference). Adjust `networks:` / `certresolver` to match the live Traefik before deploying.

- [ ] **Step 2: Write `.env.example` (no real secrets)**

Create `scripts/apex/.env.example`:

```bash
# Copy to .env and fill in. .env is gitignored — never commit real values.
TELEGRAM_BOT_TOKEN=apexbot-token-here
TELEGRAM_CHAT_ID=your-chat-id
BRIDGE_SECRET=choose-a-webhook-secret
```

- [ ] **Step 3: Ensure `.env` and `data/` are gitignored**

Run:
```bash
grep -nE "scripts/apex/\.env|scripts/apex/data|^\*\*/\.env|^\.env$" .gitignore || echo "CHECK"
```
If `scripts/apex/.env` or `scripts/apex/data/` are not already covered by an existing pattern, append to repo `.gitignore`:

```
scripts/apex/.env
scripts/apex/data/
```

Also ensure pytest cache noise is ignored (the copy left a `.pytest_cache/`); if `**/.pytest_cache/` is not already in repo `.gitignore`, add it.

- [ ] **Step 4: Fix the inherited Dockerfile (config name + healthcheck db name)**

The copied `scripts/apex/Dockerfile` carries Reclaim-specific strings. Run:
```bash
grep -n "config\|reclaim" scripts/apex/Dockerfile
```
Fix both classes of issue:
- If it `COPY`s or names `config.reclaim.yaml`, change it to not reference a specific config (the compose bind-mounts `config.apex.yaml` and passes it via `command`).
- If it has a `HEALTHCHECK` that names `reclaim.db` (e.g. `find /app/data/reclaim.db /app/data/reclaim.db-wal ...`), change `reclaim.db` → `apex.db` so the healthcheck watches Apex's real DB. (The compose healthcheck in Step 1 also targets `apex.db`; keep them consistent.)

Then confirm no Reclaim db/config names remain in the Dockerfile:
```bash
grep -n "reclaim" scripts/apex/Dockerfile || echo "Dockerfile clean — OK"
```

- [ ] **Step 4b: Remove the inherited Reclaim deploy doc**

The copy brought `scripts/apex/DEPLOY.md` (entirely Reclaim/scalper content). Apex's own
docs are `README.md` (Task 7) + `TV_ALERTS.md` (Step 5 below), so delete the stale one:
```bash
git rm scripts/apex/DEPLOY.md 2>/dev/null || rm -f scripts/apex/DEPLOY.md
```

- [ ] **Step 5: Write the TradingView alert doc**

Create `scripts/apex/TV_ALERTS.md`:

```markdown
# Apex — TradingView Alerts (SMRT Pro V3)

Create 3 alerts (HYPE, SOL, ZEC), one per coin, from the SMRT Algo Pro V3 indicator.

- Condition: SMRT Pro V3 buy/sell signal
- Webhook URL: `https://apex.agentneo.cloud/webhook/apex`
- Message (JSON):
  ```json
  {"secret": "<BRIDGE_SECRET>", "symbol": "{{ticker}}", "action": "buy", "source": "pro_v3"}
  ```
  (and a matching `"action": "sell"` alert)
- `{{ticker}}` resolves to e.g. `ZECUSDT.P`; the webhook maps it to the Lighter market (`ZEC`).

Gotcha: TradingView alerts silently expire (plan tier / inactivity). If Apex stops
filling, check the TV alerts panel FIRST before touching the bridge.
```

- [ ] **Step 6: Verify the image builds (no live deploy)**

Run:
```bash
cd scripts/apex && docker build -t apex-bridge:latest . && echo BUILD_OK
```
Expected: `BUILD_OK`. If Docker is unavailable in this environment, skip and note it in the report for the operator — the live build happens on the VPS.

- [ ] **Step 7: Commit**

```bash
git add scripts/apex/docker-compose.apex.yml scripts/apex/.env.example scripts/apex/TV_ALERTS.md scripts/apex/Dockerfile .gitignore
git commit -m "feat(apex): deploy scaffolding — compose, env example, Traefik route, TV alert doc"
```

---

### Task 7: Isolation acceptance gate + README

**Files:**
- Create: `scripts/apex/README.md`

**Interfaces:**
- Produces: a documented, verified-isolated Apex bridge ready for the operator's live-deploy step.

- [ ] **Step 1: Prove zero cross-bridge / cross-package linkage**

Run each; all must show NO matches:
```bash
grep -rn "lighter_bridge" scripts/apex --include=*.py
grep -rn "from scripts\.\|import scripts\." scripts/apex --include=*.py
grep -rn "reclaim\.db\|scalper\.db\|blofin_bridge" scripts/apex --include=*.py
```
Expected: empty for all three. Any hit is an isolation leak — fix it before continuing.

- [ ] **Step 2: Prove the secret is not committed anywhere**

Scan for any Telegram bot token by its *shape* (`<digits>:AA<35+ chars>`) so no real
token is embedded in this plan or the scan itself:
```bash
grep -rEn "[0-9]{8,12}:AA[0-9A-Za-z_-]{30,}" scripts/apex docs/ ; echo "exit=$?"
```
Expected: NO matches (the bot token must live only in the untracked `.env`). If it appears, remove it and move it to `.env`.

- [ ] **Step 3: Full suite green**

Run:
```bash
cd scripts/apex && python -m pytest -q
```
Expected: PASS (state machine, config, cooldown, plus inherited tests).

- [ ] **Step 4: Write the README**

Create `scripts/apex/README.md`:

```markdown
# Apex — Rich's V3 (Lighter paper)

Standalone copy of the V3.1 pipeline: SMRT Pro V3 TV alert -> 9 EMA (5m) retest ->
fixed $250 @ 30x -> 3-stage trail exit (SL -$30 -> BE +$20 -> at +$35 lock +$20 &
trail $15). Coins: HYPE, SOL, ZEC. 3-loss/60-min cooldown breaker. Telegram pause/
stop via @apexbot. Fully isolated: package `apex_bridge`, DB `apex.db`, container
`apex-bridge`, webhook apex.agentneo.cloud/webhook/apex.

## Run (local)
    cd scripts/apex
    cp .env.example .env   # fill TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / BRIDGE_SECRET
    python -m pytest -q
    python run_bridge.py --config config.apex.yaml

## Deploy (VPS, operator step)
    docker compose -f docker-compose.apex.yml up -d --build

## Knobs
All in config.apex.yaml. Entry filters (slope/ATR band) are ON at proven values;
tuning is deliberately deferred. Revert cooldown: cooldown.enabled=false.

## TV alerts
See TV_ALERTS.md — 3 alerts (HYPE/SOL/ZEC) to /webhook/apex.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/apex/README.md
git commit -m "docs(apex): README + isolation acceptance verified"
```

---

## Self-Review

**Spec coverage:**
- Isolation / naming → Task 1 (+ Task 7 gate). ✓
- SMRT Pro V3 webhook + 9 EMA retest → config (Task 3), reuse of webhook+process_pending; plain retest via `require_reclaim:false`, `max_gap_pct:0`. ✓
- Slope 0.15 + ATR band on + Sunday on → Task 3 config + load test. ✓
- $250×30x, $3k → Task 3. ✓
- 3-stage exit → Task 2 (logic + config schema). ✓
- Lighter venue → inherited from Reclaim; Task 7 grep proves no BloFin. ✓
- 3-loss/60-min cooldown → Task 4 (wiring) + Task 3 (config). ✓
- Telegram pause/stop → inherited `TelegramControl`; enabled in Task 3; token via env (Task 6). ✓
- Domain apex.agentneo.cloud + webhook /webhook/apex → Task 3 + Task 6. ✓
- Token never committed → Task 6 `.env` + Task 7 Step 2 gate. ✓
- Dashboard deferred → not built; dir reserved at deploy (out of scope). ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code; commands have expected output. ✓

**Type consistency:** `ExitConfig` fields (`sl_loss_usdt`, `breakeven_usdt`, `trail_activate_usdt`, `trail_distance_usdt`, `tp_ceiling_pct`) are defined and used consistently across Task 2 (dataclass + state machine + tests) and Task 3 (yaml + load test). `step(pos, mark_price, cfg)` signature unchanged from the caller in `main.py:1100`. `_register_close(reason, pnl)` matches the existing definition. ✓

**One open operator detail (not a plan gap):** the Traefik network name / certresolver on srv1370094 must match the live stack — flagged inline in Task 6 Step 1 for the operator to confirm against the existing reclaim/scalper routers before the live deploy.
