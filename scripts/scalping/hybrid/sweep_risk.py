"""Task 8 — risk-escalation frontier.

For a ladder of (risk_frac, max_leverage) levels, build the FULL combined book
(all coins routed through the switcher with frozen specialist params), run it
through the single shared-capital portfolio simulator over 3 years, and record
compounded return, max drawdown, and modeled liquidation count. Pick the most
aggressive level that stays liquidation-free with drawdown <= 40%.

Per-coin intents are generated with compounding OFF (clean per-trade R); the
portfolio simulator owns the account-level compounding, so growth isn't counted
twice.
"""
from __future__ import annotations
import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "backtest")))
sys.path.insert(0, HERE)
import data, switcher
from engine import Costs, RiskCfg
from metrics import extended_metrics
import portfolio_sim

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
START = 3000.0
RISK_LEVELS = [(0.01, 20), (0.02, 20), (0.03, 25), (0.05, 25), (0.08, 30), (0.10, 30)]
MAX_DD_CEILING = 40.0


def gen_risk(rf, lev):
    return RiskCfg(starting_equity=START, risk_frac=rf, max_leverage=lev, liq_buffer=2.5, compounding=False)


def acct_risk(rf, lev):
    return RiskCfg(starting_equity=START, risk_frac=rf, max_leverage=lev, liq_buffer=2.5, compounding=True)


def max_dd(eq):
    v = eq.values
    if len(v) == 0:
        return 0.0
    peak = np.maximum.accumulate(v)
    return float((np.where(peak > 0, (peak - v) / peak, 0.0)).max() * 100)


def build_intents(frozen, rf, lev, coins, costs=LIGHTER):
    risk = gen_risk(rf, lev)
    return {c: switcher.coin_intents(c, costs, risk, frozen) for c in coins}


def main():
    frozen = json.load(open(os.path.join(HERE, "frozen_params.json")))
    coins = data.COINS
    print(f"Risk-escalation frontier (start ${START:.0f}, 3y, Lighter, frozen params)")
    print(f"{'rf':>5} {'lev':>4} | {'return':>9} {'maxDD':>6} {'liq':>4} {'trades':>7}")
    rows = []
    for rf, lev in RISK_LEVELS:
        intents = build_intents(frozen, rf, lev, coins)
        liq = sum(extended_metrics(tr, START, compounding=False)["liq_hits"] for tr in intents.values())
        out = portfolio_sim.simulate(intents, acct_risk(rf, lev), max_positions=5, max_total_notional=START * lev)
        ret = out["final_equity"] / START - 1.0
        dd = max_dd(out["equity_curve"])
        rows.append((rf, lev, ret, dd, liq, len(out["trades"])))
        print(f"{rf:>5.0%} {lev:>4} | {ret:>+8.0%} {dd:>5.0f}% {liq:>4} {len(out['trades']):>7}")
    safe = [r for r in rows if r[4] == 0 and r[3] <= MAX_DD_CEILING]
    if safe:
        pick = max(safe, key=lambda r: r[2])
        print(f"\nRECOMMENDED: rf={pick[0]:.0%} lev={pick[1]}  ->  return={pick[2]:+.0%} maxDD={pick[3]:.0f}% liq=0")
    else:
        print("\nNo level cleared 0-liq AND DD<=40% — tighten liq_buffer or lower risk.")


if __name__ == "__main__":
    main()
