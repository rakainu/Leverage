import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np, pandas as pd
import report


def test_monthly_pnl_counts_green_months():
    idx = pd.date_range("2026-01-01", periods=120, freq="D", tz="UTC")
    eq = pd.Series(3000 * np.cumprod(1 + np.where(np.arange(120) < 60, 0.001, -0.0005)), index=idx)
    m = report.monthly_pnl(eq)
    assert len(m) >= 3 and "return_pct" in m.columns


def test_summary_keys():
    idx = pd.date_range("2026-01-01", periods=120, freq="D", tz="UTC")
    eq = pd.Series(3000 * np.cumprod(1 + np.full(120, 0.001)), index=idx)
    s = report.summary(eq, [])
    for k in ("total_return_pct", "max_dd", "pct_months_green", "worst_month"):
        assert k in s
