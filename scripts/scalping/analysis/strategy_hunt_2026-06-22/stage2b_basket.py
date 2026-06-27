"""Stage 2b — BASKET optimization of momentum families on day-trade timeframes.

Lessons from Stage 2:
  - 5m momentum can't trade enough (n=4..27 mirages) -> go to 15m / 1h.
  - per-coin tuning overfits -> optimize ONE config across a basket of coins,
    judged on POOLED trades (more sample, generalization is the objective).

One parameter set per (family, tf) is searched to maximize the basket's Calmar on
the pooled in-sample trades across all coins, then scored out-of-sample. Fixed
sizing (compounding off) makes pooled per-trade PnL order-independent, so merging
coins' trades is legitimate. Liquidation on ANY coin = hard kill.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg, simulate, split_is_oos       # noqa: E402
from metrics import extended_metrics                            # noqa: E402
from optimizer import _suggest                                  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
import common as C                                              # noqa: E402

from stage2_optimize import SPACES, FAMILIES                    # reuse spaces+registry  # noqa: E402

optuna.logging.set_verbosity(optuna.logging.WARNING)

FAM_LIST = ["donchian_breakout", "squeeze_expansion", "reclaim_pullback",
            "failed_breakout", "micro_pullback", "vwap_reclaim"]
COINS = ["SOL", "ETH", "BTC", "HYPE", "ZEC", "BNB", "DOGE", "SUI"]
TFS = ["15m", "1h"]
N_TRIALS = 70
MIN_BASKET_TRADES = 120

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=False)
TF_MIN = {"15m": 15, "1h": 60, "4h": 240}
HARD_KILL = -1e9


def load_tf(coin, tf):
    if tf in ("1m", "3m", "5m", "15m"):
        return C.load(coin, tf)
    df5 = C.load(coin, "5m")
    rule = {"1h": "1h", "4h": "4h"}[tf]
    return df5.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna()


def basket_metrics(trades_by_coin, starting_equity):
    """Pool all coins' trades; order-independent stats (fixed sizing) + merged-equity DD."""
    all_tr = [t for tr in trades_by_coin.values() for t in tr]
    if not all_tr:
        return None
    all_tr.sort(key=lambda t: t.exit_time)
    pnls = np.array([t.pnl_usd for t in all_tr])
    rets = pnls / starting_equity
    eq = starting_equity + np.cumsum(pnls)
    eq = np.concatenate([[starting_equity], eq])
    peak = np.maximum.accumulate(eq)
    maxdd = float(((peak - eq) / peak).max() * 100.0)
    wins = pnls[pnls > 0]; losses = -pnls[pnls < 0]
    pf = wins.sum() / losses.sum() if losses.sum() > 0 else float("inf")
    payoff = (wins.mean() / losses.mean()) if (len(wins) and len(losses)) else float("inf")
    span = max((all_tr[-1].exit_time - all_tr[0].entry_time).total_seconds() / 86400.0, 1e-9)
    tpd = len(all_tr) / span
    ann = 365.0 * tpd
    net_pct = pnls.sum() / starting_equity * 100.0
    cagr = net_pct * (365.0 / span)
    sd = rets.std(ddof=1) if len(rets) > 1 else 0.0
    sharpe = rets.mean() / sd * np.sqrt(ann) if sd > 0 else 0.0
    calmar = cagr / maxdd if maxdd > 0 else float("inf")
    return dict(n=len(all_tr), pf=pf, wr=len(wins) / len(all_tr) * 100.0, payoff=payoff,
                net_pct=net_pct, cagr=cagr, sharpe=sharpe, calmar=calmar, maxdd=maxdd,
                span_days=span, trades_per_day=tpd)


def run_basket(fn, dfs, params, costs, tfm):
    """Run a family with one param set across all coins; return {coin: trades}, liq_total."""
    out = {}; liq = 0
    for coin, df in dfs.items():
        sigs = fn(df, side="both", **params)
        tr = simulate(df, sigs, costs, RISK, tfm)
        out[coin] = tr
        m = extended_metrics(tr, RISK.starting_equity, compounding=False)
        liq += m["liq_hits"]
    return out, liq


def optimize_family_tf(fam, tf):
    fn = FAMILIES[fam]
    space = SPACES[fam]
    tfm = TF_MIN[tf]
    dfs_full = {c: load_tf(c, tf) for c in COINS}
    dfs_is = {c: split_is_oos(d, 0.70)[0] for c, d in dfs_full.items()}
    dfs_oos = {c: split_is_oos(d, 0.70)[1] for c, d in dfs_full.items()}

    def objective(trial):
        params = {k: _suggest(trial, k, v) for k, v in space.items()}
        try:
            tbc, liq = run_basket(fn, dfs_is, params, LIGHTER, tfm)
        except Exception:
            return HARD_KILL
        if liq > 0:
            return HARD_KILL
        m = basket_metrics(tbc, RISK.starting_equity)
        if m is None or m["n"] < MIN_BASKET_TRADES:
            return HARD_KILL + (0 if m is None else m["n"])
        val = m["calmar"] if m["calmar"] != float("inf") else 1e6
        over = m["maxdd"] - 25.0
        if over > 0:
            val -= over * abs(val or 1.0) * 0.1 + over
        return float(val)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=N_TRIALS)
    fixed = {k: v for k, v in space.items() if not isinstance(v, tuple)}
    best = {**fixed, **study.best_params}

    is_tbc, _ = run_basket(fn, dfs_is, best, LIGHTER, tfm)
    oos_tbc, oos_liq = run_basket(fn, dfs_oos, best, LIGHTER, tfm)
    bf_tbc, _ = run_basket(fn, dfs_oos, best, BLOFIN, tfm)
    is_m = basket_metrics(is_tbc, RISK.starting_equity)
    oos_m = basket_metrics(oos_tbc, RISK.starting_equity)
    bf_m = basket_metrics(bf_tbc, RISK.starting_equity)
    # per-coin OOS PF spread (generalization)
    per_coin = {c: extended_metrics(oos_tbc[c], RISK.starting_equity, compounding=False)["profit_factor"]
                for c in COINS}
    coins_profitable = sum(1 for v in per_coin.values() if v > 1.0)
    return dict(family=fam, tf=tf, params=best, is_m=is_m, oos_m=oos_m, bf_m=bf_m,
                oos_liq=oos_liq, coins_profitable=coins_profitable, per_coin=per_coin)


def main():
    results = []
    for fam in FAM_LIST:
        for tf in TFS:
            try:
                r = optimize_family_tf(fam, tf)
            except Exception as e:
                print(f"  err {fam} {tf}: {e}", file=sys.stderr); continue
            results.append(r)
            o = r["oos_m"]; b = r["bf_m"]
            fin = lambda x: 999.0 if x == float("inf") else x
            print(f"  done {fam:<18}{tf:<4} OOS: n={o['n']:>4} PF={fin(o['pf']):.2f} "
                  f"payoff={fin(o['payoff']):.2f} net={o['net_pct']:+.0f}% DD={o['maxdd']:.0f}% "
                  f"coins+={r['coins_profitable']}/8 | BloFin PF={fin(b['pf']):.2f}", flush=True)

    # serialize
    rows = []
    for r in results:
        o, b, i = r["oos_m"], r["bf_m"], r["is_m"]
        rows.append(dict(family=r["family"], tf=r["tf"], coins_profitable=r["coins_profitable"],
                         oos_n=o["n"], oos_pf=o["pf"], oos_wr=o["wr"], oos_payoff=o["payoff"],
                         oos_net=o["net_pct"], oos_calmar=o["calmar"], oos_sharpe=o["sharpe"],
                         oos_dd=o["maxdd"], oos_liq=r["oos_liq"], is_pf=i["pf"], is_net=i["net_pct"],
                         bf_pf=b["pf"], bf_net=b["net_pct"], bf_dd=b["maxdd"],
                         per_coin=r["per_coin"], params=r["params"]))
    dfr = pd.DataFrame(rows)
    out = os.path.join(HERE, "stage2b_results.csv")
    dfr.to_csv(out, index=False)

    fin = lambda x: 999.0 if x == float("inf") else x
    dfr["cs"] = dfr["oos_calmar"].map(fin)
    # a real candidate: OOS PF>=1.3, payoff>=1, DD<=25, 0 liq, generalizes to >=5/8 coins,
    # and doesn't collapse on BloFin
    cand = dfr[(dfr.oos_pf >= 1.3) & (dfr.oos_payoff >= 1.0) & (dfr.oos_dd <= 25) &
               (dfr.oos_liq == 0) & (dfr.coins_profitable >= 5)].sort_values("cs", ascending=False)

    def pr(r):
        print(f"  {r.family:<18}{r.tf:<4}OOSn={r.oos_n:>4} PF={fin(r.oos_pf):.2f} WR={r.oos_wr:>3.0f}% "
              f"payoff={fin(r.oos_payoff):.2f} net={r.oos_net:>+5.0f}% DD={r.oos_dd:>4.0f}% "
              f"Sharpe={r.oos_sharpe:>4.1f} coins+={int(r.coins_profitable)}/8 | "
              f"BloFin PF={fin(r.bf_pf):.2f} net={r.bf_net:>+5.0f}%")

    print(f"\n=== STAGE 2b (basket, 15m+1h) ===  {len(dfr)} (family,tf) optimized.")
    print(f"\nREAL CANDIDATES (OOS PF>=1.3, payoff>=1, DD<=25%, generalizes>=5/8 coins, 0 liq):")
    if cand.empty:
        print("  (none) — best by OOS Calmar:")
        for _, r in dfr.sort_values("cs", ascending=False).head(6).iterrows():
            pr(r)
    else:
        for _, r in cand.iterrows():
            pr(r)
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
