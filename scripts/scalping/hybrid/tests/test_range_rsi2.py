import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "strategies"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
import numpy as np, pandas as pd
import range_rsi2
def _mk(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c*1.001, "Low": c*0.999, "Close": c, "Volume": 1.0}, index=idx)
def test_oversold_emits_long():
    # sharp drop -> RSI(2) very low, price below SMA -> long signal
    df = _mk([100]*20 + [99, 97, 94, 90, 85])
    sigs = range_rsi2.signals(df, side="long")
    assert any(s.side == 1 for s in sigs)
def test_overbought_emits_short():
    df = _mk([100]*20 + [101, 103, 106, 110, 115])
    sigs = range_rsi2.signals(df, side="short")
    assert any(s.side == -1 for s in sigs)
def test_target_is_reversion_to_mean():
    df = _mk([100]*20 + [99, 97, 94, 90, 85])
    sigs = [s for s in range_rsi2.signals(df, side="long") if s.side == 1]
    assert sigs and sigs[0].tp_dist > 0
