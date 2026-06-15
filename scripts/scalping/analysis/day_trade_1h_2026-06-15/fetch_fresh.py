"""Fetch fresh OKX 5m for the squeeze basket (SOL/ETH/ZEC/HYPE) up to NOW.
200-day window -> ends ~today, so June 1-15 is genuinely never-seen forward data.
Single process -> can page faster than the 5-parallel search (OKX_SLEEP=0.15).
Writes parquet into ./data_june/ (isolated from the May caches)."""
from __future__ import annotations
import os, time
import ccxt
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "data_june")
os.makedirs(OUT, exist_ok=True)
COINS = ["SOL", "ETH", "ZEC", "HYPE"]
SYM = {c: f"{c}/USDT:USDT" for c in COINS}
TF, DAYS, PAGE, SLEEP = "5m", 200, 100, 0.15


def fetch(ex, sym, tf, days):
    end = ex.milliseconds(); cursor = end - days * 86400 * 1000
    bars = {}; stall = 0
    while cursor < end:
        try:
            ch = ex.fetch_ohlcv(sym, tf, since=cursor, limit=PAGE)
        except Exception as e:
            stall += 1; wait = min(20.0, 1.5 ** stall)
            print(f"  {sym} fail {stall} sleep {wait:.0f}s {type(e).__name__}", flush=True)
            if stall >= 8: break
            time.sleep(wait); continue
        stall = 0
        if not ch: break
        new = 0
        for b in ch:
            if b[0] not in bars: bars[b[0]] = b; new += 1
        last = ch[-1][0]
        if last <= cursor: break
        cursor = last + 1
        if new == 0: break
        time.sleep(SLEEP)
    ks = sorted(bars)
    df = pd.DataFrame([bars[k] for k in ks], columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True); df.index.name = "ts"
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def main():
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    for c in COINS:
        print(f"=== {c} {TF} ({DAYS}d) ===", flush=True)
        df = fetch(ex, SYM[c], TF, DAYS)
        path = os.path.join(OUT, f"okx_{c}_5m.parquet")
        df.to_parquet(path)
        print(f"  {c}: {len(df)} bars {df.index.min()} -> {df.index.max()}", flush=True)


if __name__ == "__main__":
    main()
