"""Off-switches, circuit breaker, and hot-reload of weights.yaml."""
from hlsm.safety.state import SafetyState, get_safety_state
from hlsm.safety.off_switches import (
    apply_pause,
    apply_resume,
    apply_drain,
    apply_pause_coin,
    apply_resume_coin,
    gate_entry,
    EntryGateResult,
)
from hlsm.safety.circuit_breaker import CircuitBreaker
from hlsm.safety.hot_reload import WeightsWatcher

__all__ = [
    "SafetyState",
    "get_safety_state",
    "apply_pause",
    "apply_resume",
    "apply_drain",
    "apply_pause_coin",
    "apply_resume_coin",
    "gate_entry",
    "EntryGateResult",
    "CircuitBreaker",
    "WeightsWatcher",
]
