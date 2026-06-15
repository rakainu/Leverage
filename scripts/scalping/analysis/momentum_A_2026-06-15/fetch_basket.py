"""Fetch fresh OKX 15m for a broad, volatile basket through NOW (250d) for the
momentum/breakout research (Approach A). 15m fetched natively (fast; fewer pages
than 5m). June-forward slice falls out for free. Writes ./data/okx_<C>_15m.parquet.

Broad basket so the edge has to GENERALIZE, not luck onto one coin. Failures drop."""
from __future__ import annotations
import os, sys, time
import ccxt
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
COINS = ["BTC", "ETH", "SOL", "ZEC", "HYPE", "BNB", "XMR", "DOGE", "AVAX", "LINK", "SUI", "TON"]
SYM = {c: f"{c}/USDT:USDT" for c in COINS}
TF, DAYS, PAGE, SLEEP = "15m", 250, 100, 0.15


def fetch(ex, sym, tf, days):
    end = ex.milliseconds(); cursor = end - days * 86400 * 1000
    bars = {}; stall = 0
    while cursor < end:
        try:
            ch = ex.fetch_ohlcv(sym, tf, since=cursor, limit=PAGE)
        except Exception as e:
            stall += 1; wait = min(20.0, 1.5 ** stall)
            print(f"  {sym} fail {stall} {type(e).__name__} sleep {wait:.0f}s", flush=True)
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
    only = sys.argv[1:] or COINS
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    for c in only:
        try:
            df = fetch(ex, SYM[c], TF, DAYS)
            if len(df) < 1000:
                print(f"  {c}: only {len(df)} bars — SKIP", flush=True); continue
            df.to_parquet(os.path.join(OUT, f"okx_{c}_15m.parquet"))
            print(f"  {c}: {len(df)} bars {df.index.min()} -> {df.index.max()}", flush=True)
        except Exception as e:
            print(f"  {c}: FAILED {type(e).__name__} {e}", flush=True)


if __name__ == "__main__":
    main()
