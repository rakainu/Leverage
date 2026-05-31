"""Fetch 1m + 3m + 5m + 15m OHLCV for the scalping basket, cached as parquet.

Reuses the proven ccxt BloFin fetcher (sweeps/2026-05-20/engine.py) with a
Binance fallback for any (symbol, tf) where BloFin caps history short.

Run:  python fetch_data.py
Cache lands in ../sweeps/2026-05-20/data/ so all existing loaders see it too.
"""
from __future__ import annotations
import os, sys, time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sweeps", "2026-05-20"))
from engine import fetch_ohlcv, SYMBOL_MAP  # noqa: E402

COINS = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
DAYS = 180

# Only fetch what's MISSING. 5m exists for all coins (180d); 3m exists for SOL/ZEC;
# 15m is resampled from 5m by the loaders (no fetch). So we need:
#   1m  -> all 5 coins
#   3m  -> ETH, HYPE, BTC  (SOL, ZEC already cached)
JOBS = [(c, "1m") for c in COINS] + [(c, "3m") for c in ("ETH", "HYPE", "BTC")]

# HYPE is not in the engine SYMBOL_MAP; add it.
SYMBOL_MAP.setdefault("HYPE", ("HYPE/USDT:USDT", "blofin"))

MIN_DAYS_OK = 60  # below this we try Binance for deeper history


def coverage_days(df):
    return (df.index[-1] - df.index[0]).total_seconds() / 86400.0


def main():
    summary = []
    for coin, tf in JOBS:
            ccxt_sym, _ = SYMBOL_MAP[coin]
            print(f"\n=== {coin} {tf} ===", flush=True)
            df = None
            try:
                df = fetch_ohlcv(ccxt_sym, tf, DAYS, exchange="blofin")
            except Exception as exc:
                print(f"  blofin failed: {exc}", flush=True)
            # Fall back to Binance if BloFin gave too little 1m/3m history
            if df is None or coverage_days(df) < MIN_DAYS_OK:
                got = 0 if df is None else coverage_days(df)
                print(f"  blofin coverage {got:.0f}d < {MIN_DAYS_OK}d -> trying binance", flush=True)
                bn_sym = ccxt_sym.split(":")[0].replace("/", "")  # SOL/USDT:USDT -> SOLUSDT
                try:
                    bdf = fetch_ohlcv(bn_sym, tf, DAYS, exchange="binance")
                    if df is None or coverage_days(bdf) > coverage_days(df):
                        df = bdf
                except Exception as exc:
                    print(f"  binance failed: {exc}", flush=True)
            if df is None or len(df) == 0:
                summary.append((coin, tf, "FAIL", 0, 0.0))
                continue
            # Always write a canonical blofin-style name so every loader finds it,
            # regardless of which venue actually served the bars.
            from engine import CACHE_DIR
            canon = CACHE_DIR / f"blofin_{coin}_USDT_USDT_{tf}_180d.parquet"
            df.to_parquet(canon)
            summary.append((coin, tf, "ok", len(df), coverage_days(df)))
            time.sleep(0.2)

    print("\n\n===== COVERAGE SUMMARY =====", flush=True)
    print(f"{'coin':6}{'tf':5}{'status':8}{'bars':>9}{'days':>8}")
    for coin, tf, st, n, days in summary:
        print(f"{coin:6}{tf:5}{st:8}{n:>9}{days:>8.0f}", flush=True)


if __name__ == "__main__":
    main()
