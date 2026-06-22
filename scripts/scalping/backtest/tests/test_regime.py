import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np, pandas as pd
from regime import classify
def _mk(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"Open": c, "High": c*1.001, "Low": c*0.999, "Close": c, "Volume": 1.0}, index=idx)
def test_uptrend_classifies_up():
    df = _mk(list(np.linspace(100, 200, 400)))   # steady strong uptrend
    r = classify(df)
    assert r.iloc[-1] == 1
def test_downtrend_classifies_down():
    df = _mk(list(np.linspace(200, 100, 400)))
    r = classify(df)
    assert r.iloc[-1] == -1
def test_flat_classifies_range():
    rng = np.tile([100, 101, 100, 99], 100)       # choppy sideways
    df = _mk(list(rng))
    r = classify(df)
    assert r.iloc[-1] == 0
def test_causal_no_lookahead():
    df = _mk(list(np.linspace(100, 200, 400)))
    r_full = classify(df)
    r_trunc = classify(df.iloc[:300])
    assert (r_full.iloc[:300].fillna(-9) == r_trunc.fillna(-9)).all()
