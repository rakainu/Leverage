"""Real-time 5m bar feed for Lighter markets.

Fetches an initial history window (for indicator warmup) then polls for newly
closed bars. Emits a `(symbol, df)` event each time a fresh bar lands; the
DataFrame is the rolling history with the new bar appended.

Design notes:
  - Polls REST `/candles` instead of WS because Lighter doesn't ship a candle WS.
  - 5m bars + 30s poll interval = very low load (one call per market per 30s).
  - Maintains a sliding window of `history_bars` (default 500) so indicators
    have enough seed data.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import lighter
import pandas as pd

log = logging.getLogger(__name__)


_RESOLUTION_S = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600}


@dataclass
class BarFeedConfig:
    market_id: int
    symbol: str
    resolution: str = "5m"
    history_bars: int = 500
    poll_interval_s: int = 30


class BarFeed:
    """Maintains a rolling DataFrame of OHLCV bars for one symbol."""

    def __init__(self, api_client, cfg: BarFeedConfig):
        self.api = api_client
        self.cfg = cfg
        self.df: Optional[pd.DataFrame] = None
        self._candle_api = lighter.CandlestickApi(api_client)
        self._last_closed_ts: Optional[int] = None
        self._stopped = False

    async def bootstrap(self) -> pd.DataFrame:
        """Pull initial history window. Returns the seed DataFrame."""
        res_s = _RESOLUTION_S[self.cfg.resolution]
        end = int(time.time())
        start = end - self.cfg.history_bars * res_s
        log.info(
            "%s: bootstrap %d %s bars (start=%s end=%s)",
            self.cfg.symbol, self.cfg.history_bars, self.cfg.resolution,
            pd.to_datetime(start, unit="s", utc=True),
            pd.to_datetime(end, unit="s", utc=True),
        )
        resp = await self._candle_api.candles(
            market_id=self.cfg.market_id,
            resolution=self.cfg.resolution,
            start_timestamp=start,
            end_timestamp=end,
            count_back=self.cfg.history_bars,
        )
        d = resp.to_dict()
        candles = d.get("c") or []
        if not candles:
            raise RuntimeError(f"{self.cfg.symbol}: bootstrap returned 0 candles")
        df = self._candles_to_df(candles)
        self.df = df
        self._last_closed_ts = int(df.index[-1].timestamp())
        log.info(
            "%s: bootstrapped %d bars, last closed @ %s (price %.4f)",
            self.cfg.symbol, len(df), df.index[-1], df["Close"].iloc[-1],
        )
        return df

    async def fetch_latest(self) -> Optional[pd.DataFrame]:
        """Pull the most recent few bars and append any new closed bar.

        Returns the updated DataFrame ONLY if a new closed bar was appended.
        Returns None if nothing changed.
        """
        if self.df is None:
            raise RuntimeError("call bootstrap() first")
        res_s = _RESOLUTION_S[self.cfg.resolution]
        end = int(time.time())
        # Pull the last ~10 bars to be safe (overlap with what we have)
        start = end - 10 * res_s
        resp = await self._candle_api.candles(
            market_id=self.cfg.market_id,
            resolution=self.cfg.resolution,
            start_timestamp=start,
            end_timestamp=end,
            count_back=10,
        )
        d = resp.to_dict()
        candles = d.get("c") or []
        if not candles:
            return None
        new_df = self._candles_to_df(candles)

        # A bar is "closed" when its timestamp+resolution is in the past.
        # Lighter's candle stream returns the in-progress bar as the last entry;
        # we only consume up to the second-to-last (already closed) bar.
        now = time.time()
        closed_mask = new_df.index < pd.to_datetime(now - res_s, unit="s", utc=True)
        closed = new_df[closed_mask]
        if closed.empty:
            return None

        latest_closed_ts = int(closed.index[-1].timestamp())
        if latest_closed_ts <= self._last_closed_ts:
            return None  # nothing new

        # Append only NEW closed bars
        new_bars = closed[closed.index > self.df.index[-1]]
        if new_bars.empty:
            return None
        self.df = pd.concat([self.df, new_bars])
        # Cap history to prevent unbounded memory
        if len(self.df) > self.cfg.history_bars * 2:
            self.df = self.df.iloc[-self.cfg.history_bars:]
        self._last_closed_ts = latest_closed_ts
        log.info(
            "%s: +%d new bar(s), last close @ %s (price %.4f)",
            self.cfg.symbol, len(new_bars), self.df.index[-1], self.df["Close"].iloc[-1],
        )
        return self.df

    async def run_loop(self, on_new_bar: Callable[[str, pd.DataFrame], Awaitable[None]]):
        """Poll forever, calling `on_new_bar(symbol, df)` whenever a new bar closes."""
        # Bootstrap with retries — Lighter REST can flake on startup
        backoff = 5
        while not self._stopped and self.df is None:
            try:
                await self.bootstrap()
            except Exception as exc:
                log.error("%s: bootstrap failed (retry in %ds): %s",
                          self.cfg.symbol, backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
        if self._stopped:
            return
        await on_new_bar(self.cfg.symbol, self.df)

        consecutive_errs = 0
        while not self._stopped:
            try:
                df = await self.fetch_latest()
                if df is not None:
                    await on_new_bar(self.cfg.symbol, df)
                consecutive_errs = 0
            except Exception as exc:
                consecutive_errs += 1
                log.error("%s: feed error (#%d): %s", self.cfg.symbol, consecutive_errs, exc)
                # Soft backoff on repeated errors — caps at 5 min
                extra_sleep = min(consecutive_errs * 30, 270)
                await asyncio.sleep(extra_sleep)
            await asyncio.sleep(self.cfg.poll_interval_s)

    def stop(self):
        self._stopped = True

    @staticmethod
    def _candles_to_df(candles: list[dict]) -> pd.DataFrame:
        """Lighter candle dict → standard OHLCV DataFrame indexed by UTC datetime."""
        rows = []
        for c in candles:
            rows.append({
                "ts_ms": c["t"],
                "Open": float(c["o"]),
                "High": float(c["h"]),
                "Low": float(c["l"]),
                "Close": float(c["c"]),
                "Volume": float(c.get("v", 0)),
            })
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        df = df.sort_index()
        # Remove any accidental duplicates (overlap between bootstrap and poll)
        df = df[~df.index.duplicated(keep="last")]
        return df
