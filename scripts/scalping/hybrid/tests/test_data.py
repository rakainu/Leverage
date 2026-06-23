import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data


def test_load_all_tfs():
    for tf in ["1h", "15m", "5m"]:
        df = data.load("BTC", tf)
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(df) > 1000 and df.index.is_monotonic_increasing
