"""Task 9 — end-to-end hybrid run.

classify -> switch -> portfolio sim -> monthly report, at the locked risk level,
with frozen specialist params. Lighter is the primary (gate) venue; BloFin is
re-run for an informational line only.

Set CHOSEN_RF / CHOSEN_LEV to the level recommended by sweep_risk.py.
"""
from __future__ import annotations
import os, sys, json
from collections import defaultdict
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backtest")))
sys.path.insert(0, HERE)
import data, switcher, report
from engine import Costs, RiskCfg
from metrics import extended_metrics
import portfolio_sim

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
START = 3000.0
CHOSEN_RF, CHOSEN_LEV = 0.02, 20   # <- set from sweep_risk.py recommendation


def _run(frozen, coins, costs):
    gen = RiskCfg(starting_equity=START, risk_frac=CHOSEN_RF, max_leverage=CHOSEN_LEV, liq_buffer=2.5, compounding=False)
    acct = RiskCfg(starting_equity=START, risk_frac=CHOSEN_RF, max_leverage=CHOSEN_LEV, liq_buffer=2.5, compounding=True)
    intents = {c: switcher.coin_intents(c, costs, gen, frozen) for c in coins}
    liq = sum(extended_metrics(tr, START, compounding=False)["liq_hits"] for tr in intents.values())
    out = portfolio_sim.simulate(intents, acct, max_positions=5, max_total_notional=START * CHOSEN_LEV)
    return intents, out, liq


def main():
    frozen = json.load(open(os.path.join(HERE, "frozen_params.json")))
    coins = data.COINS
    print(f"HYBRID end-to-end  | start ${START:.0f}  rf={CHOSEN_RF:.0%}  lev={CHOSEN_LEV}  | {len(coins)} coins, 3y")
    print(f"frozen params: {json.dumps(frozen)}")

    intents, out, liq = _run(frozen, coins, LIGHTER)
    eq, trades = out["equity_curve"], out["trades"]
    s = report.summary(eq, trades)
    print("\n=== LIGHTER (primary) ===")
    print(f"total return {s['total_return_pct']:+.0f}%  | maxDD {s['max_dd']:.0f}%  | liq {liq}  | "
          f"{s['n_trades']} trades over {s['n_months']} months")
    print(f"months green {s['pct_months_green']:.0f}%  | avg {s['avg_month']:+.1f}%/mo  | "
          f"best {s['best_month']:+.0f}%  worst {s['worst_month']:+.0f}%")

    # per-side attribution (long/short momentum vs range both-sided), by summed R
    by_side = defaultdict(float)
    for tr in intents.values():
        for t in tr:
            by_side[t.side] += t.r_multiple
    print(f"attribution (sum R): long+{by_side.get(1,0):.0f}R  short {by_side.get(-1,0):+.0f}R")

    mp = report.monthly_pnl(eq)["return_pct"]
    yr = mp.groupby(mp.index.year).apply(lambda x: (np.prod(1 + x / 100) - 1) * 100)
    print("per-year:", "  ".join(f"{int(y)} {v:+.0f}%" for y, v in yr.items()))

    _, bout, bliq = _run(frozen, coins, BLOFIN)
    bs = report.summary(bout["equity_curve"], bout["trades"])
    print(f"\n=== BLOFIN (informational) ===\ntotal return {bs['total_return_pct']:+.0f}%  maxDD {bs['max_dd']:.0f}%  liq {bliq}")


if __name__ == "__main__":
    main()
