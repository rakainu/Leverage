"""The 5 off-switches: global pause, drain, per-coin pause, hot-reload, circuit breaker.

This module owns the *application* of pause/drain/breaker to the entry path. Telegram
commands and the breaker reach this module to mutate the persistent state. The executor
calls :func:`gate_entry` on every prospective entry.

Hot-reload of weights.yaml is implemented in :mod:`hlsm.safety.hot_reload`.
The circuit breaker is in :mod:`hlsm.safety.circuit_breaker`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from sqlalchemy.orm import Session

from hlsm.safety.state import SafetyState, get_safety_state, mutate


class EntryDecision(str, Enum):
    ALLOW = "allow"
    SKIP_PAUSED = "skipped_paused"
    SKIP_DRAIN = "skipped_drain"
    SKIP_COIN_PAUSED = "skipped_coin_paused"
    SKIP_BREAKER = "skipped_breaker"
    SKIP_MAX_CONCURRENT = "skipped_max_concurrent"
    SKIP_UNIVERSE = "skipped_universe"
    SKIP_ALREADY_OPEN = "skipped_already_open_on_coin"


@dataclass(frozen=True)
class EntryGateResult:
    decision: EntryDecision
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == EntryDecision.ALLOW


def gate_entry(
    *,
    session: Session,
    coin: str,
    open_paper_count: int,
    max_concurrent: int,
    universe: frozenset[str] | None = None,
    already_open_on_coin: bool = False,
) -> EntryGateResult:
    """Return whether a prospective entry on `coin` is allowed right now."""
    state = get_safety_state(session)
    coin_u = coin.upper()

    if state.drain_mode:
        return EntryGateResult(EntryDecision.SKIP_DRAIN, "drain mode active")
    if state.paused_global:
        return EntryGateResult(EntryDecision.SKIP_PAUSED, "global pause active")
    if state.breaker_tripped:
        return EntryGateResult(EntryDecision.SKIP_BREAKER, "daily-loss circuit breaker tripped")
    if coin_u in state.paused_coins:
        return EntryGateResult(EntryDecision.SKIP_COIN_PAUSED, f"{coin_u} paused")
    if universe is not None and coin_u not in universe:
        return EntryGateResult(EntryDecision.SKIP_UNIVERSE, f"{coin_u} not in trading universe")
    if already_open_on_coin:
        return EntryGateResult(
            EntryDecision.SKIP_ALREADY_OPEN,
            f"already have an open paper position on {coin_u}; refusing to stack",
        )
    if open_paper_count >= max_concurrent:
        return EntryGateResult(
            EntryDecision.SKIP_MAX_CONCURRENT,
            f"open positions {open_paper_count} >= max_concurrent {max_concurrent}",
        )
    return EntryGateResult(EntryDecision.ALLOW, "ok")


# ---- mutations exposed to Telegram bot / circuit breaker ----

def apply_pause(session: Session) -> SafetyState:
    def _m(s: SafetyState) -> None:
        s.paused_global = True
    return mutate(session, _m)


def apply_resume(session: Session) -> SafetyState:
    def _m(s: SafetyState) -> None:
        s.paused_global = False
        s.drain_mode = False
        s.breaker_tripped = False
        s.breaker_tripped_at = None
    return mutate(session, _m)


def apply_drain(session: Session) -> SafetyState:
    def _m(s: SafetyState) -> None:
        s.drain_mode = True
        s.paused_global = True
    return mutate(session, _m)


def apply_pause_coin(session: Session, coin: str) -> SafetyState:
    coin_u = coin.upper()

    def _m(s: SafetyState) -> None:
        s.paused_coins.add(coin_u)
    return mutate(session, _m)


def apply_resume_coin(session: Session, coin: str) -> SafetyState:
    coin_u = coin.upper()

    def _m(s: SafetyState) -> None:
        s.paused_coins.discard(coin_u)
    return mutate(session, _m)


def trip_breaker(session: Session) -> SafetyState:
    """Called by the CircuitBreaker. Sets breaker_tripped + paused_global."""
    from datetime import datetime, timezone

    def _m(s: SafetyState) -> None:
        s.breaker_tripped = True
        s.breaker_tripped_at = datetime.now(timezone.utc)
        s.paused_global = True
    return mutate(session, _m)
