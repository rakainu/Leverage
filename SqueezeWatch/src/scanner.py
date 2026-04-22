"""Universe selection + per-symbol feature extraction.

Bulk endpoints are used for funding (premiumIndex) and 24h volume (ticker_24hr)
to keep weight low. Per-symbol calls cover klines, funding history, OI history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .binance_client import BinanceFuturesClient


log = logging.getLogger(__name__)

# Stablecoin base assets — never tradable in a "squeeze" sense.
STABLE_BASES = {
    "USDC", "BUSD", "TUSD", "FDUSD", "USDP", "USDE", "DAI",
    "USDT", "PAX", "GUSD", "EUR", "EURI", "AEUR",
}


def fetch_universe(client: BinanceFuturesClient, config: dict) -> list:
    """Return list of {symbol, base_asset, onboard_date, age_days}."""
    info = client.exchange_info()
    banlist = set(config.get("scanner", {}).get("ban_list", []))
    now = datetime.now(tz=timezone.utc)

    universe = []
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        base = s.get("baseAsset", "")
        if base in STABLE_BASES:
            continue
        sym = s.get("symbol")
        if sym in banlist:
            continue

        onboard_ms = s.get("onboardDate")
        if onboard_ms:
            onboard_dt = datetime.fromtimestamp(onboard_ms / 1000, tz=timezone.utc)
            age_days = int((now - onboard_dt).total_seconds() / 86400)
            onboard_iso = onboard_dt.date().isoformat()
        else:
            age_days = None
            onboard_iso = None

        universe.append({
            "symbol": sym,
            "base_asset": base,
            "onboard_date": onboard_iso,
            "age_days": age_days,
        })
    return universe


def fetch_bulk_data(client: BinanceFuturesClient) -> tuple:
    """One-shot bulk fetches for funding rates and 24h tickers.

    Returns (funding_map, ticker_map) keyed by symbol.
    """
    premium = client.premium_index()
    tickers = client.ticker_24hr()

    funding_map = {}
    if isinstance(premium, list):
        for p in premium:
            sym = p.get("symbol")
            if sym:
                try:
                    funding_map[sym] = float(p.get("lastFundingRate") or 0)
                except (TypeError, ValueError):
                    pass

    ticker_map = {}
    if isinstance(tickers, list):
        for t in tickers:
            sym = t.get("symbol")
            if not sym:
                continue
            try:
                ticker_map[sym] = {
                    "quote_volume_24h": float(t.get("quoteVolume") or 0),
                    "price_last": float(t.get("lastPrice") or 0),
                }
            except (TypeError, ValueError):
                continue

    return funding_map, ticker_map


def extract_features(
    client: BinanceFuturesClient,
    sym_meta: dict,
    bulk_funding: dict,
    bulk_ticker: dict,
) -> dict:
    """Returns a FeatureBundle dict, or {'_error': str} if fatal."""
    symbol = sym_meta["symbol"]
    try:
        raw_klines = client.klines(symbol, interval="1d", limit=30)
    except Exception as exc:
        return {"_error": f"klines: {exc}"}
    if not raw_klines or len(raw_klines) < 14:
        return {"_error": f"insufficient klines ({len(raw_klines) if raw_klines else 0})"}

    try:
        highs = [float(k[2]) for k in raw_klines]
        lows = [float(k[3]) for k in raw_klines]
        closes = [float(k[4]) for k in raw_klines]
    except (IndexError, TypeError, ValueError) as exc:
        return {"_error": f"kline parse: {exc}"}

    current_funding = bulk_funding.get(symbol, 0.0)

    # Funding history (best-effort — we already have current from bulk)
    fund_rates: list = []
    try:
        fund_hist = client.funding_rate_history(symbol, limit=50)
        if fund_hist:
            sorted_hist = sorted(fund_hist, key=lambda f: f.get("fundingTime") or 0)
            fund_rates = [float(f.get("fundingRate") or 0) for f in sorted_hist]
    except Exception as exc:
        log.debug("funding history failed for %s: %s", symbol, exc)
    if not fund_rates:
        fund_rates = [current_funding]

    # 14d ≈ 42 funding events (8h cadence)
    last_42 = fund_rates[-42:]
    funding_avg_14d = sum(last_42) / len(last_42)
    # "Recent flip" = any of the last ~48h (6 funding events) is negative
    recent_flip = any(r < 0 for r in fund_rates[-6:])

    # OI history (best-effort)
    oi_now = oi_7d_ago = oi_14d_ago = None
    try:
        oi_hist = client.open_interest_hist(symbol, period="1d", limit=30)
        if oi_hist:
            sorted_oi = sorted(oi_hist, key=lambda o: o.get("timestamp") or 0)
            oi_values = [float(o.get("sumOpenInterest") or 0) for o in sorted_oi]
            oi_now = oi_values[-1] if oi_values else None
            if len(oi_values) >= 8:
                oi_7d_ago = oi_values[-8]
            if len(oi_values) >= 15:
                oi_14d_ago = oi_values[-15]
    except Exception as exc:
        log.debug("OI hist failed for %s: %s", symbol, exc)

    ticker = bulk_ticker.get(symbol, {})
    quote_vol = ticker.get("quote_volume_24h", 0.0)
    price_last = ticker.get("price_last") or closes[-1]

    return_7d = 0.0
    if len(closes) >= 8 and closes[-8] > 0:
        return_7d = (closes[-1] - closes[-8]) / closes[-8]
    return_30d = 0.0
    if closes[0] > 0:
        return_30d = (closes[-1] - closes[0]) / closes[0]

    return {
        "symbol": symbol,
        "base_asset": sym_meta["base_asset"],
        "age_days": sym_meta.get("age_days"),
        "onboard_date": sym_meta.get("onboard_date"),
        "highs_14d": highs[-14:],
        "lows_14d": lows[-14:],
        "closes_21d": closes[-21:],
        "closes_30d": closes,
        "funding_now": current_funding,
        "funding_avg_14d": funding_avg_14d,
        "funding_recent_flip_negative": recent_flip,
        "oi_now": oi_now,
        "oi_7d_ago": oi_7d_ago,
        "oi_14d_ago": oi_14d_ago,
        "quote_volume_24h": quote_vol,
        "price_last": price_last,
        "return_7d": return_7d,
        "return_30d": return_30d,
    }
