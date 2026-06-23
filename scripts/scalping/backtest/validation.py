"""Robustness checks that separate a real edge from an overfit curve.

A strategy that scores well on one in-sample/out-of-sample split can still be
luck. These three checks are the gate between "looks good" and "trust it live":

  walk_forward()  - re-optimize on each rolling train window, score the UNSEEN
                    test window. Aggregated OOS performance is the honest expectation.
                    A strategy whose OOS folds collapse was curve-fit.

  monte_carlo()   - bootstrap-resample the realized trade sequence thousands of
                    times. Gives a DISTRIBUTION of outcomes (median return, 5th-pct
                    drawdown, probability of ruin) instead of one lucky path. The
                    headline number being good means nothing if the 5th-percentile
                    path liquidates you.

  param_stability() - score the neighborhood around the chosen params. An edge
                    sitting on a knife-edge (great at one exact value, terrible one
                    step away) is overfit; a real edge is a broad plateau.
"""
from __future__ import annotations

import numpy as np

from engine import simulate, walk_forward_folds
from metrics import extended_metrics, passes_guardrails, GUARDRAILS
from optimizer import optimize


def walk_forward(strategy_fn, df, param_space, *, costs, risk, tf_minutes,
                 side="both", objective="calmar", n_folds=4, n_trials=100,
                 guardrails=None, seed=42):
    """Re-optimize per fold; collect each fold's out-of-sample metrics."""
    folds = walk_forward_folds(df, n_folds)
    g = {**GUARDRAILS, **(guardrails or {})}
    out = []
    for k, (train, test) in enumerate(folds):
        res = optimize(strategy_fn, train, param_space, costs=costs, risk=risk,
                       tf_minutes=tf_minutes, side=side, objective=objective,
                       n_trials=n_trials, is_frac=0.999, guardrails=g, seed=seed)
        sigs = strategy_fn(test, side=side, **res.best_params)
        trades = simulate(test, sigs, costs, risk, tf_minutes)
        m = extended_metrics(trades, risk.starting_equity, compounding=risk.compounding)
        ok, reasons = passes_guardrails(m, **g)
        out.append(dict(fold=k, params=res.best_params, oos=m, oos_pass=ok, reasons=reasons))
    passed = sum(1 for r in out if r["oos_pass"])
    agg = dict(
        folds=n_folds, folds_passed=passed,
        oos_pf_med=float(np.median([r["oos"]["profit_factor"] if np.isfinite(r["oos"]["profit_factor"]) else 5.0 for r in out])),
        oos_calmar_med=float(np.median([r["oos"]["calmar"] if np.isfinite(r["oos"]["calmar"]) else 10.0 for r in out])),
        oos_net_pct_sum=float(np.sum([r["oos"]["net_pct"] for r in out])),
        oos_maxdd_worst=float(np.max([r["oos"]["max_dd_pct"] for r in out])),
        robust=passed >= int(np.ceil(0.75 * n_folds)),   # 3/4 folds must clear the gate
    )
    return dict(per_fold=out, summary=agg)


def monte_carlo(trades, starting_equity, *, n=5000, compounding=True, seed=42):
    """Bootstrap-resample the trade sequence; distribution of outcomes.

    Sampling per-trade R-multiples with replacement and replaying them keeps the
    edge (the R distribution) but scrambles ORDER and LUCK, exposing how bad a
    plausible bad run is.
    """
    if not trades:
        return {}
    rng = np.random.default_rng(seed)
    rs = np.array([t.r_multiple for t in trades], dtype=float)
    risk_frac = trades[0].risk_usd / (trades[0].equity_after - trades[0].pnl_usd) \
        if (trades[0].equity_after - trades[0].pnl_usd) > 0 else 0.01
    m = len(rs)

    finals, maxdds, ruins = [], [], []
    for _ in range(n):
        draw = rng.choice(rs, size=m, replace=True)
        eq = starting_equity
        peak = eq
        maxdd = 0.0
        ruined = False
        for r in draw:
            pnl = r * (eq * risk_frac if compounding else starting_equity * risk_frac)
            eq += pnl
            if eq <= 0:
                ruined = True
                eq = max(eq, 1e-9)
                break
            peak = max(peak, eq)
            maxdd = max(maxdd, (peak - eq) / peak)
        finals.append(eq)
        maxdds.append(maxdd * 100.0)
        ruins.append(ruined)

    finals = np.array(finals); maxdds = np.array(maxdds)
    ret_pct = (finals / starting_equity - 1.0) * 100.0
    return dict(
        n=n,
        ret_pct_median=float(np.median(ret_pct)),
        ret_pct_p05=float(np.percentile(ret_pct, 5)),
        ret_pct_p95=float(np.percentile(ret_pct, 95)),
        maxdd_median=float(np.median(maxdds)),
        maxdd_p95=float(np.percentile(maxdds, 95)),      # a plausibly-bad drawdown
        prob_ruin=float(np.mean(ruins)),
        prob_profit=float(np.mean(ret_pct > 0)),
    )


def param_stability(strategy_fn, df, base_params, vary, *, costs, risk, tf_minutes,
                    side="both", objective="calmar", compounding=None):
    """Score base_params and each neighbor in `vary` ({name: [values...]}).

    Returns base score + per-neighbor scores. A robust edge is a plateau: neighbors
    score close to base. A spike (neighbors collapse) flags overfit.
    """
    comp = risk.compounding if compounding is None else compounding

    def score(params):
        sigs = strategy_fn(df, side=side, **params)
        m = extended_metrics(simulate(df, sigs, costs, risk, tf_minutes),
                             risk.starting_equity, compounding=comp)
        v = m.get(objective, 0.0)
        return (1e6 if v == float("inf") else float(v)), m["n"]

    base_score, base_n = score(base_params)
    neighbors = []
    for name, values in vary.items():
        for val in values:
            p = {**base_params, name: val}
            s, nn = score(p)
            neighbors.append(dict(param=name, value=val, score=s, n=nn,
                                  rel=(s / base_score if base_score else 0.0)))
    return dict(objective=objective, base_score=base_score, base_n=base_n, neighbors=neighbors)
