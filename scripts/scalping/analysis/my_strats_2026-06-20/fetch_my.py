"""Fetch a wide liquid basket from OKX (5m, ~90d) for the new strategy search.
Writes parquet into ./data/. 15m/1h are resampled by the loader, not fetched.

Run: ../../venv/Scripts/python.exe fetch_my.py [days]
"""
from __future__ import annotations
import os, sys, time
import ccxt
import pandas as pd

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "data")
os.makedirs(OUT, exist_ok=True)

COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "AVAX", "LINK", "SUI", "HYPE"]
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 90


def fetch(coin, days):
    ex = ccxt.okx({"enableRateLimit": True})
    sym = f"{coin}/USDT:USDT"
    end = ex.milliseconds()
    cursor = end - days * 86400 * 1000
    rows = {}
    stall = 0
    while cursor < end and stall < 4:
        try:
            ch = ex.fetch_ohlcv(sym, "5m", since=cursor, limit=300)
        except Exception as e:
            print(f"  {coin} err: {e}"); stall += 1; time.sleep(1); continue
        if not ch:
            stall += 1; cursor += 300 * 5 * 60 * 1000; continue
        stall = 0
        for t, o, h, l, c, v in ch:
            rows[t] = (o, h, l, c, v)
        cursor = ch[-1][0] + 5 * 60 * 1000
        time.sleep(0.2)
    if not rows:
        return None
    df = pd.DataFrame([(k, *v) for k, v in sorted(rows.items())],
                      columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts").astype(float)


def main():
    print(f"OKX 5m, {DAYS}d, basket={COINS}")
    for c in COINS:
        df = fetch(c, DAYS)
        if df is None or len(df) < 1000:
            print(f"  {c}: UNAVAILABLE / too short — skipped"); continue
        path = os.path.join(OUT, f"{c}_5m.parquet")
        df.to_parquet(path)
        print(f"  {c}: {len(df)} bars {df.index[0].date()}->{df.index[-1].date()} -> {os.path.basename(path)}")
    print("done")


if __name__ == "__main__":
    main()
