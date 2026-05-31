"""Shared loader + runner for the 2026-05-30 aggressive scalping search."""
from __future__ import annotations
import os, sys
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import simulate, metrics, fmt, split_is_oos, walk_forward_folds, Costs, RiskCfg  # noqa: E402
import strat_lib as SL  # noqa: E402

DATA = os.path.join(HERE, "data")   # OKX-sourced parquet for this search (one venue)
COINS = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
TFS = ["1m", "3m", "5m", "15m"]
TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "1h": 60}

# Lighter-style zero-fee perp: no maker/taker fee; realistic slippage on market &
# stop fills; funding still applies. HISLIP doubles slippage for stress tests.
LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
LIGHTER_HISLIP = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.10, funding_pct_per_8h=0.01)
BLOFIN = Costs()  # taker .06 / maker .02 / slip .05 -> fee drag reference

RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=True)


def _path(coin, tf):
    return os.path.join(DATA, f"okx_{coin}_{tf}.parquet")


def load(coin: str, tf: str) -> pd.DataFrame:
    """Load OHLCV (OKX). 1m/3m/5m native parquet; 15m resampled from native 5m."""
    if tf in ("1m", "3m", "5m"):
        if os.path.exists(_path(coin, tf)):
            return pd.read_parquet(_path(coin, tf)).astype(float)
        raise FileNotFoundError(_path(coin, tf))
    if tf == "15m":
        df5 = pd.read_parquet(_path(coin, "5m")).astype(float)
        return df5.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                          "Close": "last", "Volume": "sum"}).dropna()
    raise ValueError(tf)


def weeks_of(df: pd.DataFrame, tf: str) -> float:
    return len(df) * TF_MIN[tf] / (60 * 24 * 7)


def run(family: str, df: pd.DataFrame, tf: str, side: str, params: dict,
        costs=LIGHTER, risk=RISK) -> dict:
    """Generate signals, simulate honestly, return metrics + frequency."""
    fn = SL.REGISTRY[family]
    sigs = fn(df, side=side, **params)
    trades = simulate(df, sigs, costs, risk, TF_MIN[tf])
    m = metrics(trades, risk.starting_equity)
    wk = weeks_of(df, tf)
    m["signals"] = len(sigs)
    m["trades_per_wk"] = m["n"] / wk if wk > 0 else 0.0
    m["weeks"] = wk
    # average win / loss in $ for reporting
    wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losses = [t.pnl_usd for t in trades if t.pnl_usd < 0]
    m["avg_win"] = (sum(wins) / len(wins)) if wins else 0.0
    m["avg_loss"] = (sum(losses) / len(losses)) if losses else 0.0
    m["avg_hold_min"] = m["avg_bars"] * TF_MIN[tf]
    return m, trades


# Sensible STAGE-1 defaults per family (loose enough to fire often on LTFs).
DEFAULTS = {
    "regime_mr":       dict(trend_len=200, z_period=30, z_entry=1.5, sl_atr=1.5, tp_frac=0.4, max_bars=12, limit_atr=0.25),
    "bb_revert":       dict(length=20, mult=2.0, sl_atr=1.5, tp_frac=1.0, max_bars=24),
    "kc_revert":       dict(length=20, mult=2.0, sl_atr=1.5, tp_frac=1.0, max_bars=24),
    "rsi_snapback":    dict(rsi_p=14, lo=30, hi=70, sl_atr=1.5, tp_atr=1.5, max_bars=24),
    "stoch_snapback":  dict(k_len=14, smooth=3, lo=20, hi=80, sl_atr=1.5, tp_atr=1.5, max_bars=24),
    "vwap_revert":     dict(z_period=40, z_entry=2.0, sl_atr=1.5, tp_frac=0.7, max_bars=24),
    "vwap_reclaim":    dict(sl_atr=1.5, tp_atr=2.0, max_bars=24),
    "wick_fade":       dict(wick_frac=0.6, min_range_atr=1.0, sl_atr=0.5, tp_atr=1.2, max_bars=16),
    "atr_climax_fade": dict(range_mult=2.0, sl_atr=1.0, tp_atr=1.5, max_bars=16),
    "micro_pullback":  dict(impulse_atr=1.5, pull_bars=2, sl_atr=1.0, tp_atr=2.0, max_bars=20),
    "orb_fade":        dict(open_bars=12, sl_atr=1.0, tp_frac=0.7, max_bars=24),
    "range_fade":      dict(lookback=40, edge_frac=0.10, adx_max=30, sl_atr=1.5, max_bars=48),
    "failed_breakout": dict(lookback=20, sl_atr=1.0, tp_atr=2.0, max_bars=48),
    "sweep_reversal":  dict(lookback=20, sl_atr=1.0, tp_atr=2.0, max_bars=48),
    "squeeze_expansion": dict(bb_len=20, sl_atr=1.5, tp_atr=3.0, max_bars=48, min_squeeze=6),
    "reclaim_pullback":  dict(fast=20, slow=100, sl_atr=1.5, tp_atr=3.0, max_bars=48),
    "mr_fade2":        dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, max_bars=48),
}

HDR = (f"{'family':17}{'coin':5}{'tf':4}{'side':5}{'n':>6}{'t/wk':>7}{'PF':>7}"
       f"{'WR%':>6}{'avgR':>8}{'net%':>8}{'DD%':>7}{'hold_m':>8}")


def row(family, coin, tf, side, m) -> str:
    pf = m["profit_factor"]; pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (f"{family:17}{coin:5}{tf:4}{side:5}{m['n']:>6}{m['trades_per_wk']:>7.1f}"
            f"{pfs:>7}{m['win_rate']:>6.0f}{m['avg_r']:>+8.3f}{m['net_pct']:>+8.1f}"
            f"{m['max_dd_pct']:>7.1f}{m['avg_hold_min']:>8.0f}")
