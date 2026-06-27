"""Stage 9 — market-regime-gated Donchian. The fix the per-fold evidence demands.

Stage 8 proved the long Donchian wins in bull/trending folds and loses in
flat/bear folds. So gate it on a BROAD-MARKET regime: only open longs when BTC is
above its EMA(regime_len). Sit out chop/bear (capital preservation). If the bear
folds go ~flat instead of losing, walk-forward should jump to robust.

Compares ungated vs BTC-regime-gated on the 3y basket, 1h + 4h, 6-fold WF.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd
import optuna

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import RiskCfg, ema                      # noqa: E402
from optimizer import _suggest                       # noqa: E402
from metrics import extended_metrics                 # noqa: E402
sys.path.insert(0, HERE)
from donchian_millerrh import simulate_donchian      # noqa: E402
from stage8_fair_retest import (load_hist, basket_metrics, COINS, LIGHTER, BLOFIN,  # noqa: E402
                                RISK, TF_MIN, SPACE, HARD_KILL, fin)

optuna.logging.set_verbosity(optuna.logging.WARNING)
REGIME_LEN = 200   # BTC > EMA200 on the trading TF = "market in uptrend"


def btc_regime(tf):
    btc = load_hist("BTC", tf)
    reg = (btc["Close"] > ema(btc["Close"], REGIME_LEN))
    return btc.index, reg


def gate_for(df, reg_idx, reg):
    """Align BTC regime onto df's index (ffill)."""
    s = pd.Series(reg.values, index=reg_idx).reindex(df.index, method="ffill").fillna(False)
    return s.values.astype(bool)


def run_basket(dfs, costs, params, tfm, gates=None):
    tbc, liq = {}, 0
    for c, d in dfs.items():
        g = gates[c] if gates is not None else None
        tr = simulate_donchian(d, costs, RISK, tfm, entry_gate=g, **params)
        tbc[c] = tr
        liq += extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"]
    return tbc, liq


def basket_optimize(dfs, tfm, n_trials, min_trades, gates=None, seed=42):
    def obj(trial):
        p = {k: _suggest(trial, k, v) for k, v in SPACE.items()}
        try:
            tbc, liq = run_basket(dfs, LIGHTER, p, tfm, gates)
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


def fold_bh(dfs):
    return float(np.mean([(d["Close"].iloc[-1] / d["Close"].iloc[0] - 1) * 100 for d in dfs.values()]))


def run_tf(tf):
    tfm = TF_MIN[tf]
    dfs = {c: load_hist(c, tf) for c in COINS}
    ridx, reg = btc_regime(tf)
    gates = {c: gate_for(d, ridx, reg) for c, d in dfs.items()}
    pct_on = np.mean([g.mean() for g in gates.values()]) * 100
    print(f"\n{'='*78}\n{tf}  | BTC>EMA{REGIME_LEN} regime ON {pct_on:.0f}% of the time\n{'='*78}")

    NF = 6
    print(f"Walk-forward {NF} folds (GATED, re-optimized):")
    passed = 0
    for k in range(NF):
        lo, hi = k / NF, (k + 1) / NF
        tr, te, trg, teg = {}, {}, {}, {}
        for c, d in dfs.items():
            a, b = int(lo * len(d)), int(hi * len(d))
            seg = d.iloc[a:b]; cut = int(len(seg) * 0.7)
            tr[c], te[c] = seg.iloc[:cut], seg.iloc[cut:]
            g = gates[c]
            trg[c], teg[c] = g[a:a + cut], g[a + cut:b]
        bp = basket_optimize(tr, tfm, n_trials=60, min_trades=max(20, 200 // NF), gates=trg)
        ttbc, _ = run_basket(te, LIGHTER, bp, tfm, teg)
        fm = basket_metrics(ttbc, RISK.starting_equity)
        bh = fold_bh(te); d0 = list(te.values())[0]
        if fm is None or fm["n"] == 0:
            # gate kept us flat — that's a WIN in a bear fold (no loss). count as non-losing.
            non_losing = bh < 0
            passed += non_losing
            print(f"  fold{k} [{d0.index[0].date()}..{d0.index[-1].date()}] regime={bh:>+4.0f}% | "
                  f"FLAT (gate off) {'OK (avoided bear)' if non_losing else '(missed)'}")
            continue
        ok = (fm["pf"] >= 1.2 and fm["payoff"] >= 1.0 and fm["maxdd"] <= 30 and fm["net_pct"] > 0)
        passed += ok
        print(f"  fold{k} [{d0.index[0].date()}..{d0.index[-1].date()}] regime={bh:>+4.0f}% | "
              f"n={fm['n']:>3} PF={fin(fm['pf']):.2f} payoff={fin(fm['payoff']):.2f} net={fm['net_pct']:>+5.0f}% "
              f"DD={fm['maxdd']:>3.0f}% {'PASS' if ok else 'fail'}")
    print(f"VERDICT {tf} (gated): {passed}/{NF} -> {'ROBUST' if passed >= int(np.ceil(0.75*NF)) else 'FRAGILE'}")

    # full-sample gated, compounding, for the headline equity
    is_dfs = {c: d.iloc[:int(0.7 * len(d))] for c, d in dfs.items()}
    isg = {c: gates[c][:int(0.7 * len(d))] for c, d in dfs.items()}
    best = basket_optimize(is_dfs, tfm, n_trials=120, min_trades=150, gates=isg)
    oos = {c: d.iloc[int(0.7 * len(d)):] for c, d in dfs.items()}
    oosg = {c: gates[c][int(0.7 * len(d)):] for c, d in dfs.items()}
    om, oliq = run_basket(oos, LIGHTER, best, tfm, oosg)
    m = basket_metrics(om, RISK.starting_equity)
    bf, _ = run_basket(oos, BLOFIN, best, tfm, oosg); bm = basket_metrics(bf, RISK.starting_equity)
    print(f"  OOS(last30%) gated: n={m['n']} PF={fin(m['pf']):.2f} payoff={fin(m['payoff']):.2f} "
          f"net={m['net_pct']:+.0f}% DD={m['maxdd']:.0f}% liq={oliq} | BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:+.0f}%")
    print(f"  best: {best}")


def main():
    print(f"REGIME-GATED Donchian (BTC>EMA{REGIME_LEN}) — 3y basket, only long when market trending up")
    for tf in ["1h", "4h"]:
        run_tf(tf)


if __name__ == "__main__":
    main()
