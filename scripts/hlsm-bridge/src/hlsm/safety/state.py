"""In-memory cache of safety state, sync'd to the RuntimeState DB table.

The DB is authoritative. The cache exists so the executor's per-entry check is hot.
All mutations go through this module so the DB and cache stay aligned.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from hlsm.db import RuntimeState


_RUNTIME_KEY = "safety"
_lock = threading.RLock()


@dataclass
class SafetyState:
    paused_global: bool = False
    drain_mode: bool = False
    paused_coins: set[str] = field(default_factory=set)
    breaker_tripped: bool = False
    breaker_tripped_at: datetime | None = None
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused_global": self.paused_global,
            "drain_mode": self.drain_mode,
            "paused_coins": sorted(self.paused_coins),
            "breaker_tripped": self.breaker_tripped,
            "breaker_tripped_at": self.breaker_tripped_at.isoformat() if self.breaker_tripped_at else None,
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SafetyState":
        tripped_at = data.get("breaker_tripped_at")
        return cls(
            paused_global=bool(data.get("paused_global", False)),
            drain_mode=bool(data.get("drain_mode", False)),
            paused_coins=set(data.get("paused_coins", []) or []),
            breaker_tripped=bool(data.get("breaker_tripped", False)),
            breaker_tripped_at=datetime.fromisoformat(tripped_at) if tripped_at else None,
            last_updated=datetime.fromisoformat(data.get("last_updated") or datetime.now(timezone.utc).isoformat()),
        )


_cache: SafetyState | None = None


def get_safety_state(session: Session) -> SafetyState:
    """Load (lazily) the safety state. Cached; refresh via :func:`refresh_from_db`."""
    global _cache
    with _lock:
        if _cache is None:
            row = session.get(RuntimeState, _RUNTIME_KEY)
            if row is None:
                _cache = SafetyState()
                _persist(session, _cache)
            else:
                _cache = SafetyState.from_dict(json.loads(row.value))
        return _cache


def refresh_from_db(session: Session) -> SafetyState:
    global _cache
    with _lock:
        row = session.get(RuntimeState, _RUNTIME_KEY)
        if row is None:
            _cache = SafetyState()
            _persist(session, _cache)
        else:
            _cache = SafetyState.from_dict(json.loads(row.value))
        return _cache


def mutate(session: Session, mutator) -> SafetyState:
    """Apply a callable mutation under lock + persist. Returns the new state."""
    global _cache
    with _lock:
        state = get_safety_state(session)
        mutator(state)
        state.last_updated = datetime.now(timezone.utc)
        _persist(session, state)
        _cache = state
        return state


def _persist(session: Session, state: SafetyState) -> None:
    row = session.get(RuntimeState, _RUNTIME_KEY)
    payload = json.dumps(state.to_dict())
    if row is None:
        row = RuntimeState(key=_RUNTIME_KEY, value=payload)
        session.add(row)
    else:
        row.value = payload
    session.flush()


def reset_for_tests() -> None:
    """Test helper: drop the in-memory cache. Forces re-load next call."""
    global _cache
    with _lock:
        _cache = None
