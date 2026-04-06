"""Sliding window convergence detection for Smart Money signals."""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean

from db.database import get_db
from engine.signal import BuyEvent, ConvergenceSignal

logger = logging.getLogger("smc.engine.convergence")


class ConvergenceEngine:
    """Detects when N+ distinct wallets buy the same token within a time window.

    Emits ConvergenceSignal to signal_bus when threshold is met.
    Deduplicates so the same wallet combination doesn't trigger twice.
    """

    def __init__(self, settings, event_bus: asyncio.Queue, signal_bus: asyncio.Queue):
        self.window_minutes = settings.convergence_window_minutes
        self.threshold = settings.convergence_threshold
        self.event_bus = event_bus
        self.signal_bus = signal_bus
        self._window: dict[str, list[BuyEvent]] = defaultdict(list)
        self._signaled: dict[str, set[frozenset[str]]] = defaultdict(set)

    async def run(self):
        """Consume BuyEvents, check for convergence."""
        logger.info(
            f"Convergence engine started: {self.threshold} wallets / "
            f"{self.window_minutes}min window"
        )
        while True:
            event: BuyEvent = await self.event_bus.get()
            self._prune_expired()
            self._window[event.token_mint].append(event)
            await self._check_convergence(event.token_mint)

    def _prune_expired(self):
        """Remove events outside the sliding window."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.window_minutes)
        for mint in list(self._window):
            self._window[mint] = [
                e for e in self._window[mint] if e.timestamp > cutoff
            ]
            if not self._window[mint]:
                del self._window[mint]
                self._signaled.pop(mint, None)

    async def _check_convergence(self, token_mint: str):
        """Check if enough distinct wallets have bought this token."""
        events = self._window[token_mint]
        distinct_wallets = set(e.wallet_address for e in events)

        # Persist 2-buy signals for dashboard tracking (not traded)
        if len(distinct_wallets) == 2 and self.threshold > 2:
            await self._persist_2buy_signal(token_mint, events, distinct_wallets)

        if len(distinct_wallets) < self.threshold:
            return

        # Dedup: don't re-signal the same wallet combination
        wallet_key = frozenset(distinct_wallets)
        if wallet_key in self._signaled[token_mint]:
            return
        self._signaled[token_mint].add(wallet_key)

        signal = ConvergenceSignal(
            token_mint=token_mint,
            token_symbol=events[0].token_symbol,
            wallets=sorted(distinct_wallets),
            buy_events=list(events),
            first_buy_at=min(e.timestamp for e in events),
            signal_at=datetime.now(timezone.utc),
            avg_amount_sol=mean(e.amount_sol for e in events),
            total_amount_sol=sum(e.amount_sol for e in events),
        )

        logger.info(
            f"CONVERGENCE SIGNAL: {token_mint[:12]}.. — "
            f"{len(distinct_wallets)} wallets, "
            f"{signal.total_amount_sol:.2f} SOL total"
        )

        # Persist signal to DB
        await self._persist_signal(signal)

        await self.signal_bus.put(signal)

    async def _persist_signal(self, signal: ConvergenceSignal):
        """Save convergence signal to database."""
        try:
            db = await get_db()
            await db.execute(
                """INSERT INTO convergence_signals
                   (token_mint, token_symbol, wallet_count, wallets_json,
                    first_buy_at, signal_at, avg_amount_sol, total_amount_sol)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.token_mint,
                    signal.token_symbol,
                    len(signal.wallets),
                    json.dumps(signal.wallets),
                    signal.first_buy_at.isoformat(),
                    signal.signal_at.isoformat(),
                    signal.avg_amount_sol,
                    signal.total_amount_sol,
                ),
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist signal: {e}")

    async def _persist_2buy_signal(self, token_mint: str, events: list, distinct_wallets: set):
        """Save 2-buy convergence to DB for dashboard display (not traded)."""
        wallet_key = frozenset(distinct_wallets)
        if wallet_key in self._signaled.get(token_mint, set()):
            return
        self._signaled[token_mint].add(wallet_key)

        signal = ConvergenceSignal(
            token_mint=token_mint,
            token_symbol=events[0].token_symbol,
            wallets=sorted(distinct_wallets),
            buy_events=list(events),
            first_buy_at=min(e.timestamp for e in events),
            signal_at=datetime.now(timezone.utc),
            avg_amount_sol=mean(e.amount_sol for e in events),
            total_amount_sol=sum(e.amount_sol for e in events),
        )

        logger.info(
            f"2-BUY SIGNAL: {token_mint[:12]}.. — "
            f"2 wallets, {signal.total_amount_sol:.2f} SOL total"
        )

        await self._persist_signal(signal)
