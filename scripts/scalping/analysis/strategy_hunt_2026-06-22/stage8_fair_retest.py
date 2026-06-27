"""Stage 8 — FAIR re-test on 3 years of multi-regime data (data_hist/).

The 6-month sample was a choppy bear (median -41%). Now test the faithful real
Donchian across 2023-2026 (bull + bear + chop) at 1h AND 4h, with a 6-fold
walk-forward that LABELS each test fold's market direction (buy&hold), so we can
see whether the strategy is durable or simply regime-dependent. If it's regime-
dependent (prints in bull/trend folds, bleeds in chop), that justifies a regime
filter rather than discarding it.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg                       # noqa: E402
from optimizer import _suggest                          # noqa: E402
from metrics import extended_metrics                    # noqa: E402
from validation import monte_carlo                      # noqa: E402
sys.path.insert(0, HERE)
from donchian_millerrh import simulate_donchian         # noqa: E402

import pandas as pd

DATA = os.path.join(HERE, "data_hist")
COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "LTC"]
optuna.logging.set_verbosity(optuna.logging.WARNING)

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=False)
TF_MIN = {"1h": 60, "4h": 240}
HARD_KILL = -1e9
fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x

SPACE = dict(
    dc_high=("int", 10, 60), dc_low=("int", 5, 30), dc_stop=("int", 3, 15),
    use_tight_stop=("cat", [False, True]),
    ma_filter=("cat", [False, True]), ma_len=("cat", [50, 100, 200]), ma_type=("cat", ["SMA", "EMA"]),
    slope_filter=("cat", [False, True]), slope_len=("cat", [5, 10, 20]), slope_type="SMA",
)


def load_hist(coin, tf):
    return pd.read_parquet(os.path.join(DATA, f"okx_{coin}_{tf}.parquet")).astype(float)


def basket_metrics(tbc, se):
    allt = sorted([t for tr in tbc.values() for t in tr], key=lambda t: t.exit_time)
    if not allt:
        return None
    pnls = np.array([t.pnl_usd for t in allt])
    eq = np.concatenate([[se], se + np.cumsum(pnls)])
    peak = np.maximum.accumulate(eq); maxdd = float(((peak - eq) / peak).max() * 100)
    w = pnls[pnls > 0]; l = -pnls[pnls < 0]
    pf = w.sum() / l.sum() if l.sum() > 0 else float("inf")
    payoff = (w.mean() / l.mean()) if (len(w) and len(l)) else float("inf")
    span = max((allt[-1].exit_time - allt[0].entry_time).total_seconds() / 86400, 1e-9)
    net = pnls.sum() / se * 100
    cagr = net * (365 / span)
    rets = pnls / se; sd = rets.std(ddof=1) if len(rets) > 1 else 0
    sharpe = rets.mean() / sd * np.sqrt(365 * len(allt) / span) if sd > 0 else 0
    return dict(n=len(allt), pf=pf, wr=len(w) / len(allt) * 100, payoff=payoff, net_pct=net,
                cagr=cagr, sharpe=sharpe, calmar=(cagr / maxdd if maxdd > 0 else float("inf")), maxdd=maxdd)


def run_basket(dfs, costs, params, tfm):
    tbc, liq = {}, 0
    for c, d in dfs.items():
        tr = simulate_donchian(d, costs, RISK, tfm, **params)
        tbc[c] = tr
        liq += extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"]
    return tbc, liq


def basket_optimize(dfs, tfm, n_trials, min_trades, seed=42):
    def obj(trial):
        p = {k: _suggest(trial, k, v) for k, v in SPACE.items()}
        try:
            tbc, liq = run_basket(dfs, LIGHTER, p, tfm)
        except Exception:
            return HARD_KILL
        if liq > 0:
            return HARD_KILL
        m = basket_metrics(tbc, RISK.starting_equity)
        if m is None or m["n"] < min_trades:
            return HARD_KILL + (0 if m is None else m["n"])
        v = m["calmar"] if m["calmar"] != float("inf") else 1e6
        over = m["maxdd"] - 25.0
        if over > 0:
            v -= over * abs(v or 1) * 0.1 + over
        return float(v)
    st = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    st.optimize(obj, n_trials=n_trials)
    fixed = {k: v for k, v in SPACE.items() if not isinstance(v, tuple)}
    return {**fixed, **st.best_params}


def fold_bh(dfs):  # mean buy&hold across coins over the fold window (regime label)
    rs = [(d["Close"].iloc[-1] / d["Close"].iloc[0] - 1) * 100 for d in dfs.values()]
    return float(np.mean(rs))


def run_tf(tf):
    tfm = TF_MIN[tf]
    dfs = {c: load_hist(c, tf) for c in COINS}
    span = (list(dfs.values())[0].index[-1] - list(dfs.values())[0].index[0]).days
    print(f"\n{'='*78}\n{tf}  ({span}d, {len(COINS)} coins)\n{'='*78}")

    # full-sample optimize (reference) + Monte Carlo
    is_dfs = {c: d.iloc[:int(0.7 * len(d))] for c, d in dfs.items()}
    best = basket_optimize(is_dfs, tfm, n_trials=120, min_trades=200)
    oos_dfs = {c: d.iloc[int(0.7 * len(d)):] for c, d in dfs.items()}
    om, oliq = run_basket(oos_dfs, LIGHTER, best, tfm)
    m = basket_metrics(om, RISK.starting_equity)
    bf, _ = run_basket(oos_dfs, BLOFIN, best, tfm); bm = basket_metrics(bf, RISK.starting_equity)
    print(f"best: {best}")
    print(f"OOS(last30%): n={m['n']} PF={fin(m['pf']):.2f} payoff={fin(m['payoff']):.2f} "
          f"net={m['net_pct']:+.0f}% DD={m['maxdd']:.0f}% liq={oliq} | BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:+.0f}%")

    # 6-fold walk-forward with regime labels
    NF = 6
    print(f"Walk-forward {NF} folds (re-optimized; regime = mean buy&hold of test window):")
    passed = 0
    for k in range(NF):
        lo, hi = k / NF, (k + 1) / NF
        tr, te = {}, {}
        for c, d in dfs.items():
            seg = d.iloc[int(lo * len(d)):int(hi * len(d))]
            cut = int(len(seg) * 0.7)
            tr[c], te[c] = seg.iloc[:cut], seg.iloc[cut:]
        bp = basket_optimize(tr, tfm, n_trials=60, min_trades=max(30, 200 // NF))
        ttbc, _ = run_basket(te, LIGHTER, bp, tfm)
        fm = basket_metrics(ttbc, RISK.starting_equity)
        bh = fold_bh(te)
        d0 = list(te.values())[0]
        if fm is None:
            print(f"  fold{k} [{d0.index[0].date()}..{d0.index[-1].date()}] regime={bh:+.0f}% no trades"); continue
        ok = (fm["pf"] >= 1.2 and fm["payoff"] >= 1.0 and fm["maxdd"] <= 30 and fm["net_pct"] > 0)
        passed += ok
        print(f"  fold{k} [{d0.index[0].date()}..{d0.index[-1].date()}] regime={bh:>+4.0f}% | "
              f"n={fm['n']:>3} PF={fin(fm['pf']):.2f} payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:>+5.0f}% "
              f"DD={fm['maxdd']:>3.0f}% {'PASS' if ok else 'fail'}")
    print(f"VERDICT {tf}: {passed}/{NF} folds -> {'ROBUST' if passed >= int(np.ceil(0.75*NF)) else 'FRAGILE'}")


def main():
    print("FAIR RE-TEST: faithful @millerrh Donchian on 3y multi-regime basket")
    for tf in ["1h", "4h"]:
        run_tf(tf)


if __name__ == "__main__":
    main()
