"""One-shot analyzer: pulls every trade from bridge.db, fetches 5m OHLCV
context at entry from BloFin (public, no auth), and tags each trade with
trend/chop/slope/distance/range/hour features. Prints winner-vs-loser
distributions and writes a CSV for further inspection.

Run inside the scalping container so we reuse its ccxt + sqlite envs:
  docker exec scalping python /tmp/trade_context_audit.py
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median

import ccxt

DB_PATH = "/app/data/bridge.db"
OUT_CSV = "/tmp/trade_context.csv"
EMA_LEN = 9
LOOKBACK_BARS = 60   # we need >= EMA_LEN + a slope window + cushion
SLOPE_WINDOW = 3     # bars over which we measure the EMA slope ratio
HTF_BARS = 40        # 1h bars to characterize higher-timeframe trend


def to_iso(ts_str: str) -> int:
    """Parse the bridge's ISO timestamp into a millisecond epoch."""
    return int(datetime.fromisoformat(ts_str).astimezone(timezone.utc).timestamp() * 1000)


def make_client() -> ccxt.Exchange:
    # Public OHLCV — no key needed, but route demo trades to live endpoint
    # because demo endpoint serves the same public market data anyway.
    client = ccxt.blofin({
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    return client


def ema(values: list[float], length: int) -> list[float]:
    if len(values) < length:
        return []
    k = 2.0 / (length + 1.0)
    out: list[float] = []
    seed = sum(values[:length]) / length
    out.append(seed)
    for v in values[length:]:
        out.append(out[-1] + k * (v - out[-1]))
    # align: out[i] corresponds to values[length-1 + i]
    return out


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    tr: list[float] = []
    for i in range(len(closes)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            tr.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            ))
    return tr


def atr(tr: list[float], length: int = 14) -> list[float]:
    if len(tr) < length:
        return []
    out = [sum(tr[:length]) / length]
    for v in tr[length:]:
        out.append((out[-1] * (length - 1) + v) / length)
    return out


def fetch_bars(client: ccxt.Exchange, ccxt_symbol: str, timeframe: str,
               until_ms: int, limit: int) -> list[list[float]]:
    """Fetch up to `limit` candles whose close time is <= until_ms.
    BloFin ignores `since` at request time; we use params={'until': ms},
    which ccxt maps to BloFin's `after` cursor (= bars before this ts).
    """
    return client.fetch_ohlcv(
        ccxt_symbol, timeframe=timeframe, limit=limit,
        params={"until": until_ms},
    )


def context_for_trade(client: ccxt.Exchange, symbol: str, opened_at_ms: int) -> dict | None:
    """Return market-context features at entry, or None if data unavailable."""
    base, quote = symbol.split("-")
    ccxt_sym = f"{base}/{quote}:{quote}"

    # 5m: ask for bars *up to* entry timestamp. BloFin caps at 100 per call;
    # we just need ~60 bars before entry for EMA + ATR + range stats.
    bars_5m = fetch_bars(client, ccxt_sym, "5m", opened_at_ms, 100)
    # Keep only bars that closed AT or BEFORE entry (no lookahead).
    bars_5m = [b for b in bars_5m if b[0] <= opened_at_ms]
    bars_5m.sort(key=lambda b: b[0])
    if len(bars_5m) < EMA_LEN + SLOPE_WINDOW + 2:
        return None

    closes_5m = [b[4] for b in bars_5m]
    highs_5m = [b[2] for b in bars_5m]
    lows_5m = [b[3] for b in bars_5m]

    ema_series = ema(closes_5m, EMA_LEN)
    if len(ema_series) < SLOPE_WINDOW + 1:
        return None

    last_close = closes_5m[-1]
    last_ema = ema_series[-1]
    prior_ema = ema_series[-1 - SLOPE_WINDOW]
    # slope as % move of EMA over the window — direction-agnostic measure
    slope_pct = (last_ema - prior_ema) / prior_ema * 100.0

    dist_to_ema_pct = (last_close - last_ema) / last_ema * 100.0

    tr_5m = true_range(highs_5m, lows_5m, closes_5m)
    atr_5m = atr(tr_5m, 14)
    last_atr = atr_5m[-1] if atr_5m else None
    atr_pct = (last_atr / last_close * 100.0) if last_atr else None

    # Last-N candle range as a chop proxy: how big is the high-low envelope
    # over the last 10 bars vs the 50-bar reference?
    n_short = 10
    n_long = min(50, len(highs_5m))
    range_short = max(highs_5m[-n_short:]) - min(lows_5m[-n_short:])
    range_long = max(highs_5m[-n_long:]) - min(lows_5m[-n_long:])
    chop_ratio = range_short / range_long if range_long > 0 else None

    # Higher-timeframe trend: 1h EMA(9) slope over last few bars.
    bars_1h = fetch_bars(client, ccxt_sym, "1h", opened_at_ms, 100)
    bars_1h = [b for b in bars_1h if b[0] <= opened_at_ms]
    bars_1h.sort(key=lambda b: b[0])
    htf_slope_pct = None
    if len(bars_1h) >= EMA_LEN + 3:
        closes_1h = [b[4] for b in bars_1h]
        ema_1h = ema(closes_1h, EMA_LEN)
        if len(ema_1h) >= 4:
            htf_slope_pct = (ema_1h[-1] - ema_1h[-4]) / ema_1h[-4] * 100.0

    hour_utc = datetime.fromtimestamp(opened_at_ms / 1000, tz=timezone.utc).hour

    return {
        "last_close": last_close,
        "ema9_5m": last_ema,
        "slope5m_pct_3bar": slope_pct,
        "dist_to_ema_pct": dist_to_ema_pct,
        "atr_5m": last_atr,
        "atr_pct": atr_pct,
        "chop_ratio_10v50": chop_ratio,
        "htf_1h_slope_pct_3bar": htf_slope_pct,
        "hour_utc": hour_utc,
    }


def classify(side: str, slope: float, htf_slope: float | None) -> str:
    """Bucket each trade as 'with-trend' / 'against-trend' / 'flat'.

    'flat' if |slope| < 0.05% on the 5m EMA — that's roughly a sideways tape.
    Otherwise compare slope sign to side direction.
    """
    if slope is None:
        return "unknown"
    if abs(slope) < 0.05:
        return "flat"
    if side == "long":
        return "with-trend" if slope > 0 else "against-trend"
    if side == "short":
        return "with-trend" if slope < 0 else "against-trend"
    return "unknown"


def main() -> int:
    client = make_client()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, symbol, side, entry_price, exit_price, pnl_usdt, exit_reason, "
        "opened_at, closed_at, duration_secs FROM trade_log ORDER BY opened_at"
    ).fetchall()
    con.close()

    enriched: list[dict] = []
    for r in rows:
        try:
            opened_ms = to_iso(r["opened_at"])
            ctx = context_for_trade(client, r["symbol"], opened_ms)
        except Exception as exc:
            print(f"[warn] trade {r['id']} {r['symbol']}: {exc}", file=sys.stderr)
            ctx = None

        if ctx is None:
            continue

        bucket = classify(r["side"], ctx["slope5m_pct_3bar"], ctx["htf_1h_slope_pct_3bar"])
        enriched.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "side": r["side"],
            "pnl": r["pnl_usdt"],
            "win": r["pnl_usdt"] > 0,
            "exit_reason": r["exit_reason"],
            "duration_min": (r["duration_secs"] or 0) / 60.0,
            "trend_bucket": bucket,
            **ctx,
        })

    if not enriched:
        print("No enriched trades — OHLCV fetch may have failed.")
        return 1

    # Write CSV.
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(enriched[0].keys()))
        w.writeheader()
        w.writerows(enriched)
    print(f"Wrote {OUT_CSV} with {len(enriched)} rows")

    # Aggregations.
    def summarize(rows: list[dict], label: str) -> None:
        if not rows:
            print(f"  [{label}] (no trades)")
            return
        n = len(rows)
        wins = sum(1 for r in rows if r["win"])
        losses = n - wins
        pnl = sum(r["pnl"] for r in rows)
        avg_pnl = pnl / n
        wr = wins / n * 100
        avg_slope = mean(r["slope5m_pct_3bar"] for r in rows)
        avg_dist = mean(r["dist_to_ema_pct"] for r in rows)
        avg_atrp = mean(r["atr_pct"] for r in rows if r["atr_pct"] is not None)
        avg_chop = mean(r["chop_ratio_10v50"] for r in rows if r["chop_ratio_10v50"] is not None)
        avg_htf = mean(r["htf_1h_slope_pct_3bar"] for r in rows if r["htf_1h_slope_pct_3bar"] is not None)
        print(f"  [{label}] n={n:3d} wr={wr:5.1f}% pnl={pnl:8.2f} avg={avg_pnl:6.2f} | "
              f"slope5m={avg_slope:+.3f}% dist={avg_dist:+.3f}% atr%={avg_atrp:.3f} "
              f"chop={avg_chop:.2f} htf1h={avg_htf:+.3f}%")

    print("\n=== Overall winners vs losers ===")
    summarize([r for r in enriched if r["win"]], "WIN ")
    summarize([r for r in enriched if not r["win"]], "LOSS")

    print("\n=== By trend bucket ===")
    for bucket in ("with-trend", "flat", "against-trend", "unknown"):
        summarize([r for r in enriched if r["trend_bucket"] == bucket], bucket)

    print("\n=== By symbol x trend bucket ===")
    for sym in ("SOL-USDT", "ZEC-USDT"):
        for bucket in ("with-trend", "flat", "against-trend"):
            sub = [r for r in enriched if r["symbol"] == sym and r["trend_bucket"] == bucket]
            summarize(sub, f"{sym} {bucket}")

    print("\n=== By symbol x side ===")
    for sym in ("SOL-USDT", "ZEC-USDT"):
        for side in ("long", "short"):
            for bucket in ("with-trend", "flat", "against-trend"):
                sub = [r for r in enriched if r["symbol"] == sym
                       and r["side"] == side and r["trend_bucket"] == bucket]
                summarize(sub, f"{sym} {side} {bucket}")

    print("\n=== Slope buckets (5m EMA9, %, signed by side) ===")
    # signed slope: positive = with the side
    def signed_slope(r: dict) -> float:
        s = r["slope5m_pct_3bar"]
        return s if r["side"] == "long" else -s

    bands = [
        ("strong-against", -math.inf, -0.10),
        ("mild-against",   -0.10,     -0.03),
        ("flat",           -0.03,      0.03),
        ("mild-with",       0.03,      0.10),
        ("strong-with",     0.10,      math.inf),
    ]
    for label, lo, hi in bands:
        sub = [r for r in enriched if lo <= signed_slope(r) < hi]
        summarize(sub, f"{label:14s}")

    print("\n=== Distance-from-EMA buckets (5m, %, signed by side) ===")
    def signed_dist(r: dict) -> float:
        d = r["dist_to_ema_pct"]
        return d if r["side"] == "long" else -d

    dist_bands = [
        ("deep-counter", -math.inf, -0.30),
        ("at-ema",       -0.30,      0.05),
        ("just-past",     0.05,      0.20),
        ("extended",      0.20,      math.inf),
    ]
    for label, lo, hi in dist_bands:
        sub = [r for r in enriched if lo <= signed_dist(r) < hi]
        summarize(sub, f"{label:14s}")

    print("\n=== Hour-of-day (UTC) ===")
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for r in enriched:
        by_hour[r["hour_utc"]].append(r)
    for h in sorted(by_hour):
        summarize(by_hour[h], f"h{h:02d}")

    print("\n=== HTF (1h EMA9 slope) buckets — signed by side ===")
    def signed_htf(r: dict) -> float:
        s = r.get("htf_1h_slope_pct_3bar")
        if s is None:
            return 0.0
        return s if r["side"] == "long" else -s

    htf_bands = [
        ("htf-against", -math.inf, -0.10),
        ("htf-flat",    -0.10,      0.10),
        ("htf-with",     0.10,      math.inf),
    ]
    for label, lo, hi in htf_bands:
        sub = [r for r in enriched if lo <= signed_htf(r) < hi]
        summarize(sub, f"{label:12s}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
