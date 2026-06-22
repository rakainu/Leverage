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
