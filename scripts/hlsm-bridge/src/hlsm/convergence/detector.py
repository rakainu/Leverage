"""Sliding-window convergence detector.

Tracks recent wallet OPEN events per (coin, side). When N distinct ranked wallets have
opened the same side of the same coin within the window, a ConvergenceEvent is emitted.

Deduplication: once a particular set of wallets has fired for (coin, side), the detector
will not re-fire on the same identity set within a cooldown period. New wallets joining
the cluster can re-arm the trigger.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Iterable

from hlsm.convergence.events import ConvergenceEvent, WalletOpenEvent
from hlsm.exchange.types import Side


@dataclass
class DetectorConfig:
    cluster_n: int = 3
    window_minutes: int = 45
    score_floor: float = 75.0
    cooldown_minutes: int = 60          # don't re-fire same wallet set within cooldown
    universe: frozenset[str] | None = None  # None = accept everything


class ConvergenceDetector:
    """Stateful detector. Call :meth:`on_open` per WalletOpenEvent; collect emissions from the return value."""

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config
        # (coin, side) -> deque[WalletOpenEvent] sorted ascending by ts
        self._buf: dict[tuple[str, str], Deque[WalletOpenEvent]] = defaultdict(deque)
        # (coin, side, frozenset[wallets]) -> last_fired_ts; for cooldown dedup
        self._recent_firings: dict[tuple[str, str, frozenset[str]], datetime] = {}

    def _window(self) -> timedelta:
        return timedelta(minutes=self.config.window_minutes)

    def _cooldown(self) -> timedelta:
        return timedelta(minutes=self.config.cooldown_minutes)

    def _gc(self, ts_now: datetime) -> None:
        """Drop firings older than 2x cooldown, just to keep the dedup map bounded."""
        cutoff = ts_now - 2 * self._cooldown()
        stale = [k for k, t in self._recent_firings.items() if t < cutoff]
        for k in stale:
            del self._recent_firings[k]

    def on_open(self, ev: WalletOpenEvent) -> ConvergenceEvent | None:
        """Ingest one wallet OPEN event. Returns a ConvergenceEvent if one fires, else None."""
        # Universe gate
        if self.config.universe is not None and ev.coin.upper() not in self.config.universe:
            return None
        # Score gate
        if ev.score < self.config.score_floor:
            return None

        key = (ev.coin.upper(), ev.side.value)
        buf = self._buf[key]

        # Append, then trim out-of-window entries.
        buf.append(ev)
        window = self._window()
        while buf and (ev.ts - buf[0].ts) > window:
            buf.popleft()

        # Distinct wallets currently in window
        wallets_in_window: dict[str, WalletOpenEvent] = {}
        for e in buf:
            # Keep earliest open per wallet within window
            if e.wallet_address not in wallets_in_window:
                wallets_in_window[e.wallet_address] = e

        if len(wallets_in_window) < self.config.cluster_n:
            return None

        wallet_set = frozenset(wallets_in_window.keys())
        dedup_key = (key[0], key[1], wallet_set)
        last_fired = self._recent_firings.get(dedup_key)
        if last_fired is not None and (ev.ts - last_fired) < self._cooldown():
            return None
        self._recent_firings[dedup_key] = ev.ts
        self._gc(ev.ts)

        addresses = tuple(sorted(wallets_in_window.keys()))
        scores = tuple(wallets_in_window[a].score for a in addresses)
        opened_first = min(e.ts for e in wallets_in_window.values())
        opened_last = max(e.ts for e in wallets_in_window.values())

        return ConvergenceEvent(
            coin=key[0],
            side=Side(key[1]),
            wallet_addresses=addresses,
            opened_at_first=opened_first,
            opened_at_last=opened_last,
            score_floor_used=self.config.score_floor,
            window_seconds=int(self._window().total_seconds()),
            wallet_scores=scores,
        )

    def replay(self, events: Iterable[WalletOpenEvent]) -> list[ConvergenceEvent]:
        """Replay a chronological stream of WalletOpenEvents. For historical backtests."""
        out: list[ConvergenceEvent] = []
        for ev in sorted(events, key=lambda e: e.ts):
            fired = self.on_open(ev)
            if fired is not None:
                out.append(fired)
        return out
