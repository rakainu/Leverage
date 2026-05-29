"""Honest scale-out replay over the REAL Pro V3 signals (ZEC + SOL).

Consumes pro_v3_signals.csv (the real, no-repaint webhook stream from extract.py) and
replays a configurable ATR-based scale-out exit over real 5m OHLCV.

Exit model (the user's "good entries, cut losers fast" thesis):
  - SL  = sl_atr  * ATR(14) at entry
  - TP1/TP2/TP3 = tp{n}_atr * ATR(14) at entry
  - scale out ratios r1/r2/r3 of the position at TP1/TP2/TP3
  - be_after_tp1: once TP1 fills, move the stop to breakeven -> a trade that pays TP1
    can no longer become a loser (this is the real loss-cutter, not just a tight SL).

Honesty conventions (inherited from sweeps/2026-05-20/strategy.py):
  - Entry fills at the NEXT bar open after the signal/fill timestamp (live market order).
  - TP = resting limit, fills on the favorable wick; SL = stop, fills on the adverse
    wick + measured slippage. Intrabar SL/TP ambiguity resolved conservatively (sl_first).
  - Dual fee profiles: BloFin 0.06%/side and Lighter 0% (the zero-fee deploy target).

entry_timing:
  "signal" — every raw webhook (enter at signal). Tests dropping the EMA9 retest.
  "retest" — only signals the live bridge actually filled (status=='filled'), entered at
             the recorded fill time. Tests keeping the EMA9 retest+slope gate.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SWEEPS = HERE.parent / "sweeps" / "2026-05-20"
sys.path.insert(0, str(SWEEPS))

from engine import load_symbol, calc_atr, calc_ema  # noqa: E402
from strategy import kpis  # noqa: E402  (reuse the audited KPI calc)

SIGNALS_CSV = HERE / "pro_v3_signals.csv"
SYMBOL_DATA = {"ZEC-USDT": "ZEC", "SOL-USDT": "SOL"}


@dataclass
class ExitParams:
    margin_usdt: float = 250.0
    leverage: float = 30.0
    sl_atr: float = 1.5
    tp1_atr: float = 1.0
    tp2_atr: float = 2.0
    tp3_atr: float = 3.0
    r1: float = 0.5
    r2: float = 0.25
    r3: float = 0.25
    be_after_tp1: bool = True
    sl_slippage_pct: float = 0.0006
    commission_pct: float = 0.0006     # set 0.0 for Lighter pass
    atr_period: int = 14
    max_bars: int = 288                 # 24h cap on a 5m trade
    intrabar: str = "sl_first"          # conservative resolution of same-bar SL+TP


# ---------- price prep ----------

def load_prices(symbol_key: str, days_back: int = 180) -> pd.DataFrame:
    df = load_symbol(SYMBOL_DATA[symbol_key], "5m", days_back=days_back)
    df = df.copy()
    df["atr"] = calc_atr(df, 14)
    df["ts"] = (df.index.astype("int64") // 1_000_000_000)
    return df


# ---------- single-trade simulation ----------

def _simulate(side: str, entry_idx: int, o, h, l, c, atr, p: ExitParams) -> dict:
    n = len(o)
    entry = float(o[entry_idx])
    a = float(atr[entry_idx])
    if not np.isfinite(a) or a <= 0:
        return {}
    long = side == "long"
    sl_dist = p.sl_atr * a
    tps = [p.tp1_atr * a, p.tp2_atr * a, p.tp3_atr * a]
    tp_px = [entry + d if long else entry - d for d in tps]
    ratios = [p.r1, p.r2, p.r3]

    sl_cur = entry - sl_dist if long else entry + sl_dist
    remaining = 1.0
    next_tp = 0
    notional = p.margin_usdt * p.leverage
    gross = 0.0
    fee = 0.0
    exits = []

    def book(r, price, reason):
        nonlocal gross, fee
        pct = (price - entry) / entry if long else (entry - price) / entry
        gross += r * pct * notional
        fee += r * (notional + (price / entry) * notional) * p.commission_pct
        exits.append((round(r, 4), round(price, 6), reason))

    end = min(entry_idx + 1 + p.max_bars, n)
    last_j = entry_idx
    for j in range(entry_idx + 1, end):
        last_j = j
        bh, bl = float(h[j]), float(l[j])
        adverse = (long and bl <= sl_cur) or (not long and bh >= sl_cur)
        tp_hits = []
        k = next_tp
        while k < 3 and ((long and bh >= tp_px[k]) or (not long and bl <= tp_px[k])):
            tp_hits.append(k)
            k += 1

        if adverse and tp_hits:
            if p.intrabar == "sl_first":
                tp_hits = []          # stop assumed first → takes all remaining
            elif p.intrabar == "tp_first":
                adverse = False       # take TPs this bar, stop may trigger later

        if tp_hits:
            for k in tp_hits:
                book(ratios[k], tp_px[k], f"tp{k+1}")
                remaining -= ratios[k]
                next_tp = k + 1
            if p.be_after_tp1 and next_tp >= 1:
                sl_cur = entry        # breakeven
            if remaining <= 1e-9:
                break

        if adverse and remaining > 1e-9:
            slip = entry * p.sl_slippage_pct
            exit_p = sl_cur - slip if long else sl_cur + slip
            reason = "sl_be" if (next_tp >= 1 and abs(sl_cur - entry) < 1e-9) else "sl"
            book(remaining, exit_p, reason)
            remaining = 0.0
            break

    if remaining > 1e-9:
        book(remaining, float(c[last_j]), "unresolved")

    pnl_net = gross - fee
    final_reason = exits[-1][2] if exits else "none"
    return {
        "side": side, "entry_idx": entry_idx, "entry_price": entry, "atr": a,
        "pnl_usdt": round(gross, 4), "pnl_net": round(pnl_net, 4),
        "exit_reason": final_reason, "n_partials": len(exits),
        "reached_tp1": int(next_tp >= 1), "reached_tp3": int(next_tp >= 3),
        "duration_bars": last_j - entry_idx, "exit_bar": last_j,
    }


# ---------- run over real signals ----------

def run_replay(signals: pd.DataFrame, prices: pd.DataFrame, p: ExitParams,
               entry_timing: str = "signal", reversal_mode: str = "ignore") -> pd.DataFrame:
    """Replay one symbol's real signals. signals: rows for ONE symbol, time-sorted."""
    o = prices["Open"].values; h = prices["High"].values
    l = prices["Low"].values; c = prices["Close"].values
    atr = prices["atr"].values; ts = prices["ts"].values
    n = len(o)

    if entry_timing == "retest":
        sig = signals[signals["status"] == "filled"].copy()
        time_col = "filled_at"
    else:
        sig = signals.copy()
        time_col = "created_at"
    sig = sig.dropna(subset=[time_col]).sort_values(time_col)
    sig_ts = (pd.to_datetime(sig[time_col], utc=True).astype("int64") // 1_000_000_000).values
    sides = np.where(sig["action"].values == "buy", "long", "short")

    trades = []
    busy_until = -1  # bar index until which we hold a position
    for st, side in zip(sig_ts, sides):
        entry_idx = int(np.searchsorted(ts, st, side="right"))  # next bar open after signal
        if entry_idx >= n:
            continue
        if entry_idx <= busy_until:
            # in a position: optionally flip flat on opposite signal
            continue
        tr = _simulate(side, entry_idx, o, h, l, c, atr, p)
        if not tr:
            continue
        tr["entry_ts"] = int(ts[entry_idx])
        trades.append(tr)
        busy_until = tr["exit_bar"]

    return pd.DataFrame(trades)


# ---------- smoke ----------

if __name__ == "__main__":
    print("=" * 72)
    print("PRO V3 REAL-SIGNAL SCALE-OUT REPLAY — smoke (baseline config)")
    print("=" * 72)
    allsig = pd.read_csv(SIGNALS_CSV)
    for sym in ["ZEC-USDT", "SOL-USDT"]:
        s = allsig[allsig["symbol"] == sym]
        px = load_prices(sym)
        for timing in ["signal", "retest"]:
            for fee_name, comm in [("blofin", 0.0006), ("lighter", 0.0)]:
                p = replace(ExitParams(), commission_pct=comm)
                tdf = run_replay(s, px, p, entry_timing=timing)
                k = kpis(tdf) if not tdf.empty else {"n": 0}
                if tdf.empty:
                    print(f"{sym} {timing:6} {fee_name:7}: no trades")
                    continue
                tp1 = tdf["reached_tp1"].mean()
                print(f"{sym} {timing:6} {fee_name:7}: n={k['n']:4} "
                      f"WR={k['win_rate']:.0%} net=${k['net_pnl']:8.0f} "
                      f"PF={k['profit_factor']:.2f} DD=${k['max_dd']:7.0f} "
                      f"tp1rate={tp1:.0%}")
