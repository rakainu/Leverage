"""Decide the V3.2 question: is the test engine garbage, is V3.2 a monster, or
is live leaking execution? Two read-only analyses over the exact live window.

A) ENTRY vs EXIT: replay each REAL live entry (symbol/side/price/time) through
   the engine's ideal trail on real BloFin bars. modeled≈actual ⇒ exit execution
   is faithful and any P&L gap is ENTRY selection; modeled≫actual ⇒ the live
   exit/fill is leaking.

B) SIGNAL THIS WINDOW: run the engine's own strategy (signals+filter+trail) over
   the same window and compare its net to live net. Strong+ ⇒ the live bridge
   isn't taking the trades the engine assumes (signal/fill gap, fixable). Flat/–
   ⇒ the 180d +$60k was regime, this window is genuinely bad (not garbage, just
   variance) — V3.2 needs more time, not a rewrite.

Run:
    PYTHONPATH="analysis;v3.1-drafts;analysis/sweeps/2026-05-20" \
        venv/Scripts/python.exe v3.2-analysis/replay_v32_live.py
"""
import sys
from dataclasses import replace

import pandas as pd

from engine import fetch_ohlcv
from zec_v3_realistic import (
    TrailParams, simulate_trade, generate_v3_signals, apply_entry_filter,
)
from v3_2_lab import run_bt, ExitModel, F_LIVE, base_params

LIVE = pd.read_csv("data/v32_live_trades.csv")
LIVE["opened_at"] = pd.to_datetime(LIVE["opened_at"], utc=True)
LIVE["closed_at"] = pd.to_datetime(LIVE["closed_at"], utc=True)
W0, W1 = LIVE["opened_at"].min(), LIVE["closed_at"].max()
COINS = sorted(LIVE["symbol"].unique())
print(f"live window: {W0} -> {W1}  ({(W1-W0).total_seconds()/86400:.2f}d), "
      f"{len(LIVE)} trades, coins={[c.split('-')[0] for c in COINS]}\n")

# Fetch bars once per coin (window + lookback for signal warmup), and
# precompute the engine's signal columns (run_bt needs buy_sig/ema9/adx/...).
BARS, SIG = {}, {}
for c in COINS:
    df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m",
                     days_back=12, exchange="blofin", cache=False, verbose=False)
    BARS[c] = df
    SIG[c] = apply_entry_filter(generate_v3_signals(df.copy()))
    print(f"  {c:10s} {len(df)} bars  ({df.index[0]} -> {df.index[-1]})", flush=True)

# ---- Analysis A: real live entries -> engine ideal trail (gross, no fees) ----
pA = replace(TrailParams(), sl_loss_usdt=82.5, commission_pct=0.0, sl_slippage_pct=0.0006)
recs = []
for _, t in LIVE.iterrows():
    df = BARS[t["symbol"]]
    fut = df[df.index >= t["opened_at"]].head(288)        # up to 1 day forward
    if len(fut) < 2:
        continue
    bars = [(int(ix.timestamp()), r.Open, r.High, r.Low, r.Close)
            for ix, r in fut.iterrows()]
    res = simulate_trade(t["side"], t["entry_price"], bars, pA, ordering="avg")
    recs.append(dict(symbol=t["symbol"], side=t["side"],
                     actual=t["pnl_usdt"], modeled=res.pnl_usdt,
                     unresolved=(res.exit_reason == "unresolved")))
A = pd.DataFrame(recs)
res_ok = A[~A["unresolved"]]
print("\n" + "=" * 64)
print("A) REAL LIVE ENTRIES -> ENGINE IDEAL TRAIL (gross, real bars)")
print("=" * 64)
def _blk(d, label):
    if d.empty:
        print(f"  {label:12s} n=0"); return
    print(f"  {label:12s} n={len(d):3d}  ACTUAL net={d.actual.sum():+8.0f} "
          f"avg={d.actual.mean():+6.2f}  |  MODELED net={d.modeled.sum():+8.0f} "
          f"avg={d.modeled.mean():+6.2f}  |  gap(mod-act)={d.modeled.sum()-d.actual.sum():+8.0f}")
_blk(res_ok, "ALL")
_blk(res_ok[res_ok.side == "long"], "LONG")
_blk(res_ok[res_ok.side == "short"], "SHORT")
if A["unresolved"].any():
    print(f"  ({A['unresolved'].sum()} recent trades dropped — not enough forward bars)")

# ---- Analysis B: engine's own strategy over the exact live window ----
print("\n" + "=" * 64)
print("B) ENGINE SIGNALS OVER THE SAME WINDOW (gross, zero-fee)")
print("=" * 64)
pB = base_params(sl=82.5, fee=0.0, slip=0.0006)
eng_net = live_net = 0.0
print(f"  {'coin':10s} {'eng_n':>5s} {'eng_net':>9s} {'eng_WR':>6s}   "
      f"{'live_n':>6s} {'live_net':>9s} {'live_WR':>7s}")
for c in COINS:
    tdf = run_bt(SIG[c], pB, ExitModel("trail"), F_LIVE)
    tdf["et"] = pd.to_datetime(tdf["entry_ts"], utc=True)
    win = tdf[(tdf["et"] >= W0) & (tdf["et"] <= W1)]
    lc = LIVE[LIVE["symbol"] == c]
    en = len(win); enet = float(win.pnl_net.sum()) if en else 0.0
    ewr = (win.pnl_net > 0).mean() * 100 if en else 0.0
    ln, lnet = len(lc), float(lc.pnl_usdt.sum())
    lwr = (lc.pnl_usdt > 0).mean() * 100 if ln else 0.0
    eng_net += enet; live_net += lnet
    print(f"  {c:10s} {en:5d} {enet:+9.0f} {ewr:5.0f}%   {ln:6d} {lnet:+9.0f} {lwr:6.0f}%")
print(f"  {'BASKET':10s} {'':5s} {eng_net:+9.0f}          {'':6s} {live_net:+9.0f}")
print("\nNOTE: engine entry = exact EMA9 (entry_price=ema[i]); live fills at "
      "market on the retest tick. Gap between B-engine and live = entry-fill "
      "realism + signal/trade selection. A isolates exits from real entries.")
