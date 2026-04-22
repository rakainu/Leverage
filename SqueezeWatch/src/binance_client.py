"""Binance USDT-M futures public REST client.

Thin wrapper around requests with retry + backoff on rate limits and 5xx.
No auth needed — all endpoints used here are public.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests


log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://fapi.binance.com"


class BinanceResponseError(Exception):
    """200 OK but the body isn't parseable JSON. Non-retriable — deterministic for this path."""


class BinanceFuturesClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SqueezeWatch/0.1"})

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code in (418, 429):
                    wait = float(r.headers.get("Retry-After", 2 ** attempt))
                    log.warning(
                        "Rate limited on %s (status %s), sleeping %.1fs",
                        path, r.status_code, wait,
                    )
                    time.sleep(wait)
                    continue
                if r.status_code >= 500:
                    wait = 2 ** attempt
                    log.warning("Server error %s on %s, retrying in %ds", r.status_code, path, wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                try:
                    return r.json()
                except ValueError as exc:
                    # 200 OK but non-JSON body (seen on testnet for unavailable endpoints,
                    # or cloudfront HTML error pages in prod). Deterministic — don't retry.
                    raise BinanceResponseError(
                        f"Non-JSON response from {path}: "
                        f"status={r.status_code} body={r.text[:80]!r}"
                    ) from exc
            except BinanceResponseError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                wait = 2 ** attempt
                log.warning("Request failed on %s (attempt %d): %s", path, attempt + 1, exc)
                if attempt >= self.max_retries:
                    raise
                time.sleep(wait)
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Exhausted retries for {path}")

    # ---- Public endpoints used by SqueezeWatch ----

    def exchange_info(self) -> dict:
        return self._get("/fapi/v1/exchangeInfo")

    def klines(self, symbol: str, interval: str = "1d", limit: int = 30) -> list:
        return self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def premium_index(self, symbol: Optional[str] = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self._get("/fapi/v1/premiumIndex", params)

    def funding_rate_history(self, symbol: str, limit: int = 100) -> list:
        return self._get(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": limit},
        )

    def open_interest(self, symbol: str) -> dict:
        return self._get("/fapi/v1/openInterest", {"symbol": symbol})

    def open_interest_hist(self, symbol: str, period: str = "1d", limit: int = 30) -> list:
        return self._get(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )

    def ticker_24hr(self, symbol: Optional[str] = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self._get("/fapi/v1/ticker/24hr", params)
