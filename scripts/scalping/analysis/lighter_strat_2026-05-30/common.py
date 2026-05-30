"""Shared helpers for the Lighter zero-fee strategy search."""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import simulate, metrics, fmt, split_is_oos, walk_forward_folds, Costs, RiskCfg  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "sweeps", "2026-05-20", "data")
COINS = ["SOL", "ETH", "ZEC", "HYPE"]
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}

# Lighter-style zero-fee perp: no maker/taker fee; slippage only on market & stop
# fills; funding still applies on perps. (BloFin comparison = Costs() defaults.)
LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
LIGHTER_HISLIP = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.10, funding_pct_per_8h=0.01)
BLOFIN = Costs()  # taker .06 / maker .02 / slip .05

RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=True)


def load_coin(coin: str, tf: str) -> pd.DataFrame:
    """Load a coin's OHLCV. 5m is native parquet; 15m/1h resampled from 5m for
    consistency across all coins."""
    df5 = pd.read_parquet(os.path.join(DATA, f"blofin_{coin}_USDT_USDT_5m_180d.parquet")).astype(float)
    if tf == "5m":
        return df5
    rule = {"15m": "15min", "1h": "1h"}[tf]
    return df5.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna()


def evalc(fn, df, side, params, costs, tf):
    sigs = fn(df, side=side, **params)
    return metrics(simulate(df, sigs, costs, RISK, TF_MIN[tf]), RISK.starting_equity)
