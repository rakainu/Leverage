"""Local engine for V3 Pine-replay backtests.

Provides:
  - Indicator helpers (EMA, ATR, SMMA) matching Pine Script semantics.
  - OHLCV loader pulling from BloFin via ccxt, cached on disk as parquet.

Used by strategy.py to regenerate V3 signals locally — no PineLab dependency.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / "data"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Indicators (Pine Script equivalents) ----------

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.ema(src, length)`. Standard EMA with seed = SMA of first `period` values."""
    out = series.ewm(span=period, adjust=False, min_periods=period).mean()
    # Seed the first valid value as SMA, matching Pine's ta.ema seed behavior
    if len(series) >= period:
        seed = series.iloc[:period].mean()
        # Replace the value at index period-1 with the SMA seed, then re-roll EMA forward
        out = out.copy()
        alpha = 2.0 / (period + 1)
        vals = out.values.copy()
        s_vals = series.values
        vals[period - 1] = seed
        for i in range(period, len(vals)):
            vals[i] = alpha * s_vals[i] + (1 - alpha) * vals[i - 1]
        out = pd.Series(vals, index=series.index)
    return out


def calc_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Pine `ta.atr(length)` — SMMA of True Range. Requires High/Low/Close cols."""
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return calc_smma(tr, period)


def calc_smma(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.rma(src, length)` / Wilder SMMA. Seed = SMA of first `period` values."""
    vals = series.values.astype(float)
    out = np.full_like(vals, np.nan, dtype=float)
    if len(vals) < period:
        return pd.Series(out, index=series.index)
    # Initial seed: SMA over first `period` values (drop NaNs)
    seed_window = vals[:period]
    seed_window = seed_window[~np.isnan(seed_window)]
    if len(seed_window) == 0:
        return pd.Series(out, index=series.index)
    out[period - 1] = seed_window.mean()
    alpha = 1.0 / period
    for i in range(period, len(vals)):
        x = vals[i]
        if np.isnan(x):
            out[i] = out[i - 1]
            continue
        out[i] = alpha * x + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index)


# ---------- OHLCV loader ----------

_TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}


def _ccxt_blofin() -> ccxt.Exchange:
    return ccxt.blofin({"options": {"defaultType": "swap"}, "enableRateLimit": True})


def _ccxt_binance() -> ccxt.Exchange:
    return ccxt.binance({"options": {"defaultType": "future"}, "enableRateLimit": True})


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "5m",
    days_back: int = 180,
    exchange: str = "blofin",
    cache: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Fetch OHLCV bars for `symbol` going back `days_back` from now.

    Args:
        symbol: ccxt format like "SOL/USDT:USDT" (BloFin perp) or "BTCUSDT" (Binance).
        timeframe: e.g. "5m"
        days_back: how many days of history to pull
        exchange: "blofin" or "binance"
        cache: read/write parquet cache in ./data/
        verbose: print fetch progress

    Returns:
        DataFrame indexed by datetime (UTC), columns Open/High/Low/Close/Volume.
    """
    tf_ms = _TF_MS[timeframe]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days_back * 86_400_000

    cache_key = f"{exchange}_{symbol.replace('/', '_').replace(':', '_')}_{timeframe}_{days_back}d.parquet"
    cache_path = CACHE_DIR / cache_key

    if cache and cache_path.exists():
        age_h = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_h < 6:  # refresh every 6h
            df = pd.read_parquet(cache_path)
            if verbose:
                print(f"  [cache] {cache_path.name}  bars={len(df)}  ({df.index[0]} -> {df.index[-1]})")
            return df

    if exchange == "blofin":
        client = _ccxt_blofin()
    elif exchange == "binance":
        client = _ccxt_binance()
    else:
        raise ValueError(f"unsupported exchange: {exchange}")

    if verbose:
        print(f"  [fetch] {exchange}:{symbol} {timeframe} from "
              f"{pd.to_datetime(start_ms, unit='ms', utc=True)} ...")

    all_bars: list[list[float]] = []
    cursor = end_ms
    page_size = 1000  # both blofin and binance accept up to 1000 candles/page
    fails = 0            # consecutive failures at the current cursor
    MAX_FAILS = 8        # give up paging after this many retries (no infinite loop)
    while cursor > start_ms:
        try:
            chunk = client.fetch_ohlcv(symbol, timeframe=timeframe, limit=page_size,
                                       params={"until": cursor})
            fails = 0
        except Exception as exc:
            fails += 1
            msg = repr(exc)
            # Cloudflare challenge / geo block -> exponential backoff, then bail
            backoff = min(30.0, 1.5 ** fails)
            short = "cloudflare/challenge" if ("challenge" in msg.lower() or "restricted" in msg.lower()) else msg[:120]
            print(f"    fetch failed @{cursor} (try {fails}/{MAX_FAILS}, sleep {backoff:.0f}s): {short}",
                  file=sys.stderr)
            if fails >= MAX_FAILS:
                print(f"    giving up at cursor {cursor} after {fails} fails; returning partial", file=sys.stderr)
                break
            time.sleep(backoff)
            continue
        if not chunk:
            break
        all_bars = chunk + all_bars
        cursor = chunk[0][0] - 1
        if len(chunk) < page_size // 2:
            break
        if verbose and len(all_bars) % 5000 < page_size:
            print(f"    ... {len(all_bars)} bars, cursor @ "
                  f"{pd.to_datetime(cursor, unit='ms', utc=True)}")
        time.sleep(0.05)

    # Deduplicate by ts, sort, filter to window
    seen = set()
    uniq = []
    for b in all_bars:
        if b[0] in seen:
            continue
        seen.add(b[0])
        uniq.append(b)
    uniq.sort(key=lambda b: b[0])
    uniq = [b for b in uniq if start_ms <= b[0] <= end_ms]

    df = pd.DataFrame(uniq, columns=["ts_ms", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)

    if cache:
        df.to_parquet(cache_path)
    if verbose:
        print(f"  [fetched] bars={len(df)}  ({df.index[0]} -> {df.index[-1]})")
    return df


# Convenience symbol map: friendly name -> (ccxt_symbol, exchange)
SYMBOL_MAP = {
    "SOL": ("SOL/USDT:USDT", "blofin"),
    "ZEC": ("ZEC/USDT:USDT", "blofin"),
    "BTC": ("BTC/USDT:USDT", "blofin"),
    "ETH": ("ETH/USDT:USDT", "blofin"),
}


def load_symbol(name: str, timeframe: str = "5m", days_back: int = 180,
                exchange: Optional[str] = None) -> pd.DataFrame:
    """Convenience: load by friendly name (SOL, ZEC). Falls back to Binance if BloFin fails."""
    if name in SYMBOL_MAP:
        sym, default_ex = SYMBOL_MAP[name]
        ex = exchange or default_ex
    else:
        sym = name
        ex = exchange or "blofin"
    try:
        return fetch_ohlcv(sym, timeframe, days_back, exchange=ex)
    except Exception as exc:
        if exchange is None and ex == "blofin":
            print(f"  blofin failed ({exc}), falling back to binance")
            binance_sym = sym.split(":")[0].replace("/", "")  # SOL/USDT:USDT -> SOLUSDT
            return fetch_ohlcv(binance_sym, timeframe, days_back, exchange="binance")
        raise


if __name__ == "__main__":
    # Quick smoke test
    print("=" * 60)
    print("ENGINE SMOKE TEST")
    print("=" * 60)
    df = load_symbol("ZEC", "5m", days_back=7)
    print(f"\nZEC 5m last 7d: {len(df)} bars")
    print(df.tail(3))
    print(f"\nEMA(9) last: {calc_ema(df['Close'], 9).iloc[-1]:.4f}")
    print(f"ATR(14) last: {calc_atr(df, 14).iloc[-1]:.4f}")
    print(f"SMMA(14) close last: {calc_smma(df['Close'], 14).iloc[-1]:.4f}")
