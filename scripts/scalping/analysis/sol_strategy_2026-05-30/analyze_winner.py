"""Fragility audit of the SOL 1h short-only MR winner before declaring it deliverable.

Checks:
  1. Trade-level distribution: are profits concentrated in a few outliers?
  2. Monthly PnL: positive across regimes or one lucky month?
  3. Cost stress test: does the edge survive harsher fees/slippage?
  4. Local parameter surface: is PF a sharp peak or a stable plateau?
  5. Liquidation / leverage sanity.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from btengine import load_sol, simulate, metrics, fmt, split_is_oos, Costs, RiskCfg
from strategies import mr_fade

RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, liq_buffer=2.0, compounding=True)
TF_MIN = 60
WIN = dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, adx_max=35, max_bars=48, limit_atr=0.0, side_only=-1)

def run(df, costs):
    return simulate(df, mr_fade(df, **WIN), costs, RISK, TF_MIN)

def main():
    df = load_sol("1h")
    base = Costs()
    tr = run(df, base)
    m = metrics(tr, RISK.starting_equity)
    print("WINNER  short-only z2.5 adx<=35 sl2.0 tp=mean mb48 la0.0")
    print("FULL   " + fmt(m))

    pnls = np.array([t.pnl_usd for t in tr])
    rs = np.array([t.r_multiple for t in tr])
    wins = sorted(pnls[pnls > 0], reverse=True)
    print(f"\n1) DISTRIBUTION  ({len(tr)} trades)")
    print(f"   gross profit ${sum(wins):.0f} | top-1 win ${wins[0]:.0f} ({wins[0]/sum(wins)*100:.0f}% of gross) "
          f"| top-3 ${sum(wins[:3]):.0f} ({sum(wins[:3])/sum(wins)*100:.0f}%)")
    print(f"   largest win ${pnls.max():.0f} | largest loss ${pnls.min():.0f} | "
          f"median R {np.median(rs):+.2f} | best/worst R {rs.max():+.2f}/{rs.min():+.2f}")
    print(f"   eff leverage: min {min(t.eff_leverage for t in tr):.1f}x max {max(t.eff_leverage for t in tr):.1f}x | "
          f"liq hits {m['liq_hits']} | worst MAE {max(t.mae_frac for t in tr)*100:.1f}%")

    print("\n2) MONTHLY PnL (USDT, compounding off for attribution)")
    s = pd.Series(pnls, index=[t.exit_time for t in tr])
    monthly = s.groupby(pd.Grouper(freq="ME")).agg(["sum", "count"])
    for ts, row in monthly.iterrows():
        bar = "+" * int(max(0, row["sum"]) / 10) + "-" * int(max(0, -row["sum"]) / 10)
        print(f"   {ts.strftime('%Y-%m')}  ${row['sum']:+7.0f}  (n={int(row['count'])})  {bar}")
    pos_m = (monthly["sum"] > 0).sum(); tot_m = len(monthly)
    print(f"   positive months: {pos_m}/{tot_m}")

    print("\n3) COST STRESS TEST (FULL period)")
    for label, c in [("base   (t.06/m.02/s.05)", Costs()),
                     ("worse  (t.08/m.03/s.10)", Costs(taker_pct=0.08, maker_pct=0.03, slippage_pct=0.10)),
                     ("harsh  (t.10/m.05/s.15)", Costs(taker_pct=0.10, maker_pct=0.05, slippage_pct=0.15)),
                     ("alltaker s.10",            Costs(taker_pct=0.06, maker_pct=0.06, slippage_pct=0.10))]:
        mm = metrics(run(df, c), RISK.starting_equity)
        print(f"   {label:26} PF={mm['profit_factor']:.2f}  net={mm['net_pnl']:+.0f}  n={mm['n']}  WR={mm['win_rate']:.0f}%")

    print("\n4) LOCAL PARAMETER SURFACE  (FULL PF / OOS PF)")
    is_df, oos_df = split_is_oos(df, 0.70)
    print("        adx<=30      adx<=35      adx<=40")
    for ze in [2.25, 2.5, 2.75]:
        cells = []
        for ax in [30, 35, 40]:
            c = {**WIN, "z_entry": ze, "adx_max": ax}
            mf = metrics(simulate(df, mr_fade(df, **c), base, RISK, TF_MIN), RISK.starting_equity)
            mo = metrics(simulate(oos_df, mr_fade(oos_df, **c), base, RISK, TF_MIN), RISK.starting_equity)
            cells.append(f"{mf['profit_factor']:.2f}/{mo['profit_factor']:.2f}(n{mf['n']})")
        print(f"  ze={ze:<4} " + "  ".join(f"{x:<12}" for x in cells))
    print("\n  sl_atr sweep (FULL PF, ze=2.5 adx<=35):")
    for sl in [1.5, 1.75, 2.0, 2.25, 2.5, 3.0]:
        c = {**WIN, "sl_atr": sl}
        mf = metrics(simulate(df, mr_fade(df, **c), base, RISK, TF_MIN), RISK.starting_equity)
        print(f"     sl={sl:<5} PF={mf['profit_factor']:.2f}  net={mf['net_pnl']:+.0f}  n={mf['n']}  DD={mf['max_dd_pct']:.1f}%")

if __name__ == "__main__":
    main()
