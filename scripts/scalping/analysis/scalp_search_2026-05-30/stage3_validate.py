"""STAGE 3 — honest validation of a shortlisted config.

Separates EDGE from LEVERAGE:
  * Edge metrics (PF, WR, expectancy, avg win/loss, hold, streaks, frequency) are
    computed from per-trade PRICE returns -> leverage-independent.
  * Leverage/notional ($100/200/250 x 10/20/30x) only scale $ PnL and govern
    liquidation + account drawdown. A trade is a REAL liquidation if its max
    adverse excursion (mae_frac) reaches the liq distance (1/L)*(1-mmr) before
    its modeled exit -> then it loses the full margin, not just the stop.

Validation battery: full period, IS/OOS (70/30), 4-fold walk-forward, and a 2x
slippage stress. Prints everything; no cherry-picking.

Usage: python stage3_validate.py <family> <coin> <tf> <side> '<json params>'
"""
from __future__ import annotations
import sys, json
import numpy as np
import common as K

MMR = 0.005  # maintenance margin rate (matches btengine RiskCfg default)


def price_returns(trades):
    """Per-trade signed price return fraction (slippage already in entry/exit)."""
    out = []
    for t in trades:
        r = t.side * (t.exit_price - t.entry_price) / t.entry_price
        out.append((r, t.mae_frac, t.bars_held, t.exit_reason))
    return out


def edge_metrics(trades, tf):
    pr = price_returns(trades)
    if not pr:
        return {"n": 0}
    rets = np.array([x[0] for x in pr])
    wins = rets[rets > 0]; losses = rets[rets < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    return {
        "n": len(rets),
        "wr": 100.0 * len(wins) / len(rets),
        "pf": pf,
        "exp_ret_pct": 100.0 * rets.mean(),         # expectancy per trade, price %
        "avg_win_pct": 100.0 * wins.mean() if len(wins) else 0.0,
        "avg_loss_pct": 100.0 * losses.mean() if len(losses) else 0.0,
        "avg_hold_min": np.mean([x[2] for x in pr]) * K.TF_MIN[tf],
        "med_mae_pct": 100.0 * np.median([x[1] for x in pr]),
        "max_mae_pct": 100.0 * np.max([x[1] for x in pr]),
    }


def freq(df, tf, n):
    wk = K.weeks_of(df, tf)
    return n / wk if wk else 0.0


def lev_notional_table(trades):
    """For each (notional, leverage), compute $ outcome honestly incl. liquidation."""
    pr = price_returns(trades)
    rows = []
    for N in (100, 200, 250):
        for L in (10, 20, 30):
            liq_frac = (1.0 / L) * (1 - MMR)
            margin = N / L
            pnl = []
            liqs = 0
            for r, mae, _, _ in pr:
                if mae >= liq_frac:           # liquidated before modeled exit
                    pnl.append(-margin); liqs += 1
                else:
                    pnl.append(r * N)         # price return on fixed notional
            pnl = np.array(pnl)
            eq = np.cumsum(pnl)
            peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
            dd = (peak - np.concatenate([[0.0], eq]))
            rows.append(dict(notional=N, lev=L, margin=round(margin, 2),
                             net_usd=round(pnl.sum(), 2), maxdd_usd=round(dd.max(), 2),
                             liq_hits=liqs, liq_frac_pct=round(100 * liq_frac, 2)))
    return rows


def run_block(fam, df, tf, side, params, costs, label):
    m, trades = K.run(fam, df, tf, side, params, costs=costs)
    em = edge_metrics(trades, tf)
    em["tpw"] = round(freq(df, tf, em["n"]), 2)
    pf = em.get("pf", 0)
    pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(f"  {label:14} n={em['n']:>5} tpw={em['tpw']:>5.1f} PF={pfs:>5} "
          f"WR={em['wr']:>4.0f}% exp={em['exp_ret_pct']:>+6.3f}% "
          f"win={em['avg_win_pct']:>+5.2f}% loss={em['avg_loss_pct']:>+5.2f}% "
          f"hold={em['avg_hold_min']:>4.0f}m maxMAE={em['max_mae_pct']:.1f}%", flush=True)
    return em, trades


def main():
    fam, coin, tf, side = sys.argv[1:5]
    params = json.loads(sys.argv[5])
    df = K.load(coin, tf)
    print(f"\n### {fam} | {coin} {tf} | {side} | {params}")
    print(f"period: {df.index[0]} -> {df.index[-1]}  ({K.weeks_of(df, tf):.1f} wk)")

    print("EDGE (Lighter zero-fee, slip 0.05%):")
    em_full, trades_full = run_block(fam, df, tf, side, params, K.LIGHTER, "full")

    # IS / OOS 70/30
    is_df, oos_df = K.split_is_oos(df, 0.70)
    run_block(fam, is_df, tf, side, params, K.LIGHTER, "in-sample")
    run_block(fam, oos_df, tf, side, params, K.LIGHTER, "out-sample")

    # walk-forward folds (report the OOS tail of each fold)
    print("WALK-FORWARD (4 folds, OOS tail each):")
    for k, (_, seg_oos) in enumerate(K.walk_forward_folds(df, 4)):
        run_block(fam, seg_oos, tf, side, params, K.LIGHTER, f"fold{k+1}-oos")

    # slippage stress
    print("SLIPPAGE STRESS:")
    run_block(fam, df, tf, side, params, K.LIGHTER_HISLIP, "slip 0.10%")
    run_block(fam, df, tf, side, params, K.BLOFIN, "blofin fees")

    # leverage / notional impact (full period)
    print("LEVERAGE x NOTIONAL ($ outcome, honest liquidation):")
    print(f"  {'notl':>5}{'lev':>5}{'margin':>8}{'net$':>9}{'maxDD$':>9}{'liq':>5}{'liqDist%':>9}")
    for r in lev_notional_table(trades_full):
        print(f"  {r['notional']:>5}{r['lev']:>5}{r['margin']:>8.2f}{r['net_usd']:>9.2f}"
              f"{r['maxdd_usd']:>9.2f}{r['liq_hits']:>5}{r['liq_frac_pct']:>9.2f}", flush=True)


if __name__ == "__main__":
    main()
