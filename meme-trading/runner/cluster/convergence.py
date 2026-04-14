"""Convergence detector: per-token sliding window, A+B-only counting.

Consumes BuyEvents from event_bus, emits ClusterSignals to signal_bus
when min_wallets distinct A+B-tier wallets buy the same token within
window_minutes.
"""
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from statistics import mean

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.ingest.events import BuyEvent
from runner.utils.logging import get_logger

logger = get_logger("runner.cluster.convergence")


@dataclass(frozen=True)
class ClusterSignal:
    """Emitted when enough A+B wallets converge on a token."""

    token_mint: str
    wallets: list[str]
    wallet_count: int
    tier_counts: dict[str, int]
    first_buy_time: datetime
    last_buy_time: datetime
    convergence_seconds: int
    mid_price_sol: float
    id: int | None = None


class ConvergenceDetector:
    def __init__(
        self,
        event_bus: asyncio.Queue,
        signal_bus: asyncio.Queue,
        tier_cache: WalletTierCache,
        min_wallets: int = 3,
        window_minutes: int = 30,
        db: Database | None = None,
        weights: WeightsLoader | None = None,
    ):
        self.event_bus = event_bus
        self.signal_bus = signal_bus
        self.tier_cache = tier_cache
        self._static_min_wallets = min_wallets
        self._static_window_minutes = window_minutes
        self.db = db
        self.weights = weights
        # per-token: list of BuyEvents inside the window
        self._window: dict[str, list[BuyEvent]] = defaultdict(list)
        # per-token: set of frozensets of wallet-address combinations we already signaled
        self._signaled: dict[str, set[frozenset[str]]] = defaultdict(set)
        # per-token: last signal emit time, for the rescore cooldown
        self._last_signal_at: dict[str, datetime] = {}

    @property
    def rescore_cooldown_seconds(self) -> int:
        if self.weights is not None:
            return int(self.weights.get("cooldowns.rescore_seconds", 1800))
        return 1800

    @property
    def min_wallets(self) -> int:
        if self.weights is not None:
            return int(self.weights.get("cluster.min_wallets", self._static_min_wallets))
        return self._static_min_wallets

    @property
    def window_minutes(self) -> int:
        if self.weights is not None:
            return int(self.weights.get("cluster.window_minutes", self._static_window_minutes))
        return self._static_window_minutes

    @property
    def require_a_tier_when_min(self) -> int:
        """When min_wallets <= this value, require ≥1 A-tier wallet in cluster.
        Guards precision at low thresholds so two random B-tier wallets don't
        fire a signal."""
        if self.weights is not None:
            return int(self.weights.get("cluster.require_a_tier_when_min", 2))
        return 2

    async def run(self) -> None:
        logger.info(
            "convergence_start",
            min_wallets=self.min_wallets,
            window_minutes=self.window_minutes,
        )
        while True:
            event: BuyEvent = await self.event_bus.get()
            await self._process(event)

    async def _process(self, event: BuyEvent) -> None:
        if self.weights is not None:
            self.weights.check_and_reload()
        # Reject C-tier immediately — they do not contribute to the cluster.
        tier = self.tier_cache.tier_of(event.wallet_address)
        if tier == Tier.C:
            return

        token = event.token_mint
        self._prune_expired(token, event.block_time)
        self._window[token].append(event)

        ab_events = [
            e
            for e in self._window[token]
            if self.tier_cache.tier_of(e.wallet_address) in (Tier.A, Tier.B)
        ]
        distinct_wallets = {e.wallet_address for e in ab_events}

        if len(distinct_wallets) < self.min_wallets:
            # Near-miss observability: log when we're 1 wallet shy of signal.
            # Helps decide whether tightening to a lower min_wallets would
            # multiply signal count without trashing precision.
            if len(distinct_wallets) == self.min_wallets - 1:
                logger.info(
                    "cluster_near_miss",
                    mint=token,
                    wallets=len(distinct_wallets),
                    needed=self.min_wallets,
                )
            return

        # Precision guard: at low min_wallets thresholds (default ≤2), require
        # at least one A-tier wallet in the cluster. Prevents two random
        # B-tier wallets from triggering a signal.
        if self.min_wallets <= self.require_a_tier_when_min:
            has_a_tier = any(
                self.tier_cache.tier_of(w) == Tier.A for w in distinct_wallets
            )
            if not has_a_tier:
                logger.debug(
                    "cluster_skipped_no_a_tier",
                    mint=token,
                    wallets=len(distinct_wallets),
                )
                return

        cluster_key = frozenset(distinct_wallets)
        if cluster_key in self._signaled[token]:
            return
        # Per-mint rescore cooldown: don't re-emit a signal for the same mint
        # within rescore_cooldown_seconds, regardless of which wallets joined.
        last = self._last_signal_at.get(token)
        cooldown = self.rescore_cooldown_seconds
        if last is not None and (event.block_time - last).total_seconds() < cooldown:
            logger.debug("rescore_cooldown_skip", mint=token, cooldown_sec=cooldown)
            return
        self._signaled[token].add(cluster_key)
        self._last_signal_at[token] = event.block_time

        wallet_events_by_addr: dict[str, BuyEvent] = {}
        for e in ab_events:
            # Keep earliest event per wallet for ordering/mid price.
            if (
                e.wallet_address not in wallet_events_by_addr
                or e.block_time < wallet_events_by_addr[e.wallet_address].block_time
            ):
                wallet_events_by_addr[e.wallet_address] = e
        picked = sorted(wallet_events_by_addr.values(), key=lambda x: x.block_time)

        tier_counts: dict[str, int] = {"A": 0, "B": 0}
        for e in picked:
            t = self.tier_cache.tier_of(e.wallet_address)
            if t == Tier.A:
                tier_counts["A"] += 1
            elif t == Tier.B:
                tier_counts["B"] += 1

        first_t = picked[0].block_time
        last_t = picked[-1].block_time
        mid_price = mean(e.price_sol for e in picked)

        signal = ClusterSignal(
            token_mint=token,
            wallets=[e.wallet_address for e in picked],
            wallet_count=len(picked),
            tier_counts=tier_counts,
            first_buy_time=first_t,
            last_buy_time=last_t,
            convergence_seconds=int((last_t - first_t).total_seconds()),
            mid_price_sol=mid_price,
        )
        if self.db is not None and self.db.conn is not None:
            try:
                cursor = await self.db.conn.execute(
                    """
                    INSERT INTO cluster_signals
                    (token_mint, wallet_count, wallets_json, tier_counts_json,
                     first_buy_time, last_buy_time, convergence_seconds, mid_price_sol)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.token_mint,
                        signal.wallet_count,
                        json.dumps(signal.wallets),
                        json.dumps(signal.tier_counts),
                        signal.first_buy_time.isoformat(),
                        signal.last_buy_time.isoformat(),
                        signal.convergence_seconds,
                        signal.mid_price_sol,
                    ),
                )
                await self.db.conn.commit()
                signal = replace(signal, id=cursor.lastrowid)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cluster_signal_persist_failed",
                    mint=signal.token_mint,
                    error=str(e),
                )
        logger.info(
            "cluster_signal",
            mint=token,
            wallets=signal.wallet_count,
            tier_counts=signal.tier_counts,
            convergence_seconds=signal.convergence_seconds,
        )
        await self.signal_bus.put(signal)

    def _prune_expired(self, token: str, now: datetime) -> None:
        cutoff = now - timedelta(minutes=self.window_minutes)
        self._window[token] = [
            e for e in self._window[token] if e.block_time >= cutoff
        ]
