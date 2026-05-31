"""Fetch OHLCV for the scalping basket from OKX (reachable from this IP; deep
history via the history-candles endpoint, accessed through `since`-forward paging).

Single consistent source for the whole 2026-05-30 search. Writes parquet into
this folder's ./data/ (isolated from the BloFin caches in sweeps/2026-05-20/data).

Run:  python fetch_okx.py
"""
from __future__ import annotations
import os, sys, time
import ccxt
import pandas as pd

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "data")
os.makedirs(OUT, exist_ok=True)

COINS = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
SYM = {c: f"{c}/USDT:USDT" for c in COINS}

# (timeframe, days_back). 15m is resampled from 5m by the loader -> not fetched.
# Windows sized for ample walk-forward sample at each tf without over-paging.
JOBS = [("1m", 45), ("3m", 120), ("5m", 180)]

PAGE = 100        # OKX history-candles caps ~100/page
# When running 5 coins in parallel from one IP, pace each process so the COMBINED
# rate stays under OKX's ~10 req/s public cap (5 * 1/0.6s ~= 8.3/s).
SLEEP = float(os.environ.get("OKX_SLEEP", "0.6"))


def fetch(ex, sym, tf, days):
    end = ex.milliseconds()
    since = end - days * 86400 * 1000
    bars = {}
    cursor = since
    stall = 0
    while cursor < end:
        try:
            ch = ex.fetch_ohlcv(sym, tf, since=cursor, limit=PAGE)
        except Exception as e:
            stall += 1
            wait = min(20.0, 1.5 ** stall)
            print(f"    {sym} {tf} fail (try {stall}) sleep {wait:.0f}s: {type(e).__name__}", flush=True)
            if stall >= 8:
                print(f"    giving up paging {sym} {tf}; partial", flush=True)
                break
            time.sleep(wait); continue
        stall = 0
        if not ch:
            break
        new = 0
        for b in ch:
            if b[0] not in bars:
                bars[b[0]] = b; new += 1
        last_ts = ch[-1][0]
        if last_ts <= cursor:        # no forward progress -> done
            break
        cursor = last_ts + 1
        if new == 0:
            break
        time.sleep(SLEEP)
    ks = sorted(bars)
    df = pd.DataFrame([bars[k] for k in ks], columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def main():
    # Optional single-coin mode for parallel runs: `python fetch_okx.py SOL`
    coins = [sys.argv[1]] if len(sys.argv) > 1 else COINS
    ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    summary = []
    for tf, days in JOBS:
        for c in coins:
            print(f"\n=== {c} {tf} ({days}d) ===", flush=True)
            df = fetch(ex, SYM[c], tf, days)
            if len(df) == 0:
                summary.append((c, tf, "FAIL", 0, 0.0)); continue
            path = os.path.join(OUT, f"okx_{c}_{tf}.parquet")
            df.to_parquet(path)
            cov = (df.index[-1] - df.index[0]).total_seconds() / 86400.0
            print(f"  saved {len(df)} bars {df.index[0]} -> {df.index[-1]} ({cov:.0f}d)", flush=True)
            summary.append((c, tf, "ok", len(df), cov))
    print("\n===== SUMMARY =====", flush=True)
    for c, tf, st, n, cov in summary:
        print(f"{c:6}{tf:5}{st:8}{n:>9}{cov:>8.0f}d", flush=True)


if __name__ == "__main__":
    main()
