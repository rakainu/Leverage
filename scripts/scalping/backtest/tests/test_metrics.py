"""Tests for the risk-adjusted metrics + guardrail gate."""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from engine import Signal, Costs, RiskCfg, simulate  # noqa: E402
from metrics import extended_metrics, passes_guardrails  # noqa: E402

ZERO = Costs(taker_pct=0, maker_pct=0, slippage_pct=0, funding_pct_per_8h=0)


def _winning_book(n=40):
    """Build a deterministic book of n clean +2R winners, one per 2 bars."""
    rows = []
    sigs = []
    # bar pattern per trade: decision(90) / entry(100) / TP-hit(104)
    for k in range(n):
        base = len(rows)
        rows += [[90, 91, 89, 90, 1], [100, 101, 99, 100, 1], [100, 105, 100, 104, 1]]
        sigs.append(Signal(i=base, side=1, sl_dist=2, tp_dist=4))
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="1h", tz="UTC")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx).astype(float)
    return df, sigs


def test_riskadjusted_fields_present_and_finite():
    df, sigs = _winning_book(40)
    R = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, compounding=True)
    trades = simulate(df, sigs, ZERO, R, 60)
    m = extended_metrics(trades, R.starting_equity, compounding=True)
    for k in ("cagr", "sharpe", "sortino", "calmar", "ulcer", "recovery_factor", "trades_per_day"):
        assert k in m, f"missing {k}"
        assert np.isfinite(m[k]) or m[k] == float("inf"), f"{k} not finite: {m[k]}"
    assert m["n"] > 0
    assert m["sharpe"] > 0, "all-winners book must have positive Sharpe"


def test_guardrails_pass_clean_book():
    df, sigs = _winning_book(40)
    R = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, compounding=True)
    m = extended_metrics(simulate(df, sigs, ZERO, R, 60), R.starting_equity)
    ok, reasons = passes_guardrails(m)
    assert ok, f"clean winning book should pass guardrails, failed: {reasons}"


def test_guardrails_reject_too_few_trades():
    df, sigs = _winning_book(5)  # below min_trades=30
    R = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, compounding=True)
    m = extended_metrics(simulate(df, sigs, ZERO, R, 60), R.starting_equity)
    ok, reasons = passes_guardrails(m)
    assert not ok and any("trades" in r for r in reasons)


def test_empty_book_is_safe():
    m = extended_metrics([], 1000.0)
    assert m["n"] == 0 and m["sharpe"] == 0.0
    ok, reasons = passes_guardrails(m)
    assert not ok


if __name__ == "__main__":
    test_riskadjusted_fields_present_and_finite()
    test_guardrails_pass_clean_book()
    test_guardrails_reject_too_few_trades()
    test_empty_book_is_safe()
    print("all metrics tests passed")
