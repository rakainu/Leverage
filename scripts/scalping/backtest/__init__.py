"""Canonical backtest harness for the Leverage project.

Single source of truth for crypto leverage strategy testing. Honest-fill,
liquidation-aware simulator (engine) + risk-adjusted metrics + guardrail gate.
Optimizer and validation layer on top of these.

Usage (flat-module style, matching the repo convention):
    import sys, os
    sys.path.insert(0, os.path.join(<...>, "scripts", "scalping", "backtest"))
    from engine import Signal, Costs, RiskCfg, simulate
    from metrics import extended_metrics, passes_guardrails

Or as a package:
    from backtest import Signal, Costs, RiskCfg, simulate, extended_metrics, passes_guardrails
"""
from __future__ import annotations

import os
import sys

# make the flat modules importable by bare name whether used as a package or
# via the repo's sys.path-insert convention
sys.path.insert(0, os.path.dirname(__file__))

from engine import (  # noqa: E402
    Signal, Costs, RiskCfg, Trade, simulate, metrics,
    ema, sma, rma, atr, rsi, rolling_zscore, adx,
    split_is_oos, walk_forward_folds,
)
from metrics import extended_metrics, passes_guardrails, GUARDRAILS  # noqa: E402

__all__ = [
    "Signal", "Costs", "RiskCfg", "Trade", "simulate", "metrics",
    "ema", "sma", "rma", "atr", "rsi", "rolling_zscore", "adx",
    "split_is_oos", "walk_forward_folds",
    "extended_metrics", "passes_guardrails", "GUARDRAILS",
]
