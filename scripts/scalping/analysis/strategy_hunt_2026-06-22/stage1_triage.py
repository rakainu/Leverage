"""Stage 1 — broad triage. Every family x coin x timeframe at default params.

Goal: cheaply kill families with no edge and SEE the win/loss profile (payoff
ratio) so we can tell fade families (small win/big loss) from momentum families
(big win/small loss). No optimization here — defaults only. Lighter zero-fee.

Ranks surviving (family, coin, tf) cells by Calmar, with payoff ratio shown.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg, simulate   # noqa: E402
from metrics import extended_metrics          # noqa: E402

# reuse the analysis loader + family registry + default params
sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
import common as C                            # noqa: E402  (DATA, load, TF_MIN, DEFAULTS)
import strat_lib as SL                        # noqa: E402

from donchian_family import donchian_breakout, DONCHIAN_DEFAULTS  # noqa: E402

# family registry for the hunt = library families + Donchian
FAMILIES = dict(SL.REGISTRY)
FAMILIES["donchian_breakout"] = donchian_breakout
DEFAULTS = dict(C.DEFAULTS)
DEFAULTS["donchian_breakout"] = DONCHIAN_DEFAULTS

COINS = ["SOL", "ETH", "BTC", "ZEC", "HYPE"]
TFS = ["3m", "5m"]

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
# Triage uses FIXED sizing (compounding off) so families are compared on raw edge,
# not on how compounding flatters whoever trades most.
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=False)

# loose triage bar: enough trades to mean something, and not bleeding
MIN_TRADES = 40


def payoff(trades):
    wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losses = [-t.pnl_usd for t in trades if t.pnl_usd < 0]
    aw = np.mean(wins) if wins else 0.0
    al = np.mean(losses) if losses else 0.0
    return (aw / al) if al > 0 else float("inf")


def main():
    rows = []
    for coin in COINS:
        for tf in TFS:
            try:
                df = C.load(coin, tf)
            except Exception as e:
                print(f"  skip {coin} {tf}: {e}", file=sys.stderr)
                continue
            tfm = C.TF_MIN[tf]
            for fam, fn in FAMILIES.items():
                params = DEFAULTS.get(fam, {})
                try:
                    sigs = fn(df, side="both", **params)
                    trades = simulate(df, sigs, LIGHTER, RISK, tfm)
                    m = extended_metrics(trades, RISK.starting_equity, compounding=False)
                except Exception as e:
                    print(f"  err {fam} {coin} {tf}: {e}", file=sys.stderr)
                    continue
                if m["n"] < MIN_TRADES:
                    continue
                rows.append(dict(
                    family=fam, coin=coin, tf=tf, n=m["n"],
                    pf=m["profit_factor"], wr=m["win_rate"], payoff=payoff(trades),
                    net_pct=m["net_pct"], calmar=m["calmar"], sharpe=m["sharpe"],
                    maxdd=m["max_dd_pct"], liq=m["liq_hits"],
                ))

    df_r = pd.DataFrame(rows)
    if df_r.empty:
        print("no cells cleared the min-trade bar")
        return
    out = os.path.join(HERE, "stage1_results.csv")
    df_r.to_csv(out, index=False)

    fin = lambda x: 999.0 if x == float("inf") else x
    df_r["calmar_s"] = df_r["calmar"].map(fin)

    # only profitable, no-liquidation cells are interesting
    good = df_r[(df_r["net_pct"] > 0) & (df_r["liq"] == 0)].copy()
    good = good.sort_values("calmar_s", ascending=False)

    def pr(r):
        pf = "inf" if r.pf == float("inf") else f"{r.pf:.2f}"
        po = "inf" if r.payoff == float("inf") else f"{r.payoff:.2f}"
        cal = "inf" if r.calmar == float("inf") else f"{r.calmar:.1f}"
        print(f"  {r.family:<18}{r.coin:<5}{r.tf:<4}n={r.n:>5}  PF={pf:>5}  WR={r.wr:>3.0f}%  "
              f"payoff={po:>5}  net={r.net_pct:>+7.1f}%  Calmar={cal:>6}  "
              f"Sharpe={r.sharpe:>5.2f}  DD={r.maxdd:>4.1f}%")

    print(f"\n=== STAGE 1 TRIAGE ===  {len(df_r)} cells traded, "
          f"{len(good)} profitable & liq-free.  (Lighter 0-fee, default params, fixed sizing)")
    print(f"\nTop 25 by Calmar:")
    for _, r in good.head(25).iterrows():
        pr(r)

    # profile split: which families escape 'small win / big loss'?
    print(f"\nProfile by family (median across coins/tf, profitable cells):")
    print(f"  {'family':<18}{'cells':>6}{'med_payoff':>11}{'med_WR':>8}{'med_PF':>8}{'med_calmar':>11}")
    g = good.groupby("family")
    prof = g.agg(cells=("net_pct", "size"), payoff=("payoff", lambda s: np.median([fin(x) for x in s])),
                 wr=("wr", "median"), pf=("pf", lambda s: np.median([fin(x) for x in s])),
                 calmar=("calmar_s", "median")).sort_values("calmar", ascending=False)
    for fam, r in prof.iterrows():
        print(f"  {fam:<18}{int(r.cells):>6}{r.payoff:>11.2f}{r.wr:>7.0f}%{r.pf:>8.2f}{r.calmar:>11.1f}")

    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
