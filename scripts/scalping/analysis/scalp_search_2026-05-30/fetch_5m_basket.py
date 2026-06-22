"""Fetch fresh 5m OHLCV for the FULL live basket (gauntlet Stage 2 data).

Extends the existing 5 coins into the current regime and adds BNB/DOGE/SUI.
15m/30m/1h are resampled from 5m by the loader, so 5m is all we need.

Run:  venv/Scripts/python.exe analysis/scalp_search_2026-05-30/fetch_5m_basket.py
"""
from __future__ import annotations
import os
import time

import ccxt
import pandas as pd

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "data")
os.makedirs(OUT, exist_ok=True)

COINS = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "BNB", "DOGE", "SUI"]
SYM = {c: f"{c}/USDT:USDT" for c in COINS}
DAYS = 200          # ~Dec -> now, ample for IS/OOS + 4-fold walk-forward
PAGE = 100
SLEEP = 0.35


def fetch(ex, sym, days):
    end = ex.milliseconds()
    cursor = end - days * 86400 * 1000
    bars, stall = {}, 0
    while cursor < end:
        try:
            ch = ex.fetch_ohlcv(sym, "5m", since=cursor, limit=PAGE)
        except Exception as e:
            stall += 1
            if stall >= 8:
                print(f"    give up {sym}: {type(e).__name__}", flush=True)
                break
            time.sleep(min(20.0, 1.5 ** stall))
            continue
        stall = 0
        if not ch:
            break
        for b in ch:
            bars[b[0]] = b
        last = ch[-1][0]
        if last <= cursor:
            break
        cursor = last + 1
        time.sleep(SLEEP)
    ks = sorted(bars)
    df = pd.DataFrame([bars[k] for k in ks], columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def main():
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    for c in COINS:
        print(f"=== {c} 5m ({DAYS}d) ===", flush=True)
        try:
            df = fetch(ex, SYM[c], DAYS)
        except Exception as e:
            print(f"  {c} FAILED outright: {e}", flush=True)
            continue
        if len(df) == 0:
            print(f"  {c} no data (symbol not on OKX?)", flush=True)
            continue
        df.to_parquet(os.path.join(OUT, f"okx_{c}_5m.parquet"))
        print(f"  saved {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
