"""Phase 1 — quantify WHY live's retest diverges from the engine's, to pick the
fix. Two measured suspects over the live window's engine entries:

(a) EMA9 PARITY: engine uses calc_ema over the FULL series (deep warmup); live's
    poller recomputes EMA9 from only ~20 fetched bars (limit=max(period+10,20)).
    How far apart is the EMA9 *value* the retest is measured against?

(b) RETEST TRIGGER: engine fires when the bar LOW/HIGH touches EMA9 (full intrabar
    range), filling at EMA9. A live tick-poller only sees sampled prices. Of the
    engine's fills, how many are "wick-only" — the bar wicked to EMA9 but CLOSED
    outside the retest band — i.e. a fill you can only catch intrabar (→ needs a
    resting EMA9 limit, Plan A), vs "close-also" ones a bar-close check would get.

(c) OVERSHOOT: how many candidate touches blow >0.2% past EMA9 (engine rejects).

Run:
    PYTHONPATH="analysis;v3.1-drafts;analysis/sweeps/2026-05-20" \
        venv/Scripts/python.exe v3.2-analysis/phase1_diag.py
"""
import numpy as np
import pandas as pd

from engine import fetch_ohlcv, calc_ema
from zec_v3_realistic import (
    generate_v3_signals, apply_entry_filter, _check_retest,
    EMA_PERIOD, RETEST_OVERSHOOT_PCT, RETEST_TIMEOUT_BARS, MIN_SLOPE_PCT,
)

LIVE = pd.read_csv("data/v32_live_trades.csv")
LIVE["opened_at"] = pd.to_datetime(LIVE["opened_at"], utc=True)
W0, W1 = LIVE["opened_at"].min(), LIVE["opened_at"].max()
COINS = sorted(LIVE["symbol"].unique())
LIVE_LIMIT = max(EMA_PERIOD + 10, 20)     # what the poller fetches each tick


def live_style_ema(closes: np.ndarray, idx: int) -> float:
    """EMA9 as the poller computes it: only the last LIVE_LIMIT closed bars."""
    lo = max(0, idx - LIVE_LIMIT + 1)
    seg = closes[lo:idx + 1]
    return float(calc_ema(pd.Series(seg), EMA_PERIOD).values[-1])


ema_diffs = []
wick_only = close_also = overshoot_rej = 0
n_fills = 0
for c in COINS:
    df = fetch_ohlcv(c.replace("-USDT", "/USDT:USDT"), timeframe="5m", days_back=12,
                     exchange="blofin", cache=False, verbose=False)
    sig = apply_entry_filter(generate_v3_signals(df.copy()))
    ema = sig["ema9"].values
    low = sig["Low"].values; high = sig["High"].values; close = sig["Close"].values
    slope = sig["slope_pct"].values
    closes = sig["Close"].values
    buy = sig["buy_sig"].values; sell = sig["sell_sig"].values
    ts = sig.index
    pending = []
    for i in range(len(sig)):
        keep = []
        for sig_i, side in pending:
            if i - sig_i > RETEST_TIMEOUT_BARS:
                continue
            in_win = (ts[i] >= W0) and (ts[i] <= W1)
            fires = _check_retest(side, ema[i], low[i], high[i])
            if fires and abs(slope[i]) >= MIN_SLOPE_PCT and in_win:
                n_fills += 1
                # EMA parity at the fill bar
                le = live_style_ema(closes, i)
                ema_diffs.append(abs(le - ema[i]) / ema[i] * 100.0)
                # wick-only vs close-also (would a bar-close price be in the band?)
                close_fires = _check_retest(side, ema[i], close[i], close[i])
                if close_fires:
                    close_also += 1
                else:
                    wick_only += 1
                # did the bar blow past the 0.2% overshoot floor at all?
                ov = ema[i] * RETEST_OVERSHOOT_PCT / 100.0
                pierced = (low[i] < ema[i] - ov) if side == "long" else (high[i] > ema[i] + ov)
                if pierced:
                    overshoot_rej += 1
                continue
            keep.append((sig_i, side))
        pending = keep
        if buy[i]:
            pending.append((i, "long"))
        if sell[i]:
            pending.append((i, "short"))

d = np.array(ema_diffs)
print(f"engine fills in window: {n_fills}\n")
print("=== (a) EMA9 PARITY: live 20-bar EMA9 vs engine full-warmup EMA9 ===")
print(f"  mean abs diff: {d.mean():.4f}%   median: {np.median(d):.4f}%   "
      f"max: {d.max():.4f}%   p90: {np.percentile(d,90):.4f}%")
print(f"  (retest band is ±{RETEST_OVERSHOOT_PCT}% — diffs near/above that scramble the trigger)")
print("\n=== (b) RETEST TRIGGER TYPE ===")
print(f"  wick-only (bar touched EMA9 but CLOSED outside band): {wick_only}/{n_fills}"
      f"  ({wick_only/n_fills*100:.0f}%)  <- only catchable intrabar (Plan A limit@EMA9)")
print(f"  close-also (close in band; a bar-close check would catch): {close_also}/{n_fills}"
      f"  ({close_also/n_fills*100:.0f}%)")
print(f"\n=== (c) bars piercing >0.2% past EMA9 (engine still fills off the wick touch): "
      f"{overshoot_rej}/{n_fills} ===")
