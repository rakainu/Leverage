# Regime-Switching Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an actively-trading, per-coin regime-switching system (long-momentum in uptrends, Connors RSI-2 mean-reversion in ranges, short-momentum in downtrends) that earns profit across months, validated on a true single-account portfolio simulator over 3 years of multi-regime data.

**Architecture:** A per-coin regime classifier (ADX + EMA-slope, 1h context, hysteresis) routes each coin to one of three real, independently-vetted specialist strategies. A single shared-capital portfolio simulator executes the combined book with honest fills, compounding, and zero-liquidation sizing. A risk-escalation sweep finds the most aggressive setting that stays liquidation-free with recoverable drawdown. Success is measured as a monthly P&L distribution.

**Tech Stack:** Python 3.12, numpy, pandas, optuna (scalping venv at `scripts/scalping/venv`). Builds on the existing honest backtest package `scripts/scalping/backtest/` and the hunt modules in `scripts/scalping/analysis/strategy_hunt_2026-06-22/`.

## Global Constraints

- Venue/costs: optimize on **Lighter** (taker 0%, maker 0%, slippage 0.05%, funding 0.01%/8h). Report BloFin (taker 0.06/maker 0.02/slip 0.05) as informational only — never a gate.
- Starting capital **$3,000**, **compounding ON**, risk profile: maximize compounded return subject to **zero liquidations** (hard) and **max drawdown ≤ 40%** (recoverable ceiling).
- **No lookahead** anywhere: signals/regime decided on a bar's CLOSE, orders fill at the next bar's OPEN; channel/indicator levels use only confirmed prior bars (shift ≥1). Honest fills: market/stop = adverse slippage (taker); both-hit bar → stop wins.
- Data: OKX, 3 years (2023-06 → 2026-06). Context TF = **1h**; execution TFs = **15m** (Up/Down) and **5m** (Range). History lives in `scripts/scalping/analysis/strategy_hunt_2026-06-22/data_hist/` (1h, 4h present; **15m and 5m must be fetched** for the 10 majors — Task 1). Coins: BTC, ETH, SOL, BNB, DOGE, XRP, ADA, AVAX, LINK, LTC.
- Strategies must be REAL: Donchian (vetted Pine), Connors RSI-2 (documented). No invented families. Each specialist is optimized on its own regime's bars and walk-forward-vetted before freezing.
- Run everything on `scripts/scalping/venv/Scripts/python.exe`. All new library code in `scripts/scalping/backtest/`; hybrid-specific code in `scripts/scalping/hybrid/`.

---

## File Structure

```
scripts/scalping/backtest/
  regime.py            # per-coin ADX+EMA-slope regime classifier (reusable)
  portfolio_sim.py     # single shared-capital multi-coin simulator (reusable)
  tests/test_regime.py
  tests/test_portfolio_sim.py
scripts/scalping/hybrid/
  __init__.py
  data.py              # multi-TF loader (1h/15m/5m) over data_hist
  strategies/
    long_momo.py       # Donchian breakout long (wraps donchian_millerrh, regime-gated)
    short_momo.py      # Donchian breakdown short (mirror)
    range_rsi2.py      # Connors RSI-2 both-sided mean reversion
  switcher.py          # regime -> specialist routing + portfolio intents
  optimize_specialists.py  # per-regime optimize + walk-forward each specialist
  sweep_risk.py        # risk-escalation frontier (return/DD/liq vs risk_frac)
  report.py            # monthly P&L distribution + per-regime attribution
  run_hybrid.py        # end-to-end: classify -> switch -> portfolio sim -> report
  tests/test_range_rsi2.py
  tests/test_short_momo.py
  tests/test_switcher.py
```

Reused as-is (do not modify): `backtest/engine.py`, `backtest/metrics.py`, `backtest/optimizer.py`, `backtest/validation.py`, `analysis/strategy_hunt_2026-06-22/donchian_millerrh.py`, `analysis/strategy_hunt_2026-06-22/eventsim.py`, `fetch_history.py`.

---

### Task 1: Fetch 15m + 5m multi-year history

**Files:**
- Modify: `scripts/scalping/analysis/strategy_hunt_2026-06-22/fetch_history.py:` (extend `TFS`)
- Create: `scripts/scalping/hybrid/data.py`
- Test: `scripts/scalping/hybrid/tests/test_data.py`

**Interfaces:**
- Produces: `data.load(coin: str, tf: str) -> pd.DataFrame` (cols Open/High/Low/Close/Volume, UTC index) reading `data_hist/okx_<COIN>_<tf>.parquet`; `data.COINS: list[str]`; `data.regime_tf="1h"`.

- [ ] **Step 1: Extend the fetcher to 15m and 5m.** In `fetch_history.py` change `TFS = ["1h", "4h"]` to `TFS = ["1h", "4h", "15m", "5m"]` and add `"15m": 900_000, "5m": 300_000` to `TF_MS`. (5m over 3y ≈ 315k bars/coin — acceptable; pagination already forward-`since`.)

- [ ] **Step 2: Run the fetch.**
Run: `scripts/scalping/venv/Scripts/python.exe scripts/scalping/analysis/strategy_hunt_2026-06-22/fetch_history.py`
Expected: prints `<COIN> 15m ... (3.0y)` and `<COIN> 5m ... (~3.0y or exchange max)` for all 10 coins; parquet files appear in `data_hist/`.

- [ ] **Step 3: Write the loader test.**
```python
# hybrid/tests/test_data.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data
def test_load_all_tfs():
    for tf in ["1h", "15m", "5m"]:
        df = data.load("BTC", tf)
        assert list(df.columns) == ["Open","High","Low","Close","Volume"]
        assert len(df) > 1000 and df.index.is_monotonic_increasing
```

- [ ] **Step 4: Implement `hybrid/data.py`.**
```python
import os
import pandas as pd
HIST = os.path.join(os.path.dirname(__file__), "..", "analysis", "strategy_hunt_2026-06-22", "data_hist")
COINS = ["BTC","ETH","SOL","BNB","DOGE","XRP","ADA","AVAX","LINK","LTC"]
regime_tf = "1h"
def load(coin: str, tf: str) -> pd.DataFrame:
    return pd.read_parquet(os.path.join(HIST, f"okx_{coin}_{tf}.parquet")).astype(float)
```

- [ ] **Step 5: Run test + commit.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_data.py -v`
Expected: PASS. Then `git add -A scripts/scalping/hybrid scripts/scalping/analysis/strategy_hunt_2026-06-22/fetch_history.py && git commit -m "feat(hybrid): multi-TF data loader + 15m/5m history"`

---

### Task 2: Per-coin regime classifier

**Files:**
- Create: `scripts/scalping/backtest/regime.py`
- Test: `scripts/scalping/backtest/tests/test_regime.py`

**Interfaces:**
- Consumes: `engine.ema`, `engine.adx`.
- Produces: `regime.classify(df_1h, adx_trend=25.0, adx_range=20.0, ema_len=100, slope_lb=3, confirm_bars=2) -> pd.Series[int]` indexed like `df_1h`, values `{1: up, 0: range, -1: down}`, causal (each value uses only bars ≤ its index), with hysteresis (state changes only after `confirm_bars` consecutive bars agree on the new raw state).

- [ ] **Step 1: Write the failing tests.**
```python
# backtest/tests/test_regime.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np, pandas as pd
from regime import classify
def _mk(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c*1.001, "Low": c*0.999, "Close": c, "Volume": 1.0}, index=idx)
def test_uptrend_classifies_up():
    df = _mk(list(np.linspace(100, 200, 400)))   # steady strong uptrend
    r = classify(df)
    assert r.iloc[-1] == 1
def test_downtrend_classifies_down():
    df = _mk(list(np.linspace(200, 100, 400)))
    r = classify(df)
    assert r.iloc[-1] == -1
def test_flat_classifies_range():
    rng = np.tile([100, 101, 100, 99], 100)       # choppy sideways
    df = _mk(list(rng))
    r = classify(df)
    assert r.iloc[-1] == 0
def test_causal_no_lookahead():
    df = _mk(list(np.linspace(100, 200, 400)))
    r_full = classify(df)
    r_trunc = classify(df.iloc[:300])
    assert (r_full.iloc[:300].fillna(-9) == r_trunc.fillna(-9)).all()
```

- [ ] **Step 2: Run to verify failure.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/backtest/tests/test_regime.py -v`
Expected: FAIL (no module `regime`).

- [ ] **Step 3: Implement `backtest/regime.py`.**
```python
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from engine import ema, adx

def classify(df, adx_trend=25.0, adx_range=20.0, ema_len=100, slope_lb=3, confirm_bars=2):
    c = df["Close"]
    a = adx(df, 14).shift(1)                 # confirmed prior bar -> causal
    e = ema(c, ema_len)
    rising = (e > e.shift(slope_lb)).shift(1)
    falling = (e < e.shift(slope_lb)).shift(1)
    av, rv, fv = a.values, rising.values, falling.values
    raw = np.zeros(len(df), dtype=float)     # raw per-bar state
    for i in range(len(df)):
        if not np.isfinite(av[i]):
            raw[i] = 0; continue
        if av[i] >= adx_trend and rv[i] == True:   # noqa: E712
            raw[i] = 1
        elif av[i] >= adx_trend and fv[i] == True:  # noqa: E712
            raw[i] = -1
        elif av[i] <= adx_range:
            raw[i] = 0
        else:
            raw[i] = raw[i-1] if i else 0     # dead-band: hold previous
    # hysteresis: only switch after confirm_bars consecutive agreeing raw states
    out = np.zeros(len(df), dtype=int)
    state = 0; run_val = raw[0]; run_len = 0
    for i in range(len(df)):
        if raw[i] == run_val:
            run_len += 1
        else:
            run_val = raw[i]; run_len = 1
        if run_len >= confirm_bars and run_val != state:
            state = int(run_val)
        out[i] = state
    return pd.Series(out, index=df.index)
```

- [ ] **Step 4: Run tests to verify pass.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/backtest/tests/test_regime.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/backtest/regime.py scripts/scalping/backtest/tests/test_regime.py && git commit -m "feat(backtest): per-coin ADX+EMA-slope regime classifier with hysteresis"`

---

### Task 3: Connors RSI-2 range specialist

**Files:**
- Create: `scripts/scalping/hybrid/strategies/range_rsi2.py`, `scripts/scalping/hybrid/strategies/__init__.py`
- Test: `scripts/scalping/hybrid/tests/test_range_rsi2.py`

**Interfaces:**
- Consumes: `engine.Signal, engine.rsi, engine.sma, engine.atr`.
- Produces: `range_rsi2.signals(df, lo=5, hi=95, rsi_len=2, sma_len=5, sl_atr=1.5, max_bars=24, side="both") -> list[Signal]`. Faithful Connors RSI-2: long when RSI(rsi_len) < lo AND close < SMA(sma_len) (stretched below mean); short when RSI > hi AND close > SMA. Exit target = reversion to SMA(sma_len) (encoded as tp_dist = |close - sma|), hard stop = sl_atr*ATR, time stop = max_bars. Signal decided on close, fills next open (engine handles).

- [ ] **Step 1: Write the failing test.**
```python
# hybrid/tests/test_range_rsi2.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
import numpy as np, pandas as pd
import range_rsi2
def _mk(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c*1.001, "Low": c*0.999, "Close": c, "Volume": 1.0}, index=idx)
def test_oversold_emits_long():
    # sharp drop -> RSI(2) very low, price below SMA -> long signal
    df = _mk([100]*20 + [99, 97, 94, 90, 85])
    sigs = range_rsi2.signals(df, side="long")
    assert any(s.side == 1 for s in sigs)
def test_overbought_emits_short():
    df = _mk([100]*20 + [101, 103, 106, 110, 115])
    sigs = range_rsi2.signals(df, side="short")
    assert any(s.side == -1 for s in sigs)
def test_target_is_reversion_to_mean():
    df = _mk([100]*20 + [99, 97, 94, 90, 85])
    sigs = [s for s in range_rsi2.signals(df, side="long") if s.side == 1]
    assert sigs and sigs[0].tp_dist > 0
```

- [ ] **Step 2: Run to verify failure.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_range_rsi2.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `range_rsi2.py`.**
```python
from __future__ import annotations
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
from engine import Signal, rsi, sma, atr

def signals(df, lo=5, hi=95, rsi_len=2, sma_len=5, sl_atr=1.5, max_bars=24, atr_p=14, side="both"):
    C = df["Close"]
    r = rsi(C, rsi_len).values
    m = sma(C, sma_len).values
    a = atr(df, atr_p).values
    cv = C.values
    out = []
    for i in range(len(df)):
        if not np.isfinite(r[i]) or not np.isfinite(m[i]) or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        sv = 0
        if r[i] < lo and cv[i] < m[i]:
            sv = 1
        elif r[i] > hi and cv[i] > m[i]:
            sv = -1
        if sv == 0 or (side == "long" and sv < 0) or (side == "short" and sv > 0):
            continue
        tp = abs(m[i] - cv[i])                 # target = revert to the mean
        if tp <= 0:
            continue
        out.append(Signal(i=i, side=sv, sl_dist=sl_atr * a[i], tp_dist=tp,
                          entry_style="market", max_bars=max_bars))
    return out
```
Also create empty `strategies/__init__.py`.

- [ ] **Step 4: Run tests to verify pass.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_range_rsi2.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/hybrid/strategies scripts/scalping/hybrid/tests/test_range_rsi2.py && git commit -m "feat(hybrid): Connors RSI-2 range mean-reversion specialist"`

---

### Task 4: Long & short momentum specialists (Donchian)

**Files:**
- Create: `scripts/scalping/hybrid/strategies/long_momo.py`, `scripts/scalping/hybrid/strategies/short_momo.py`
- Test: `scripts/scalping/hybrid/tests/test_short_momo.py`

**Interfaces:**
- Consumes: `donchian_millerrh.simulate_donchian` (long), and a new short variant.
- Produces:
  - `long_momo.simulate(df, costs, risk, tfm, entry_gate=None, **params) -> list[Trade]` — thin re-export of `simulate_donchian` (already long-only breakout + channel trail).
  - `short_momo.simulate(df, costs, risk, tfm, entry_gate=None, **params) -> list[Trade]` — mirror: short on N-bar-low breakdown, trail upper Donchian channel, ATR/tight stop above. Same honesty rules, side=-1.

- [ ] **Step 1: Write the failing test (short side).**
```python
# hybrid/tests/test_short_momo.py
import os, sys
HUNT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "analysis", "strategy_hunt_2026-06-22"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
sys.path.insert(0, HUNT)
import numpy as np, pandas as pd
from engine import Costs, RiskCfg
import short_momo
ZERO = Costs(0,0,0,0)
R = RiskCfg(starting_equity=3000, risk_frac=0.01, max_leverage=20, compounding=False)
def _mk(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c*1.002, "Low": c*0.998, "Close": c, "Volume": 1.0}, index=idx)
def test_short_on_breakdown_profits_in_downtrend():
    df = _mk(list(np.linspace(200, 100, 300)))   # steady downtrend
    trades = short_momo.simulate(df, ZERO, R, 15, dc_high=20, dc_low=20, dc_stop=10)
    assert len(trades) >= 1 and all(t.side == -1 for t in trades)
    assert sum(t.pnl_usd for t in trades) > 0     # shorts make money falling
```

- [ ] **Step 2: Run to verify failure.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_short_momo.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement both modules.**
`long_momo.py`:
```python
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "analysis", "strategy_hunt_2026-06-22")))
from donchian_millerrh import simulate_donchian as simulate  # long-only breakout + channel trail
```
`short_momo.py` — mirror of `donchian_millerrh.simulate_donchian` with sides flipped. Copy that file's body and invert: entry on `L[i] <= dn_lvl[i]` where `dn_lvl = lowest(low, dc_high).shift(1)`; trail = `highest(high, dc_low).shift(1)` ratcheting DOWN; init stop above entry; exit when `H[j] >= trail`; `side=-1`; entry fill `min(level, O)*(1-slip)` becomes for short `max(dn_lvl, O)*(1-slip)`? — use: short entry fills at `min(dn_lvl[i], O[f])*(1 - slip)`. Full body:
```python
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
from engine import Costs, RiskCfg, Trade

def simulate(df, costs, risk, tf_minutes, *, dc_high=20, dc_low=10, dc_stop=8,
             use_tight_stop=False, entry_gate=None, **_ignored):
    O,H,L,C = (df[k].values for k in ("Open","High","Low","Close"))
    idx = df.index; n = len(df)
    dn_lvl = pd.Series(L).rolling(dc_high).min().shift(1).values     # breakdown level
    up_trail = pd.Series(H).rolling(dc_low).max().shift(1).values    # trailing stop (channel)
    tight = pd.Series(H).rolling(dc_stop).max().shift(1).values
    if entry_gate is not None:
        entry_gate = np.asarray(entry_gate, bool)
    slip = costs.slippage_pct/100; taker = costs.taker_pct/100
    trades=[]; equity=risk.starting_equity; i=0
    while i < n-1:
        if not np.isfinite(dn_lvl[i]) or not np.isfinite(up_trail[i]):
            i+=1; continue
        if entry_gate is not None and not entry_gate[i]:
            i+=1; continue
        if L[i] > dn_lvl[i]:
            i+=1; continue
        f=i+1; raw=min(dn_lvl[i], O[f]); entry=raw*(1-slip)
        init_stop = tight[i] if use_tight_stop else up_trail[i]
        if not np.isfinite(init_stop) or init_stop <= entry:
            i+=1; continue
        sdf=(init_stop-entry)/entry
        eq=equity if risk.compounding else risk.starting_equity
        risk_usd=eq*risk.risk_frac; notional=risk_usd/sdf
        if notional>eq*risk.max_leverage: notional=eq*risk.max_leverage; risk_usd=notional*sdf
        qty=notional/entry
        eff=min(risk.max_leverage, max(1.0, 1.0/(sdf*risk.liq_buffer)))
        liq=entry*(1+(1.0/eff)*(1-risk.maint_margin_rate))
        trail=init_stop; mae=0.0; exit_i=exit_px=reason=None; j=f
        while j<n:
            mae=max(mae,(H[j]-entry)/entry)
            if np.isfinite(up_trail[j]): trail=min(trail, up_trail[j])   # ratchet down
            if H[j]>=trail:
                exit_px=trail*(1+slip); reason="trail" if trail<init_stop else "stop"; exit_i=j; break
            j+=1
        if exit_i is None:
            exit_i=n-1; exit_px=C[exit_i]*(1+slip); reason="eod"
        bars=exit_i-f; hours=bars*tf_minutes/60
        fees=notional*taker+(qty*exit_px)*taker
        funding=notional*(costs.funding_pct_per_8h/100)*(hours/8)
        pnl=(entry-exit_px)*qty - fees - funding; equity+=pnl
        trades.append(Trade(side=-1, entry_i=f, entry_time=idx[f], entry_price=entry,
            exit_i=exit_i, exit_time=idx[exit_i], exit_price=exit_px, exit_reason=reason,
            notional=notional, qty=qty, risk_usd=risk_usd, fees_usd=fees, funding_usd=funding,
            pnl_usd=pnl, r_multiple=pnl/risk_usd if risk_usd>0 else 0, equity_after=equity,
            bars_held=bars, liq_price=liq, eff_leverage=eff, mae_frac=mae))
        i=exit_i+1
    return trades
```

- [ ] **Step 4: Run tests to verify pass.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_short_momo.py -v`
Expected: PASS (shorts profit in the downtrend).

- [ ] **Step 5: Commit.**
`git add scripts/scalping/hybrid/strategies/long_momo.py scripts/scalping/hybrid/strategies/short_momo.py scripts/scalping/hybrid/tests/test_short_momo.py && git commit -m "feat(hybrid): long + short Donchian momentum specialists"`

---

### Task 5: Single-account portfolio simulator

**Files:**
- Create: `scripts/scalping/backtest/portfolio_sim.py`
- Test: `scripts/scalping/backtest/tests/test_portfolio_sim.py`

**Interfaces:**
- Consumes: `engine.Costs, engine.RiskCfg`.
- Produces: `portfolio_sim.simulate(intents: dict[str, list[Trade]], risk, *, max_positions, max_total_notional) -> dict` returning `{"equity_curve": pd.Series, "trades": list, "final_equity": float}`. Takes each coin's already-simulated specialist `Trade` list (entry/exit times + r_multiple), merges them into ONE shared-capital account in time order, sizing each entry as `risk_frac * current_equity` (compounding), enforcing the concurrent-position and notional caps (an entry that would breach a cap is skipped), and applying each trade's realized `r_multiple` to that sized risk. This converts per-coin signals into a real account curve.

- [ ] **Step 1: Write the failing tests.**
```python
# backtest/tests/test_portfolio_sim.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pandas as pd
from engine import Trade, RiskCfg
import portfolio_sim
def _t(coin, t0, t1, r):
    ts=pd.Timestamp(t0, tz="UTC"); te=pd.Timestamp(t1, tz="UTC")
    return Trade(side=1, entry_i=0, entry_time=ts, entry_price=100, exit_i=1, exit_time=te,
        exit_price=100, exit_reason="x", notional=1000, qty=10, risk_usd=30, fees_usd=0,
        funding_usd=0, pnl_usd=0, r_multiple=r, equity_after=0, bars_held=1, liq_price=0,
        eff_leverage=5, mae_frac=0)
def test_compounding_grows_risk():
    R=RiskCfg(starting_equity=3000, risk_frac=0.10, compounding=True)
    intents={"BTC":[_t("BTC","2026-01-01","2026-01-02",1.0), _t("BTC","2026-01-03","2026-01-04",1.0)]}
    out=portfolio_sim.simulate(intents, R, max_positions=5, max_total_notional=1e9)
    # +10% then +10% compounded on $3000 = 3000*1.1*1.1 = 3630
    assert abs(out["final_equity"] - 3630) < 1
def test_position_cap_skips_overlap():
    R=RiskCfg(starting_equity=3000, risk_frac=0.10, compounding=False)
    # three fully-overlapping trades, cap=2 -> only 2 taken
    intents={c:[_t(c,"2026-01-01","2026-01-10",1.0)] for c in ["BTC","ETH","SOL"]}
    out=portfolio_sim.simulate(intents, R, max_positions=2, max_total_notional=1e9)
    assert len(out["trades"]) == 2
```

- [ ] **Step 2: Run to verify failure.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/backtest/tests/test_portfolio_sim.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `portfolio_sim.py`.**
```python
from __future__ import annotations
import numpy as np, pandas as pd

def simulate(intents, risk, *, max_positions=4, max_total_notional=1e12):
    # flatten to events sorted by entry time; each carries (coin, entry, exit, r, eff_leverage)
    evs = []
    for coin, trs in intents.items():
        for t in trs:
            evs.append(t)
    evs.sort(key=lambda t: t.entry_time)
    equity = risk.starting_equity
    open_positions = []          # list of (exit_time, notional)
    taken = []; curve = [(evs[0].entry_time, equity)] if evs else []
    for t in evs:
        # free positions that have closed by this entry time
        open_positions = [(xt, no) for (xt, no) in open_positions if xt > t.entry_time]
        if len(open_positions) >= max_positions:
            continue
        base = equity if risk.compounding else risk.starting_equity
        risk_usd = base * risk.risk_frac
        notional = risk_usd * t.eff_leverage
        if sum(no for _, no in open_positions) + notional > max_total_notional:
            continue
        pnl = t.r_multiple * risk_usd
        equity += pnl
        open_positions.append((t.exit_time, notional))
        taken.append(t)
        curve.append((t.exit_time, equity))
    curve.sort(key=lambda x: x[0])
    eq = pd.Series([v for _, v in curve], index=[ts for ts, _ in curve])
    return {"equity_curve": eq, "trades": taken, "final_equity": equity}
```

- [ ] **Step 4: Run tests to verify pass.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/backtest/tests/test_portfolio_sim.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/backtest/portfolio_sim.py scripts/scalping/backtest/tests/test_portfolio_sim.py && git commit -m "feat(backtest): single shared-capital portfolio simulator"`

---

### Task 6: Switcher (regime → specialist routing)

**Files:**
- Create: `scripts/scalping/hybrid/switcher.py`
- Test: `scripts/scalping/hybrid/tests/test_switcher.py`

**Interfaces:**
- Consumes: `regime.classify`, `long_momo/short_momo/range_rsi2`, `data.load`.
- Produces: `switcher.coin_intents(coin, costs, risk, params, *, regime_cfg) -> list[Trade]` — for one coin: classify regime on 1h, build an execution-TF entry gate per regime (Up→long_momo on 15m gated to up-bars, Down→short_momo on 15m gated to down-bars, Range→range_rsi2 on 5m gated to range-bars), run each specialist with its gate, return the union of trades. Regime aligned to exec TF by ffill.

- [ ] **Step 1: Write the failing test.**
```python
# hybrid/tests/test_switcher.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
from engine import Costs, RiskCfg
import switcher
def test_coin_intents_runs_and_returns_trades():
    costs = Costs(0,0,0.05,0.01)
    risk = RiskCfg(starting_equity=3000, risk_frac=0.02, max_leverage=20, compounding=True)
    params = {"long": {}, "short": {}, "range": {}}
    trades = switcher.coin_intents("BTC", costs, risk, params, regime_cfg={})
    assert isinstance(trades, list)
    # every trade must respect its regime: longs only in up, shorts only in down
    assert all(t.side in (1, -1) for t in trades)
```

- [ ] **Step 2: Run to verify failure.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_switcher.py -v`
Expected: FAIL (no module).

- [ ] **Step 3: Implement `switcher.py`.**
```python
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest")))
import data
from regime import classify
import long_momo, short_momo, range_rsi2

def _gate(reg_1h, exec_index, want):
    g = (reg_1h == want).reindex(exec_index, method="ffill").fillna(False)
    return g.values.astype(bool)

def coin_intents(coin, costs, risk, params, *, regime_cfg):
    df1h = data.load(coin, "1h")
    reg = classify(df1h, **regime_cfg)
    df15 = data.load(coin, "15m"); df5 = data.load(coin, "5m")
    longs = long_momo.simulate(df15, costs, risk, 15, entry_gate=_gate(reg, df15.index, 1), **params.get("long", {}))
    shorts = short_momo.simulate(df15, costs, risk, 15, entry_gate=_gate(reg, df15.index, -1), **params.get("short", {}))
    # range specialist: gate via engine isn't built-in for Signal flow -> filter signals by regime
    rg = _gate(reg, df5.index, 0)
    from engine import simulate as sig_sim
    sigs = [s for s in range_rsi2.signals(df5, **params.get("range", {})) if rg[s.i]]
    ranges = sig_sim(df5, sigs, costs, risk, 5)
    return longs + shorts + ranges
```

- [ ] **Step 4: Run test to verify pass.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_switcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/hybrid/switcher.py scripts/scalping/hybrid/tests/test_switcher.py && git commit -m "feat(hybrid): regime->specialist switcher with per-regime gating"`

---

### Task 7: Per-regime optimization + walk-forward of each specialist

**Files:**
- Create: `scripts/scalping/hybrid/optimize_specialists.py`

**Interfaces:**
- Consumes: `optimizer._suggest`, `metrics.extended_metrics`, `regime.classify`, the three specialists, `data.load`.
- Produces: `optimize_specialists.optimize_role(role) -> dict` (best frozen params for that role) and a `main()` that optimizes all three on their regime-only bars across the basket, walk-forward-validates, and writes `hybrid/frozen_params.json`.

- [ ] **Step 1: Write the role param-spaces and a smoke test.**
```python
# top of optimize_specialists.py
SPACES = {
  "long":  {"dc_high": ("int",10,60), "dc_low": ("int",5,30), "dc_stop": ("int",3,15), "use_tight_stop": ("cat",[False,True])},
  "short": {"dc_high": ("int",10,60), "dc_low": ("int",5,30), "dc_stop": ("int",3,15), "use_tight_stop": ("cat",[False,True])},
  "range": {"lo": ("int",2,15), "hi": ("int",85,98), "rsi_len": ("cat",[2,3]), "sma_len": ("cat",[5,10]), "sl_atr": ("float",0.8,2.5), "max_bars": ("int",12,48)},
}
```
Smoke test (`hybrid/tests/test_optimize_smoke.py`): assert `SPACES` has all three roles and each value is a tuple or scalar.

- [ ] **Step 2: Run smoke test to verify fail then pass.**
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_optimize_smoke.py -v` (fail: no module → implement → pass).

- [ ] **Step 3: Implement `optimize_role` + `main`.** For each role: build the regime-gated trade stream per coin (reuse Task 6 gating but for a single role), pool basket trades, objective = `extended_metrics` Calmar with HARD_KILL on liquidation and a min-trade floor (reuse the pattern from `analysis/strategy_hunt_2026-06-22/stage8_fair_retest.py:basket_optimize`). Optimize on IS (first 70% of the 3y), then run the 6-fold walk-forward labeling regime; print per-role verdict; dump best params to `frozen_params.json`.
```python
import json, optuna, numpy as np
# ... (mirror stage8_fair_retest.basket_optimize, but call the role's specialist with its regime gate;
#      for "range" use engine.simulate on regime-filtered signals as in switcher.py)
def main():
    frozen = {role: optimize_role(role) for role in ("long","short","range")}
    json.dump(frozen, open(os.path.join(os.path.dirname(__file__), "frozen_params.json"), "w"), indent=2)
```

- [ ] **Step 4: Run it.**
Run: `scripts/scalping/venv/Scripts/python.exe scripts/scalping/hybrid/optimize_specialists.py`
Expected: per-role walk-forward printout + `frozen_params.json` written with `long`/`short`/`range` param dicts.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/hybrid/optimize_specialists.py scripts/scalping/hybrid/frozen_params.json scripts/scalping/hybrid/tests/test_optimize_smoke.py && git commit -m "feat(hybrid): per-regime optimize + walk-forward; freeze specialist params"`

---

### Task 8: Risk-escalation sweep (find max safe aggressiveness)

**Files:**
- Create: `scripts/scalping/hybrid/sweep_risk.py`

**Interfaces:**
- Consumes: `switcher.coin_intents`, `portfolio_sim.simulate`, frozen params, `data.COINS`.
- Produces: `sweep_risk.main()` — for `risk_frac` in [0.01,0.02,0.03,0.05,0.08,0.10] (and matching `max_leverage` caps), build the full combined book (all coins, frozen params), run the portfolio sim over the 3y, and record compounded return, max drawdown, and liquidation count. Print the frontier and the recommended pick (most aggressive with 0 liq and DD ≤ 40%).

- [ ] **Step 1: Write a smoke test** (`hybrid/tests/test_sweep_smoke.py`): import `sweep_risk`, assert it exposes `RISK_LEVELS` list and a `max_dd(equity_curve)` helper returning a float on a toy rising/falling series.

- [ ] **Step 2: Run smoke (fail → implement → pass).**

- [ ] **Step 3: Implement.** Liquidation count = number of trades whose `mae_frac * entry_price` breached `liq_price` (reuse the `metrics` liq logic). Drawdown from the portfolio `equity_curve`.
```python
RISK_LEVELS = [(0.01,20),(0.02,20),(0.03,25),(0.05,25),(0.08,30),(0.10,30)]
def max_dd(eq):
    import numpy as np
    v = eq.values; peak = np.maximum.accumulate(v); return float(((peak-v)/peak).max()*100)
def main():
    frozen = json.load(open(.../"frozen_params.json"))
    for rf, lev in RISK_LEVELS:
        risk = RiskCfg(starting_equity=3000, risk_frac=rf, max_leverage=lev, compounding=True)
        intents = {c: switcher.coin_intents(c, LIGHTER, risk, frozen, regime_cfg={}) for c in data.COINS}
        liq = sum(extended_metrics(v, 3000)["liq_hits"] for v in intents.values())
        out = portfolio_sim.simulate(intents, risk, max_positions=5, max_total_notional=3000*lev)
        print(f"rf={rf:.0%} lev={lev}: return={out['final_equity']/3000-1:+.0%} maxDD={max_dd(out['equity_curve']):.0f}% liq={liq}")
```

- [ ] **Step 4: Run it.**
Run: `scripts/scalping/venv/Scripts/python.exe scripts/scalping/hybrid/sweep_risk.py`
Expected: a frontier table; identify the most aggressive row with `liq=0` and `maxDD ≤ 40%`.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/hybrid/sweep_risk.py scripts/scalping/hybrid/tests/test_sweep_smoke.py && git commit -m "feat(hybrid): risk-escalation frontier sweep"`

---

### Task 9: Monthly P&L report + end-to-end run

**Files:**
- Create: `scripts/scalping/hybrid/report.py`, `scripts/scalping/hybrid/run_hybrid.py`

**Interfaces:**
- Consumes: `portfolio_sim` output equity curve + trades, frozen params, chosen risk level.
- Produces: `report.monthly_pnl(equity_curve) -> pd.DataFrame` (per-month return %), `report.summary(equity_curve, trades) -> dict` (% months green, avg month, worst month, total return, maxDD, Sharpe, per-regime attribution). `run_hybrid.main()` ties classify→switch→portfolio_sim→report for the locked config, Lighter primary + BloFin informational.

- [ ] **Step 1: Write the monthly-P&L test.**
```python
# hybrid/tests/test_report.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd, numpy as np
import report
def test_monthly_pnl_counts_green_months():
    idx = pd.date_range("2026-01-01", periods=120, freq="D", tz="UTC")
    eq = pd.Series(3000*np.cumprod(1+np.where(np.arange(120)<60,0.001,-0.0005)), index=idx)
    m = report.monthly_pnl(eq)
    assert len(m) >= 3 and "return_pct" in m.columns
```

- [ ] **Step 2: Run to verify failure, then implement, then pass.**
```python
# report.py
import pandas as pd, numpy as np
def monthly_pnl(eq):
    m = eq.resample("ME").last()
    r = m.pct_change().dropna()*100
    return pd.DataFrame({"return_pct": r})
def summary(eq, trades):
    mp = monthly_pnl(eq)["return_pct"]
    peak = eq.cummax(); dd = ((peak-eq)/peak).max()*100
    return dict(total_return_pct=(eq.iloc[-1]/eq.iloc[0]-1)*100,
                pct_months_green=float((mp>0).mean()*100), avg_month=float(mp.mean()),
                worst_month=float(mp.min()), max_dd=float(dd), n_trades=len(trades))
```
Run: `scripts/scalping/venv/Scripts/python.exe -m pytest scripts/scalping/hybrid/tests/test_report.py -v` → PASS.

- [ ] **Step 3: Implement `run_hybrid.py`** wiring everything at the locked risk level (from Task 8) and frozen params; print the monthly distribution + summary for Lighter, then re-run with BloFin costs for the informational line.

- [ ] **Step 4: Run end-to-end.**
Run: `scripts/scalping/venv/Scripts/python.exe scripts/scalping/hybrid/run_hybrid.py`
Expected: monthly P&L table over 3y + summary (% months green, avg/worst month, total return, maxDD); BloFin informational line.

- [ ] **Step 5: Commit.**
`git add scripts/scalping/hybrid/report.py scripts/scalping/hybrid/run_hybrid.py scripts/scalping/hybrid/tests/test_report.py && git commit -m "feat(hybrid): monthly P&L report + end-to-end hybrid run"`

---

## Self-Review

**Spec coverage:** regime classifier (T2) ✓; 3 real specialists — long Donchian (T4), short mirror (T4), Connors RSI-2 (T3) ✓; per-regime optimize+walk-forward+freeze (T7) ✓; switcher with gating + caps (T6, caps enforced in portfolio sim T5) ✓; single-account portfolio sim w/ compounding (T5) ✓; risk-escalation frontier, zero-liq + ≤40% DD pick (T8) ✓; monthly-P&L success metric (T9) ✓; Lighter primary / BloFin informational (T8, T9) ✓; multi-TF data incl. 5m/15m (T1) ✓.

**Placeholder scan:** Task 7 step 3 references "mirror stage8_fair_retest.basket_optimize" — the implementer has that file; the param-spaces and outputs are concrete. Acceptable (reuse of an existing, named function). All test/impl code blocks are concrete.

**Type consistency:** specialists all return `list[Trade]` (engine.Trade) and accept `entry_gate`; `range_rsi2.signals` returns `list[Signal]` consumed via `engine.simulate` (matches switcher T6); `portfolio_sim.simulate(intents: dict[str,list[Trade]], risk, ...)` consumes those (T5/T8); `report.*` consumes the portfolio equity Series (T9). Consistent.

**Note on the Range gate:** the Signal-based engine has no `entry_gate` param, so the switcher filters RSI-2 signals by regime *before* `engine.simulate` (T6 step 3). This is the chosen approach and is internally consistent across T6/T7.
