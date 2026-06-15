"""Ride-to-base-then-withdraw model (like the scalper's compounding + weekly skim).
$3k start, 2% risk, compound up to a $10k base, then weekly skim everything above
$10k into a withdrawal ledger. Notional capped to Lighter depth.

Answers: how many weeks to ride $3k -> $10k base, then what's the steady-state
weekly payout once parked at base."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
from common import load, portfolio, weeks_span, LIGHTER, bt, TF_MIN
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scalp_search_2026-05-30")))
from strat_lib import regime_mr  # noqa: E402

CFG = dict(side="both", trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
           sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25)
BASKET = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
START, BASE, RISK_FRAC, CAP = 3000.0, 10000.0, 0.02, 50000.0


def all_trades():
    rows = []
    for c in BASKET:
        df = load(c)
        ts = bt.simulate(df, regime_mr(df, **CFG), LIGHTER, bt.RiskCfg(
            starting_equity=1000.0, risk_frac=0.01, max_leverage=10, liq_buffer=2.5,
            compounding=True), TF_MIN)
        for t in ts:
            rows.append((t.exit_time, t.r_multiple, t.risk_usd / t.notional if t.notional > 0 else 0))
    rows.sort(key=lambda x: x[0])
    return rows


def sim():
    rows = all_trades()
    eq = START
    reached_base = None
    ledger = 0.0
    week_skims = []
    cur_week = None; week_start_eq = None
    curve = []
    for tm, r, sf in rows:
        wk = tm.isocalendar()[:2]
        if cur_week is None:
            cur_week = wk
        if wk != cur_week:
            # weekly skim of anything above base
            if eq > BASE:
                skim = eq - BASE; ledger += skim; eq = BASE
                if reached_base is not None:
                    week_skims.append(skim)
            cur_week = wk
        desired_risk = RISK_FRAC * eq
        notional = desired_risk / sf if sf > 0 else 0
        risk_usd = CAP * sf if notional > CAP else desired_risk
        eq += r * risk_usd
        curve.append((tm, eq, ledger))
        if reached_base is None and eq >= BASE:
            reached_base = tm
    t0 = rows[0][0]
    wk_to_base = (reached_base - t0).days / 7.0 if reached_base else None
    span_wk = (rows[-1][0] - t0).days / 7.0
    steady_wk = span_wk - (wk_to_base or 0)
    return dict(wk_to_base=wk_to_base, ledger=ledger, final_eq=eq, span_wk=span_wk,
                steady_wk=steady_wk, week_skims=np.array(week_skims), curve=curve)


def main():
    print(f"# RIDE-TO-BASE + WITHDRAW | regime_mr {BASKET} | $3k->${BASE/1000:.0f}k base | "
          f"2% risk | ${CAP/1000:.0f}k notional cap | 15m Lighter 0-fee\n")
    r = sim()
    print(f"Phase 1 (ride up):  $3,000 -> $10,000 base in ~{r['wk_to_base']:.1f} weeks")
    print(f"Phase 2 (skim):     ~{r['steady_wk']:.0f} weeks parked at base, weekly profit withdrawn\n")
    ws = r["week_skims"]
    if len(ws):
        pos = ws[ws > 0]
        print(f"Weekly withdrawal (steady state, n={len(ws)} weeks):")
        print(f"   median ${np.median(ws):,.0f} | mean ${ws.mean():,.0f} | "
              f"best ${ws.max():,.0f} | worst ${ws.min():,.0f}")
        print(f"   {len(pos)}/{len(ws)} weeks paid out ( {100*len(pos)/len(ws):.0f}% positive )")
    print(f"\nTotal withdrawn over ~{r['span_wk']:.0f}wk window: ${r['ledger']:,.0f} "
          f"(+ ${r['final_eq']:,.0f} still in account)")
    print(f"=> on a $3k stake: ~${r['ledger']:,.0f} skimmed + base intact = "
          f"${r['ledger']+r['final_eq']:,.0f} total value")


if __name__ == "__main__":
    main()
