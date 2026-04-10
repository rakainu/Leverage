# Strategy Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the SMC meme trading system's profitability based on 87 trades of paper data — fix SL execution slippage, add convergence speed filter, deactivate toxic wallets, add time-of-day filter, and implement trailing stop loss.

**Architecture:** Five independent changes to the existing async pipeline: (1) faster position monitoring loop, (2) convergence speed gating in the signal router, (3) wallet deactivation via wallets.json, (4) hour-of-day trade filter in signal router, (5) trailing SL replacing fixed TP/SL in position manager. All config via existing .env/Settings pattern.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, Pydantic Settings, Jupiter API

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `config/settings.py` | Modify | Add new settings: trailing SL params, convergence speed bounds, blocked hours |
| `executor/position_manager.py` | Rewrite | Trailing SL logic, faster check interval (5s), in-memory high watermark tracking |
| `main.py` | Modify | Log new settings on startup |
| `engine/convergence.py` | Modify | Attach `convergence_minutes` to signal |
| `engine/signal.py` | Modify | Add `convergence_minutes` field to ConvergenceSignal |
| `config/wallets.json` | Modify (VPS) | Deactivate 20 toxic wallets |
| `db/schema.sql` | Modify | Add `high_watermark_pct` column to positions |

---

### Task 1: Add new settings to config

**Files:**
- Modify: `config/settings.py:36-41`

- [ ] **Step 1: Add trailing SL, convergence speed, and time filter settings**

In `config/settings.py`, replace the position management block (lines 36-41) with:

```python
    # Position management
    max_concurrent_positions: int = 5
    trade_amount_sol: float = 0.1
    stop_loss_pct: float = 25.0
    position_timeout_minutes: int = 240

    # Trailing stop loss
    # Phase 1: Entry SL at -stop_loss_pct (25%)
    # Phase 2: Once profit hits trail_activate_pct, move SL to trail_breakeven_pct
    # Phase 3: Trail at trail_distance_pct below high watermark
    trail_activate_pct: float = 30.0      # start trailing after +30%
    trail_breakeven_pct: float = 5.0      # lock in +5% when trailing activates
    trail_distance_pct: float = 20.0      # trail 20% below peak

    # Convergence speed filter (minutes between first buy and signal)
    min_convergence_minutes: float = 10.0
    max_convergence_minutes: float = 20.0

    # Time-of-day filter (UTC hours to block trading)
    blocked_hours_utc: list[int] = [13, 14, 15, 16]
```

Also add a `parse_json_list` validator for `blocked_hours_utc` — add it to the existing validator:

```python
    @field_validator("solana_rpc_urls", "solana_ws_urls", "blocked_hours_utc", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v
```

Remove `take_profit_pct` — the trailing stop replaces it.

- [ ] **Step 2: Commit**

```bash
git add config/settings.py
git commit -m "config: add trailing SL, convergence speed, and time filter settings"
```

---

### Task 2: Add convergence_minutes to ConvergenceSignal

**Files:**
- Modify: `engine/signal.py:21-30`
- Modify: `engine/convergence.py:84-93`

- [ ] **Step 1: Add field to dataclass**

In `engine/signal.py`, add `convergence_minutes` to ConvergenceSignal:

```python
@dataclass
class ConvergenceSignal:
    """Emitted when N+ distinct wallets buy the same token within the window."""
    token_mint: str
    token_symbol: str | None
    wallets: list[str]
    buy_events: list[BuyEvent]
    first_buy_at: datetime
    signal_at: datetime
    avg_amount_sol: float
    total_amount_sol: float
    convergence_minutes: float = 0.0
```

- [ ] **Step 2: Compute convergence_minutes when creating signal**

In `engine/convergence.py`, in `_check_convergence` (around line 84), after creating the signal object, compute the field:

```python
        signal = ConvergenceSignal(
            token_mint=token_mint,
            token_symbol=events[0].token_symbol,
            wallets=sorted(distinct_wallets),
            buy_events=list(events),
            first_buy_at=min(e.timestamp for e in events),
            signal_at=datetime.now(timezone.utc),
            avg_amount_sol=mean(e.amount_sol for e in events),
            total_amount_sol=sum(e.amount_sol for e in events),
        )
        signal.convergence_minutes = (signal.signal_at - signal.first_buy_at).total_seconds() / 60
```

- [ ] **Step 3: Commit**

```bash
git add engine/signal.py engine/convergence.py
git commit -m "engine: attach convergence_minutes to signal for speed filtering"
```

---

### Task 3: Add convergence speed + time-of-day filters to signal router

**Files:**
- Modify: `main.py:50-97` (signal_router function)

- [ ] **Step 1: Add filters before safety check**

In `main.py`, inside the `signal_router` `while True` loop, right after `signal: ConvergenceSignal = await signal_bus.get()` (line 51), add the two filters BEFORE the safety check:

```python
        # --- Convergence speed filter ---
        conv_min = signal.convergence_minutes
        if conv_min < settings.min_convergence_minutes or conv_min > settings.max_convergence_minutes:
            logger.info(
                f"SKIP {signal.token_mint[:12]}.. — convergence speed "
                f"{conv_min:.1f}min outside [{settings.min_convergence_minutes}-{settings.max_convergence_minutes}]min window"
            )
            db = await get_db()
            await db.execute(
                """UPDATE convergence_signals SET action_taken='skip_speed'
                   WHERE token_mint=? AND action_taken IS NULL
                   ORDER BY signal_at DESC LIMIT 1""",
                (signal.token_mint,),
            )
            await db.commit()
            continue

        # --- Time-of-day filter (UTC) ---
        current_hour = datetime.now(timezone.utc).hour
        if current_hour in settings.blocked_hours_utc:
            logger.info(
                f"SKIP {signal.token_mint[:12]}.. — blocked hour UTC {current_hour}"
            )
            db = await get_db()
            await db.execute(
                """UPDATE convergence_signals SET action_taken='skip_hour'
                   WHERE token_mint=? AND action_taken IS NULL
                   ORDER BY signal_at DESC LIMIT 1""",
                (signal.token_mint,),
            )
            await db.commit()
            continue
```

Also add the `datetime` and `timezone` imports at the top of `main.py` (already imported on line 6 — verify they're there).

- [ ] **Step 2: Log new filter settings on startup**

In `main.py`, in the `main()` function, after line 156 (the TP/SL log line), replace that line and add:

```python
    logger.info(f"SL: {settings.stop_loss_pct}% | Trail: activate@+{settings.trail_activate_pct}%, lock@+{settings.trail_breakeven_pct}%, distance {settings.trail_distance_pct}%")
    logger.info(f"Convergence speed: {settings.min_convergence_minutes}-{settings.max_convergence_minutes}min | Blocked hours UTC: {settings.blocked_hours_utc}")
```

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "router: add convergence speed and time-of-day trade filters"
```

---

### Task 4: Add high_watermark_pct to DB schema and rewrite position manager with trailing SL

**Files:**
- Modify: `db/schema.sql`
- Rewrite: `executor/position_manager.py`

- [ ] **Step 1: Add high_watermark_pct column to schema**

In `db/schema.sql`, add after the `pnl_24h_pct` line (line 56):

```sql
    high_watermark_pct REAL DEFAULT 0.0,
```

- [ ] **Step 2: Rewrite position_manager.py with trailing SL**

Replace the entire `executor/position_manager.py` with:

```python
"""Position manager — monitors open positions with trailing stop loss."""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import Settings
from db.database import get_db
from executor.jupiter import JupiterClient

logger = logging.getLogger("smc.executor.positions")


class PositionManager:
    """Every 5 seconds, checks all open positions for exit conditions.

    Exit logic (trailing stop):
      Phase 1 (entry):  SL at -stop_loss_pct (default -25%)
      Phase 2 (profit): Once pnl >= trail_activate_pct (default +30%),
                         move SL floor to +trail_breakeven_pct (default +5%)
                         and trail at trail_distance_pct below high watermark.
      Phase 3 (peak):   As price rises, SL ratchets up. Never moves down.
      Timeout:           Close after position_timeout_minutes regardless.
    """

    def __init__(self, settings: Settings, alert_bus: asyncio.Queue):
        self.settings = settings
        self.alert_bus = alert_bus
        self.jupiter = JupiterClient(settings.jupiter_api_key)

    async def run(self):
        """Main monitoring loop — 5s interval for tighter SL execution."""
        logger.info(
            f"Position manager started (5s interval, "
            f"trail: activate@+{self.settings.trail_activate_pct}%, "
            f"lock@+{self.settings.trail_breakeven_pct}%, "
            f"distance {self.settings.trail_distance_pct}%)"
        )
        while True:
            try:
                await self._check_positions()
            except Exception as e:
                logger.error(f"Position manager error: {e}")
            await asyncio.sleep(5)

    async def _check_positions(self):
        """Evaluate all open positions."""
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE status='open'"
        )
        for pos in rows:
            await self._evaluate_position(pos, db)

    async def _evaluate_position(self, pos, db):
        """Check a single position for exit conditions."""
        current_price = await self.jupiter.get_price_sol(pos["token_mint"])
        if not current_price:
            return

        entry_price = pos["entry_price"]
        if not entry_price or entry_price == 0:
            return

        pnl_pct = ((current_price - entry_price) / entry_price) * 100

        # Cap at +/-1000% — anything beyond is bad price data
        if abs(pnl_pct) > 1000:
            logger.warning(
                f"Position #{pos['id']} P&L {pnl_pct:+.0f}% exceeds cap, "
                f"entry={entry_price}, current={current_price} — skipping"
            )
            return

        opened_at = datetime.fromisoformat(pos["opened_at"])
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60

        # --- Update high watermark ---
        prev_hwm = pos["high_watermark_pct"] or 0.0
        hwm = max(prev_hwm, pnl_pct)

        # --- Compute dynamic stop level ---
        stop_level = self._compute_stop_level(hwm)

        # --- Check exit conditions ---
        close_reason = None
        if pnl_pct <= stop_level:
            if hwm >= self.settings.trail_activate_pct:
                close_reason = "trailing_stop"
            else:
                close_reason = "stop_loss"
        elif age_min >= self.settings.position_timeout_minutes:
            close_reason = "timeout"

        if close_reason:
            pnl_sol = (pnl_pct / 100) * pos["amount_sol"]
            now = datetime.now(timezone.utc).isoformat()

            await db.execute(
                """UPDATE positions SET
                   status='closed', close_reason=?, exit_price=?,
                   current_price=?, pnl_pct=?, pnl_sol=?,
                   high_watermark_pct=?,
                   closed_at=?, updated_at=?
                   WHERE id=?""",
                (close_reason, current_price, current_price,
                 pnl_pct, pnl_sol, hwm, now, now, pos["id"]),
            )
            await db.commit()

            logger.info(
                f"Position #{pos['id']} CLOSED ({close_reason}): "
                f"{pos['token_mint'][:12]}.. | "
                f"P&L: {pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL) | "
                f"HWM: {hwm:+.1f}%"
            )

            await self.alert_bus.put({
                "type": "position_closed",
                "position_id": pos["id"],
                "token_mint": pos["token_mint"],
                "token_symbol": pos["token_symbol"],
                "reason": close_reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_sol": round(pnl_sol, 4),
                "high_watermark_pct": round(hwm, 2),
                "mode": pos["mode"],
            })
        else:
            # Update current price and high watermark
            await db.execute(
                """UPDATE positions SET
                   current_price=?, pnl_pct=?, high_watermark_pct=?, updated_at=?
                   WHERE id=?""",
                (current_price, pnl_pct, hwm,
                 datetime.now(timezone.utc).isoformat(), pos["id"]),
            )
            await db.commit()

    def _compute_stop_level(self, high_watermark_pct: float) -> float:
        """Compute the current stop-loss level based on trailing logic.

        Returns the P&L% at which the position should be stopped out.

        Phase 1: HWM < trail_activate_pct → fixed SL at -stop_loss_pct
        Phase 2: HWM >= trail_activate_pct → max(trail_breakeven_pct, HWM - trail_distance_pct)
        """
        if high_watermark_pct < self.settings.trail_activate_pct:
            # Phase 1: not yet in profit territory — use fixed SL
            return -self.settings.stop_loss_pct

        # Phase 2+: trailing active
        # The SL is the higher of:
        #   - breakeven lock (e.g. +5%)
        #   - high watermark minus trail distance (e.g. HWM - 20%)
        trailing_level = high_watermark_pct - self.settings.trail_distance_pct
        return max(self.settings.trail_breakeven_pct, trailing_level)
```

- [ ] **Step 3: Commit**

```bash
git add db/schema.sql executor/position_manager.py
git commit -m "positions: trailing SL with 5s checks, high watermark tracking"
```

---

### Task 5: Deactivate toxic wallets on VPS

**Files:**
- Modify: VPS `/docker/smc-trading/config/wallets.json`

These 20 wallets have the worst performance from the data analysis. Deactivate them by setting `"active": false`.

Full addresses to deactivate:

```
2X4H5Y9C4Fy6Pf3wpq8Q4gMvLcWvfrrwDv2bdR8AAwQv
J9TYAsWWidbrcZybmLSfrLzryANf4CgJBLdvwdGuC8MB
4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk
651WNqyBNtCnCAnuTehtpVwgp4eJWsoGURwDrV8qGEd5
5p2bA4wza1WmyEiWwyDJVNrXXsAM4M9yQcKDvTK5mWKv
3nG9zBc6fTne3j9kkS1CB5quyt25CS29GEjvMGNKmDSz
HiK8buhCxdD1JLKonZMp29TNpz4KD8M2gTSrxiva5JH9
GGPVgdQSbJQtW1yEzqMNdP4o6hkRcFmbdnaFUnXfhKmX
QSS5Dc5BA9v8PHwxYkaN7v93zsPbB83fP8wVKPzWdsG
6HS6c4YLH1DXrgAGLLG8WxTRJNaBVUQ3LKpYmZZHeYe8
DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt
5QLUCUFA62q1b2y8TKfFf31rL4Qj8zsKTQzuQ8xbTa1o
FNhJCt9smgDVABeYGzAX6FrjuJxpauVG1mKRMa6P4AXm
8SZnC4nicCmRBz1HZzFS1YrwMFEaEnkVi4k2oh5GEYV9
FSAmbD6jm6SZZQadSJeC1paX3oTtAiY9hTx1UYzVoXqj
9jGvhQ2mBtTh1BDWt8qj1KWo3Xcj8uxwPaEBu9zg6v9Y
GQhCQkMcFJXJRRysccenkUPeUrYQH3yPYntLGevCTY4s
8yhTYM3XBkkiz5VE73xg4JAVrbTcdPrwfTBnoMG5Bhfg
XJ7tbEKnrwvioTxABhEtwmG4AWkGEDDJASstUzBgh7x
6SM6A8WuvryrpAXrt4qTuXadYk7aPvSff2HkS4XNpVzP
```

- [ ] **Step 1: Run Python one-liner on VPS to deactivate wallets**

```bash
ssh root@46.202.146.30 "python3 -c \"
import json
toxic = {
    '2X4H5Y9C4Fy6Pf3wpq8Q4gMvLcWvfrrwDv2bdR8AAwQv',
    'J9TYAsWWidbrcZybmLSfrLzryANf4CgJBLdvwdGuC8MB',
    '4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk',
    '651WNqyBNtCnCAnuTehtpVwgp4eJWsoGURwDrV8qGEd5',
    '5p2bA4wza1WmyEiWwyDJVNrXXsAM4M9yQcKDvTK5mWKv',
    '3nG9zBc6fTne3j9kkS1CB5quyt25CS29GEjvMGNKmDSz',
    'HiK8buhCxdD1JLKonZMp29TNpz4KD8M2gTSrxiva5JH9',
    'GGPVgdQSbJQtW1yEzqMNdP4o6hkRcFmbdnaFUnXfhKmX',
    'QSS5Dc5BA9v8PHwxYkaN7v93zsPbB83fP8wVKPzWdsG',
    '6HS6c4YLH1DXrgAGLLG8WxTRJNaBVUQ3LKpYmZZHeYe8',
    'DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt',
    '5QLUCUFA62q1b2y8TKfFf31rL4Qj8zsKTQzuQ8xbTa1o',
    'FNhJCt9smgDVABeYGzAX6FrjuJxpauVG1mKRMa6P4AXm',
    '8SZnC4nicCmRBz1HZzFS1YrwMFEaEnkVi4k2oh5GEYV9',
    'FSAmbD6jm6SZZQadSJeC1paX3oTtAiY9hTx1UYzVoXqj',
    '9jGvhQ2mBtTh1BDWt8qj1KWo3Xcj8uxwPaEBu9zg6v9Y',
    'GQhCQkMcFJXJRRysccenkUPeUrYQH3yPYntLGevCTY4s',
    '8yhTYM3XBkkiz5VE73xg4JAVrbTcdPrwfTBnoMG5Bhfg',
    'XJ7tbEKnrwvioTxABhEtwmG4AWkGEDDJASstUzBgh7x',
    '6SM6A8WuvryrpAXrt4qTuXadYk7aPvSff2HkS4XNpVzP',
}
data = json.load(open('/docker/smc-trading/config/wallets.json'))
count = 0
for w in data['wallets']:
    if w['address'] in toxic:
        w['active'] = False
        count += 1
json.dump(data, open('/docker/smc-trading/config/wallets.json', 'w'), indent=2)
print(f'Deactivated {count} wallets')
\""
```

- [ ] **Step 2: Verify wallet count dropped**

```bash
ssh root@46.202.146.30 "python3 -c \"
import json
data = json.load(open('/docker/smc-trading/config/wallets.json'))
active = sum(1 for w in data['wallets'] if w.get('active', True))
total = len(data['wallets'])
print(f'{active} active / {total} total')
\""
```

Expected: `82 active / 102 total` (102 - 20 = 82)

---

### Task 6: Add .env settings on VPS

- [ ] **Step 1: Add new env vars to VPS .env**

```bash
ssh root@46.202.146.30 "cat >> /docker/smc-trading/.env << 'EOF'

# Trailing stop loss
SMC_TRAIL_ACTIVATE_PCT=30.0
SMC_TRAIL_BREAKEVEN_PCT=5.0
SMC_TRAIL_DISTANCE_PCT=20.0

# Convergence speed filter (minutes)
SMC_MIN_CONVERGENCE_MINUTES=10.0
SMC_MAX_CONVERGENCE_MINUTES=20.0

# Blocked hours UTC (13-16 = worst performing window)
SMC_BLOCKED_HOURS_UTC=[13,14,15,16]
EOF"
```

- [ ] **Step 2: Remove old take_profit_pct from .env (trailing SL replaces it)**

```bash
ssh root@46.202.146.30 "sed -i '/SMC_TAKE_PROFIT_PCT/d' /docker/smc-trading/.env"
```

---

### Task 7: Deploy code to VPS and restart

- [ ] **Step 1: Push code to GitHub**

```bash
git add -A
git commit -m "strategy: trailing SL, convergence speed filter, time-of-day filter, toxic wallet removal"
git push origin master
```

- [ ] **Step 2: Pull code on VPS and add the schema migration**

```bash
ssh root@46.202.146.30 "cd /docker/smc-trading && git pull origin master"
```

- [ ] **Step 3: Run schema migration inside running container**

The `init_db()` function uses `CREATE TABLE IF NOT EXISTS` so it won't add new columns. We need to add the column manually:

```bash
ssh root@46.202.146.30 "docker exec smc-trading python3 -c \"
import sqlite3
conn = sqlite3.connect('/app/data/smc.db')
try:
    conn.execute('ALTER TABLE positions ADD COLUMN high_watermark_pct REAL DEFAULT 0.0')
    conn.commit()
    print('Column added')
except Exception as e:
    print(f'Column may already exist: {e}')
conn.close()
\""
```

- [ ] **Step 4: Rebuild and restart container**

```bash
ssh root@46.202.146.30 "cd /docker/smc-trading && docker compose up -d --build"
```

- [ ] **Step 5: Verify startup logs show new settings**

```bash
ssh root@46.202.146.30 "sleep 10 && docker logs smc-trading --tail 20 2>&1"
```

Expected: Logs should show trailing SL params, convergence speed window, blocked hours, and reduced wallet count (~82 active).

- [ ] **Step 6: Verify no open positions from before the change are broken**

```bash
ssh root@46.202.146.30 "docker exec smc-trading python3 -c \"
import sqlite3
conn = sqlite3.connect('/app/data/smc.db')
rows = conn.execute('SELECT id, high_watermark_pct FROM positions WHERE status=\\\"open\\\"').fetchall()
print(f'{len(rows)} open positions')
for r in rows:
    print(f'  #{r[0]}: hwm={r[1]}')
conn.close()
\""
```
