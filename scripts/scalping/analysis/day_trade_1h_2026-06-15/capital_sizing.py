"""What does a REAL $3,000 account do on squeeze (sq12, 4-coin)? Shows leverage
(10x vs 20x cap) and compounding on/off, with the leverage ACTUALLY used and any
liquidation hits — to answer 'what would I start live with'.

Risk-based sizing: each trade risks risk_frac of equity to the 1.5xATR stop;
leverage is a safety cap (notional <= equity*lev, liq >= liq_buffer*stop away)."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(__file__); sys.path.insert(0, HERE)
from revalidate_squeeze import load_1h, make_signals, COINS, LIGHTER, bt, TF_MIN

START = 3000.0
VARIANT = "sq12"


def run(start, risk_frac, max_lev, compounding, coins):
    RISK = bt.RiskCfg(starting_equity=start, risk_frac=risk_frac, max_leverage=max_lev,
                      liq_buffer=2.5, compounding=compounding)
    recs = []
    levs, liqs = [], 0
    for c in coins:
        df = load_1h(c)
        trades = bt.simulate(df, make_signals(df, VARIANT), LIGHTER, RISK, TF_MIN)
        for t in trades:
            recs.append((t.exit_time, t.r_multiple))
            levs.append(t.eff_leverage)
            # liquidation check (same as metrics): did MAE breach the liq price?
            if ((t.side > 0 and t.mae_frac * t.entry_price >= (t.entry_price - t.liq_price)) or
                (t.side < 0 and t.mae_frac * t.entry_price >= (t.liq_price - t.entry_price))):
                liqs += 1
    recs.sort(key=lambda x: x[0])
    eq = start; curve = [start]; pnls = []
    for _, r in recs:
        base = eq if compounding else start
        pnl = r * risk_frac * base
        eq += pnl; pnls.append(pnl); curve.append(eq)
    pnls = np.array(pnls); curve = np.array(curve)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    peak = np.maximum.accumulate(curve); dd = ((peak - curve) / peak).max() * 100
    dd_usd = (peak - curve).max()
    return dict(n=len(pnls), final=eq, net=eq - start, net_pct=(eq/start-1)*100,
                pf=pf, dd=dd, dd_usd=dd_usd, maxlev=max(levs) if levs else 0,
                medlev=float(np.median(levs)) if levs else 0, liqs=liqs)


def show(tag, m):
    print(f"  {tag:30} ${m['final']:>7,.0f}  net {m['net_pct']:+6.1f}% (${m['net']:+,.0f})  "
          f"maxDD {m['dd']:4.1f}% (${m['dd_usd']:,.0f})  PF {m['pf']:.2f}  "
          f"lev used med {m['medlev']:.1f}x/max {m['maxlev']:.0f}x  liq {m['liqs']}")


def main():
    print(f"# $3,000 account | squeeze {VARIANT} 4-coin {COINS} | fresh data ~7mo | Lighter 0-fee\n")
    print("=== 0.75% risk/trade (the deployed setting) ===")
    for comp in (True, False):
        for lev in (10, 20):
            tag = f"{'COMPOUND' if comp else 'fixed'} {lev}x cap"
            show(tag, run(START, 0.0075, lev, comp, COINS))
    print("\n=== 1.0% risk/trade (a touch more aggressive) ===")
    for comp in (True, False):
        for lev in (10, 20):
            tag = f"{'COMPOUND' if comp else 'fixed'} {lev}x cap"
            show(tag, run(START, 0.01, lev, comp, COINS))


if __name__ == "__main__":
    main()
