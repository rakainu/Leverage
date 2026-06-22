# hybrid/tests/test_short_momo.py
import os, sys
HUNT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "analysis", "strategy_hunt_2026-06-22"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
sys.path.insert(0, HUNT)
import numpy as np, pandas as pd
from engine import Costs, RiskCfg
import short_momo
ZERO = Costs(0,0,0,0)
R = RiskCfg(starting_equity=3000, risk_frac=0.01, max_leverage=20, compounding=False)
def _mk(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c*1.002, "Low": c*0.998, "Close": c, "Volume": 1.0}, index=idx)
def test_short_on_breakdown_profits_in_downtrend():
    df = _mk(list(np.linspace(200, 100, 300)))   # steady downtrend
    trades = short_momo.simulate(df, ZERO, R, 15, dc_high=20, dc_low=20, dc_stop=10)
    assert len(trades) >= 1 and all(t.side == -1 for t in trades)
    assert sum(t.pnl_usd for t in trades) > 0     # shorts make money falling
