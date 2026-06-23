import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
import pandas as pd
import sweep_risk


def test_exposes_levels_and_max_dd():
    assert isinstance(sweep_risk.RISK_LEVELS, list) and len(sweep_risk.RISK_LEVELS) >= 3
    rising = pd.Series([100, 120, 90, 130], index=pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC"))
    dd = sweep_risk.max_dd(rising)
    assert abs(dd - 25.0) < 1e-6  # peak 120 -> trough 90 = 25% drawdown
