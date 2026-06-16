"""V3.2 SignalEngine — self-generated entry signals replace TradingView webhooks.

Each scan, for every enabled symbol:
  1. Fetch recent 5m bars and drop the still-forming candle.
  2. Run the proven HA-V3 signal (`signals.latest_signal`) on the last *closed* bar.
  3. On a fresh cross, queue a pending signal — identical to what a Pro V3
     webhook used to do via `router.dispatch`.

The existing PositionPoller then handles the EMA-retest, slope/weekday/body
gates, and the trailing exit unchanged. De-duplication keys on the last closed
bar's timestamp so each new 5m bar is acted on at most once.

Why this exists: the audit proved the live Pro V3 signal was sparse (~36% of
alerts filled) and its longs lost even with a perfect exit, while this HA-V3
signal is the one the backtest engine makes +$19k / PF 2.80 on. Generating it
in-process removes the TradingView dependency (and the alert-expiration gap).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol, Sequence

from .signals import Bar, Signal, SignalParams, latest_signal

log = logging.getLogger(__name__)

# Minimum closed bars before the recursive EMA/SMMA seeds have washed out enough
# to match the engine. The parity test confirms convergence well before this.
MIN_BARS_FOR_SIGNAL = 160


class _GateLike(Protocol):
    def is_paused(self, symbol: str) -> bool: ...


class _StoreLike(Protocol):
    def cancel_pending_signals_for_symbol(self, symbol: str) -> int: ...
    def create_pending_signal(
        self, *, symbol: str, action: str, signal_price: float,
        timeout_minutes: int,
    ) -> int: ...


class _BloFinLike(Protocol):
    def fetch_recent_ohlcv(
        self, inst_id: str, *, timeframe: str, limit: int,
    ) -> list[list[float]]: ...


class SignalEngine:
    """Generates HA-V3 entry signals from polled OHLCV and queues pendings."""

    def __init__(
        self,
        *,
        store: _StoreLike,
        blofin: _BloFinLike,
        symbols: Sequence[str],
        params: SignalParams,
        timeframe: str = "5m",
        lookback_bars: int = 300,
        timeout_minutes: int = 30,
        scan_interval_seconds: int = 30,
        min_adx: float = 0.0,
        gate: Optional[_GateLike] = None,
        notifier=None,
    ) -> None:
        self.store = store
        self.blofin = blofin
        self.symbols = list(symbols)
        self.params = params
        self.timeframe = timeframe
        self.lookback_bars = lookback_bars
        self.timeout_minutes = timeout_minutes
        self.scan_interval_seconds = scan_interval_seconds
        self.min_adx = min_adx
        self.gate = gate
        self.notifier = notifier
        # Per-symbol last *closed* bar timestamp we've already scanned.
        self._last_scanned_ts: dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    # -- core (pure-ish, unit-tested) -------------------------------------
    def scan_once(self) -> list[int]:
        """Scan every symbol once. Returns the ids of any pending signals created."""
        created: list[int] = []
        for symbol in self.symbols:
            try:
                sid = self._scan_symbol(symbol)
                if sid is not None:
                    created.append(sid)
            except Exception:
                log.exception("signal scan failed for %s", symbol)
        return created

    def _scan_symbol(self, symbol: str) -> Optional[int]:
        if self.gate is not None and self.gate.is_paused(symbol):
            return None

        raw = self.blofin.fetch_recent_ohlcv(
            symbol, timeframe=self.timeframe, limit=self.lookback_bars,
        )
        # Drop the still-forming candle; act only on closed bars.
        closed = raw[:-1]
        if len(closed) < MIN_BARS_FOR_SIGNAL:
            return None

        last_ts = closed[-1][0]
        if self._last_scanned_ts.get(symbol) == last_ts:
            return None  # already acted on this closed bar
        self._last_scanned_ts[symbol] = last_ts

        bars = [Bar(open=b[1], high=b[2], low=b[3], close=b[4]) for b in closed]
        sig = latest_signal(bars, self.params)
        if sig.side is None:
            return None

        # Optional risk-adjusted quality gate (default off). Engine showed
        # min_adx=18 lifts PF 2.80->2.90 and cuts DD ~30% at the cost of ~25%
        # of net — exposed as a knob rather than baked in.
        if self.min_adx > 0 and sig.adx < self.min_adx:
            log.info(
                "HA-V3 %s signal for %s suppressed by min_adx: %.1f < %.1f",
                sig.side, symbol, sig.adx, self.min_adx,
            )
            return None

        action = sig.side  # 'buy' | 'sell'
        signal_price = closed[-1][4]
        # A fresh signal supersedes any stale pending for this symbol.
        self.store.cancel_pending_signals_for_symbol(symbol)
        sid = self.store.create_pending_signal(
            symbol=symbol, action=action, signal_price=signal_price,
            timeout_minutes=self.timeout_minutes,
        )
        log.info(
            "HA-V3 %s signal for %s @ %.4f (adx=%.1f) -> pending %d",
            action, symbol, signal_price, sig.adx, sid,
        )
        return sid

    # -- async loop (mirrors PositionPoller lifecycle) --------------------
    async def run(self) -> None:
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        log.info(
            "SignalEngine started (symbols=%s, scan=%ds)",
            self.symbols, self.scan_interval_seconds,
        )
        while not self._stop_event.is_set():
            self.scan_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(self.scan_interval_seconds, 0.001),
                )
            except asyncio.TimeoutError:
                continue
        log.info("SignalEngine stopped")

    def start(self) -> None:
        if self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("SignalEngine.start() called outside event loop; skipping")
            return
        self._stop_event = asyncio.Event()
        self._task = loop.create_task(self.run())

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:
                log.exception("SignalEngine task raised on shutdown")
            self._task = None
