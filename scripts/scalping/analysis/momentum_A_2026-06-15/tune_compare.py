"""Head-to-head: baseline vs the tuning contenders, on the metrics that matter —
per-trade thickness (cushion above breakeven), robustness (OOS/June/2x-slip), and the
REAL outcome ($3k@2% weeks-to-2x + steady weekly skim at $10k base). No conservative
nudging; the table decides."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
from common import load, portfolio, weeks_span, LIGHTER, LIGHTER_2X, bt, TF_MIN
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scalp_search_2026-05-30")))
from strat_lib import regime_mr  # noqa: E402

BASKET = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
COMMON = dict(trend_len=200, slope_lb=20, z_period=30)
CONFIGS = {
    "BASELINE        ": dict(z_entry=1.5, tp_frac=0.3, limit_atr=0.25, sl_atr=2.0, max_bars=12),
    "SMOOTH sl3/mb24 ": dict(z_entry=1.5, tp_frac=0.3, limit_atr=0.25, sl_atr=3.0, max_bars=24),
    "MARKET entry    ": dict(z_entry=1.5, tp_frac=0.3, limit_atr=0.0,  sl_atr=2.0, max_bars=12),
    "FAT z3.0/tp0.5  ": dict(z_entry=3.0, tp_frac=0.5, limit_atr=0.25, sl_atr=3.0, max_bars=24),
}
START, BASE, CAP = 3000.0, 10000.0, 50000.0


def trades(dfs, cfg, costs):
    return {c: bt.simulate(df, regime_mr(df, side="both", **COMMON, **cfg), costs,
            bt.RiskCfg(1000.0, 0.01, max_leverage=10, liq_buffer=2.5, compounding=True),
            TF_MIN) for c, df in dfs.items()}


def cushion(per):
    rs = np.concatenate([[t.r_multiple for t in ts] for ts in per.values() if ts])
    w, l = rs[rs > 0], rs[rs < 0]
    wr = len(w) / len(rs); be = (-l.mean()) / (w.mean() - l.mean())
    return wr * 100, be * 100, (wr - be) * 100


def ride_and_skim(per):
    rows = sorted(((t.exit_time, t.r_multiple, t.risk_usd / t.notional)
                   for ts in per.values() for t in ts if t.notional > 0), key=lambda x: x[0])
    eq = START; reached = None; ledger = 0.0; skims = []; cw = None; t0 = rows[0][0]
    for tm, r, sf in rows:
        wk = tm.isocalendar()[:2]
        if cw is None:
            cw = wk
        if wk != cw:
            if eq > BASE:
                s = eq - BASE; ledger += s; eq = BASE
                if reached:
                    skims.append(s)
            cw = wk
        dr = 0.02 * eq; notl = dr / sf if sf else 0
        eq += r * (CAP * sf if notl > CAP else dr)
        if reached is None and eq >= BASE:
            reached = tm
    w2base = (reached - t0).days / 7.0 if reached else None
    return w2base, (np.median(skims) if skims else 0.0)


def main():
    full = {c: load(c) for c in BASKET}
    wk = weeks_span(full)
    isd = {c: bt.split_is_oos(full[c], 0.70)[1] for c in BASKET}   # OOS
    JUN = pd.Timestamp("2026-06-01", tz="UTC")
    jun = {c: full[c][full[c].index >= JUN] for c in BASKET}
    print(f"# regime_mr tuning head-to-head | {BASKET} | ~{wk:.0f}wk | 15m Lighter 0-fee\n")
    print(f"{'config':16} {'PF':>5} {'WR':>4} {'BE':>4} {'cush':>5} {'tpw':>6} {'OOS':>5} "
          f"{'Jun':>5} {'2xsl':>5} | {'wk->10k':>8} {'skim/wk':>9}")
    for name, cfg in CONFIGS.items():
        per = trades(full, cfg, LIGHTER)
        pm = portfolio(per)
        wr, be, cush = cushion(per)
        oos = portfolio(trades(isd, cfg, LIGHTER))
        jn = portfolio(trades(jun, cfg, LIGHTER))
        sl2 = portfolio(trades(full, cfg, LIGHTER_2X))
        w2b, skim = ride_and_skim(per)
        w2bs = f"{w2b:.0f}wk" if w2b else "—"
        print(f"{name} {pm['pf']:5.2f} {wr:3.0f}% {be:3.0f}% {cush:+4.0f} {pm['n']/wk:6.1f} "
              f"{oos['pf']:5.2f} {jn['pf']:5.2f} {sl2['pf']:5.2f} | {w2bs:>8} ${skim:>7,.0f}")
    print("\nReading: cush = WR minus breakeven WR (room before the edge dies). skim/wk = median")
    print("weekly withdrawal once parked at $10k base, 2% risk (BACKTEST ceiling — live lower).")


if __name__ == "__main__":
    main()
