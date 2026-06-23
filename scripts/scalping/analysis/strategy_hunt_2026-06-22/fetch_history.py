"""Fetch multi-YEAR OKX perp history (1h + 4h) for a fair, multi-regime test set.

The existing OKX parquet is only ~6 months (Dec 2025-Jun 2026), which turned out
to be a choppy bear market — an adverse, unrepresentative sample for directional
strategies. This pulls ~3 years so backtests span bull + bear + chop. OKX only
(Binance/Bybit geo-blocked). Saved to ./data_hist/okx_<COIN>_<tf>.parquet.
"""
from __future__ import annotations
import os
import sys
import time

import ccxt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data_hist")
os.makedirs(OUT, exist_ok=True)

# long-history majors on OKX perps (skip very new listings like HYPE)
COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX", "LINK", "LTC"]
TFS = ["1h", "4h", "15m", "5m"]
DAYS = 1100  # ~3 years
TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "15m": 900_000, "5m": 300_000}


def fetch(client, symbol, tf, days):
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    cursor = start                     # paginate FORWARD with `since` (hits OKX history endpoint)
    bars, fails = [], 0
    while cursor < end:
        try:
            chunk = client.fetch_ohlcv(symbol, timeframe=tf, since=cursor, limit=100)
            fails = 0
        except Exception as exc:
            fails += 1
            if fails >= 6:
                print(f"    give up {symbol} {tf} @ {cursor}: {repr(exc)[:80]}", file=sys.stderr); break
            time.sleep(min(20, 1.5 ** fails)); continue
        if not chunk:
            break
        bars += chunk
        last = chunk[-1][0]
        if last <= cursor:
            break
        cursor = last + 1
        time.sleep(0.10)
    seen, uniq = set(), []
    for b in bars:
        if b[0] in seen or not (start <= b[0] <= end):
            continue
        seen.add(b[0]); uniq.append(b)
    uniq.sort(key=lambda b: b[0])
    df = pd.DataFrame(uniq, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def main():
    client = ccxt.okx({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    for coin in COINS:
        sym = f"{coin}/USDT:USDT"
        for tf in TFS:
            path = os.path.join(OUT, f"okx_{coin}_{tf}.parquet")
            if os.path.exists(path):
                print(f"  skip {coin:<5}{tf:<4} (cached)", flush=True)
                continue
            try:
                df = fetch(client, sym, tf, DAYS)
            except Exception as exc:
                print(f"  FAIL {coin} {tf}: {repr(exc)[:100]}", file=sys.stderr); continue
            if len(df) < 100:
                print(f"  thin {coin} {tf}: {len(df)} bars", file=sys.stderr); continue
            path = os.path.join(OUT, f"okx_{coin}_{tf}.parquet")
            df.to_parquet(path)
            yrs = (df.index[-1] - df.index[0]).days / 365.0
            print(f"  {coin:<5}{tf:<4} {len(df):>6} bars  {df.index[0].date()} -> {df.index[-1].date()} ({yrs:.1f}y)", flush=True)


if __name__ == "__main__":
    main()
