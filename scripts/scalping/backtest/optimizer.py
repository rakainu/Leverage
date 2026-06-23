"""Optuna-driven parameter search over the honest engine.

This is the freqtrade-grade hyperopt machinery, but bolted onto btengine so the
optimization happens against HONEST leverage accounting (real fills, funding,
liquidation) instead of an optimistic spot backtest.

Risk profile #2 (high return WITH guardrails) is baked into the objective:
  - the search MAXIMIZES a risk-adjusted target (default Calmar = return / maxDD),
  - a modeled liquidation breach is a HARD kill (score = -inf): we never optimize
    toward a book that can get liquidated,
  - too-few-trades and drawdown-ceiling breaches are penalized so the search is
    pushed back into the feasible region.

Strategy contract (matches strat_lib): fn(df, side='both', **params) -> [Signal].

Discipline: search on IN-SAMPLE only, then report the winner's OUT-OF-SAMPLE
metrics too. A param set that wins IS but falls apart OOS is overfit — the
returned record shows both so that's visible at a glance. Deeper robustness
(walk-forward, Monte Carlo) lives in validation.py.

param_space entries:
    ("int",   lo, hi)            ("int", lo, hi, step)
    ("float", lo, hi)            ("float", lo, hi, "log")
    ("cat",   [choices...])
    <any scalar>                  -> fixed, passed through unsearched
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import optuna

from engine import Costs, RiskCfg, simulate, split_is_oos
from metrics import extended_metrics, passes_guardrails, GUARDRAILS

optuna.logging.set_verbosity(optuna.logging.WARNING)

HARD_KILL = -1e9  # liquidation breach / no trades


@dataclass
class OptResult:
    best_params: dict
    best_value: float
    objective: str
    is_metrics: dict
    oos_metrics: dict
    is_pass: bool
    oos_pass: bool
    oos_fail_reasons: list
    n_trials: int
    study: object = field(default=None, repr=False)


def _suggest(trial, name, spec):
    if not isinstance(spec, tuple):
        return spec  # fixed scalar
    kind = spec[0]
    if kind == "int":
        step = spec[3] if len(spec) > 3 else 1
        return trial.suggest_int(name, spec[1], spec[2], step=step)
    if kind == "float":
        log = len(spec) > 3 and spec[3] == "log"
        return trial.suggest_float(name, spec[1], spec[2], log=log)
    if kind == "cat":
        return trial.suggest_categorical(name, spec[1])
    raise ValueError(f"bad param spec for {name}: {spec}")


def _score(m: dict, objective: str, guardrails: dict, compounding: bool) -> float:
    """Turn metrics into an optimizer score. Higher is better."""
    if m["n"] < guardrails["min_trades"]:
        return HARD_KILL + m["n"]                       # nudge toward more trades
    if not guardrails["allow_liq"] and m.get("liq_hits", 0) > 0:
        return HARD_KILL                                # never optimize toward liquidation
    val = m.get(objective, 0.0)
    if val == float("inf"):
        val = 1e6
    # soft penalty for breaching the drawdown ceiling (keeps search near feasibility)
    over = m["max_dd_pct"] - guardrails["max_dd_pct"]
    if over > 0:
        val -= over * abs(val if val else 1.0) * 0.1 + over
    return float(val)


def optimize(
    strategy_fn: Callable,
    df,
    param_space: dict,
    *,
    costs: Costs,
    risk: RiskCfg,
    tf_minutes: int,
    side: str = "both",
    objective: str = "calmar",
    n_trials: int = 200,
    is_frac: float = 0.70,
    guardrails: dict | None = None,
    seed: int = 42,
    show_progress: bool = False,
) -> OptResult:
    """Search param_space to maximize `objective` on the in-sample slice.

    objective in {calmar, sharpe, sortino, cagr, net_pct, recovery_factor, profit_factor}.
    """
    g = {**GUARDRAILS, **(guardrails or {})}
    is_df, oos_df = split_is_oos(df, is_frac)
    comp = risk.compounding

    def run(seg, params):
        sigs = strategy_fn(seg, side=side, **params)
        trades = simulate(seg, sigs, costs, risk, tf_minutes)
        return extended_metrics(trades, risk.starting_equity, compounding=comp)

    def objective_fn(trial):
        params = {name: _suggest(trial, name, spec) for name, spec in param_space.items()}
        try:
            m = run(is_df, params)
        except Exception:
            return HARD_KILL
        return _score(m, objective, g, comp)

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective_fn, n_trials=n_trials, show_progress_bar=show_progress)

    best = study.best_params
    # merge in any fixed (unsearched) params so the result is directly runnable
    fixed = {k: v for k, v in param_space.items() if not isinstance(v, tuple)}
    best_params = {**fixed, **best}

    is_m = run(is_df, best_params)
    oos_m = run(oos_df, best_params)
    is_ok, _ = passes_guardrails(is_m, **g)
    oos_ok, oos_reasons = passes_guardrails(oos_m, **g)

    return OptResult(
        best_params=best_params, best_value=study.best_value, objective=objective,
        is_metrics=is_m, oos_metrics=oos_m, is_pass=is_ok, oos_pass=oos_ok,
        oos_fail_reasons=oos_reasons, n_trials=n_trials, study=study,
    )
