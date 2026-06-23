"""Multi-TF loader over the 3-year OKX history in strategy_hunt_2026-06-22/data_hist.

Context TF (regime) = 1h; execution TFs = 15m (Up/Down momentum) and 5m (Range MR).
Parquet produced by fetch_history.py: data_hist/okx_<COIN>_<tf>.parquet.
"""
from __future__ import annotations
import os
import pandas as pd

HIST = os.path.join(os.path.dirname(__file__), "..", "analysis", "strategy_hunt_2026-06-22", "data_hist")
COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "LTC"]
regime_tf = "1h"


def load(coin: str, tf: str) -> pd.DataFrame:
    path = os.path.join(HIST, f"okx_{coin}_{tf}.parquet")
    return pd.read_parquet(path)[["Open", "High", "Low", "Close", "Volume"]].astype(float)
