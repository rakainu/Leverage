"""1h day-trade test — does 'regime gate + let winners run' flip the edge positive?

Tests Rich's theory (2026-06-15): the ENTRIES are fine; the bridge loses because
it (a) takes trend entries in chop (no regime gate) and (b) caps winners ~1R while
taking full stops. On 1h, day-trade horizon (24h time stop, NOT swing), compare a
clean 2x2 per coin:

    gate {OFF, ON}  x  exit {CAPPED ~1:1, RUN scale-out+trail}

Two entry families (pullback EMA20-resume, breakout 20-bar) so the verdict isn't
entry-specific. Honest fills via btengine (no lookahead, stop wins both-hit bars,
BloFin perp costs). IS/OOS 70/30 to catch overfit.

Data: OKX USDT-perp 5m cached -> resampled to 1h. SOL/ETH/HYPE/BTC.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scalp_search_2026-05-30", "data"))
sys.path.insert(0, ENGINE_DIR)
import btengine as bt  # noqa: E402

COINS = ["SOL", "ETH", "HYPE", "BTC"]
TF_MIN = 60


def load_1h(symbol: str) -> pd.DataFrame:
    df5 = pd.read_parquet(os.path.join(DATA_DIR, f"okx_{symbol}_5m.parquet"))
    df = df5.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                 "Close": "last", "Volume": "sum"}).dropna()
    return df.astype(float)


def prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = bt.ema(df["Close"], 20)
    df["ema50"] = bt.ema(df["Close"], 50)
    df["ema200"] = bt.ema(df["Close"], 200)
    df["atr"] = bt.atr(df, 14)
    df["slope200"] = df["ema200"] - df["ema200"].shift(20)  # 20-bar EMA200 slope
    df["hh20"] = df["High"].rolling(20).max().shift(1)       # prior-20 high (no lookahead)
    df["ll20"] = df["Low"].rolling(20).min().shift(1)
    return df


def regime_ok(row, side: int) -> bool:
    """Higher-timeframe trend agreement: EMA50 vs EMA200 + EMA200 slope direction."""
    if side > 0:
        return row.ema50 > row.ema200 and row.slope200 > 0
    return row.ema50 < row.ema200 and row.slope200 < 0


def gen_signals(df: pd.DataFrame, entry: str, gate: bool, exit_mode: str) -> list[bt.Signal]:
    C = df["Close"].values; A = df["atr"].values
    e20 = df["ema20"].values
    hh = df["hh20"].values; ll = df["ll20"].values
    sigs: list[bt.Signal] = []
    rows = list(df.itertuples())
    for i in range(200, len(df) - 1):
        atr_i = A[i]
        if not np.isfinite(atr_i) or atr_i <= 0:
            continue
        side = 0
        if entry == "pullback":  # close re-crosses EMA20 = pullback resume
            if C[i] > e20[i] and C[i - 1] <= e20[i - 1]:
                side = +1
            elif C[i] < e20[i] and C[i - 1] >= e20[i - 1]:
                side = -1
        elif entry == "breakout":  # close breaks prior 20-bar extreme
            if C[i] > hh[i]:
                side = +1
            elif C[i] < ll[i]:
                side = -1
        if side == 0:
            continue
        if gate and not regime_ok(rows[i], side):
            continue

        if exit_mode == "capped":   # ~1:1, no trail, the current losing style
            sig = bt.Signal(i=i, side=side, sl_dist=1.0 * atr_i, tp_dist=1.0 * atr_i,
                            entry_style="market", max_bars=24)
        else:                        # RUN: wide stop, scale 50% at 1R, BE runner, wide trail
            sig = bt.Signal(i=i, side=side, sl_dist=1.5 * atr_i, tp_dist=1.5 * atr_i,
                            entry_style="market", max_bars=24,
                            tp1_frac=0.5, be_after_tp1=True, trail_atr=3.0 * atr_i)
        sigs.append(sig)
    return sigs


def run_one(df: pd.DataFrame, entry: str, gate: bool, exit_mode: str):
    costs = bt.Costs()              # BloFin perp default (taker .06 / maker .02 / slip .05)
    risk = bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, compounding=False)
    sigs = gen_signals(df, entry, gate, exit_mode)
    return bt.simulate(df, sigs, costs, risk, TF_MIN)


def agg_metrics(trades, weeks):
    m = bt.metrics(trades, 1000.0)
    m["tpw"] = m["n"] / weeks if weeks else 0
    if trades:
        m["avg_hold_h"] = np.mean([t.bars_held for t in trades])
    else:
        m["avg_hold_h"] = 0.0
    return m


def fmt_row(label, m):
    pf = m["profit_factor"]
    pfs = "inf" if pf == float("inf") else f"{pf:5.2f}"
    return (f"{label:<26} n={m['n']:>4} PF={pfs} WR={m['win_rate']:4.0f}% "
            f"avgR={m['avg_r']:+.3f} net={m['net_pct']:+6.1f}% maxDD={m['max_dd_pct']:4.1f}% "
            f"tpw={m['tpw']:4.1f} hold={m['avg_hold_h']:4.1f}h")


CONFIGS = [
    ("gateOFF capped", False, "capped"),
    ("gateON  capped", True, "capped"),
    ("gateON  RUN", True, "run"),
    ("gateOFF RUN", False, "run"),
]


def main():
    data = {c: prep(load_1h(c)) for c in COINS}
    span_days = (list(data.values())[0].index.max() - list(data.values())[0].index.min()).days
    weeks = span_days / 7.0
    print(f"# 1h day-trade test | {COINS} | {span_days}d (~{weeks:.0f}wk) | BloFin costs | 24h time-stop")
    print(f"# IS/OOS = 70/30 chronological. Risk 1%/trade, no compounding.\n")

    for entry in ("pullback", "breakout"):
        print(f"\n{'='*96}\nENTRY = {entry.upper()}\n{'='*96}")
        for label, gate, exit_mode in CONFIGS:
            # pooled basket: concat trades across coins, full window
            pooled = []
            per_coin = {}
            is_pf, oos_pf = [], []
            for c in COINS:
                df = data[c]
                tr = run_one(df, entry, gate, exit_mode)
                per_coin[c] = agg_metrics(tr, weeks)
                pooled += tr
                # IS/OOS
                dfi, dfo = bt.split_is_oos(df, 0.70)
                tri = run_one(dfi, entry, gate, exit_mode)
                tro = run_one(dfo, entry, gate, exit_mode)
                mi, mo = bt.metrics(tri, 1000.0), bt.metrics(tro, 1000.0)
                if mi["n"] >= 10:
                    is_pf.append(mi["profit_factor"])
                if mo["n"] >= 10:
                    oos_pf.append(mo["profit_factor"])
            pm = agg_metrics(pooled, weeks * len(COINS))
            print("\n  " + fmt_row(f"[POOLED] {label}", pm))
            for c in COINS:
                print("      " + fmt_row(c, per_coin[c]))
            def avg(x):
                x = [v for v in x if v != float("inf")]
                return sum(x) / len(x) if x else float("nan")
            print(f"      IS PF avg={avg(is_pf):.2f}  OOS PF avg={avg(oos_pf):.2f}  "
                  f"(per-coin, n>=10)")


if __name__ == "__main__":
    main()
