"""Pure helpers for the bar feed (no SDK imports → unit-testable in isolation).

Rate-limiting is now centralized in the shared lighter-gateway (one caching,
rate-capped egress per VPS; see scripts/marketdata) — bridges point connection.host
at it, so the box makes at most one upstream Lighter call per coin per TTL window.
These per-bridge backoffs remain as a SAFETY NET for the degraded path where the
gateway is briefly unreachable: the bridge backs off and retries against the gateway
(which `restart: always` recovers within seconds). There is NO direct-to-Lighter
fallback — the gateway is the sole egress. WAF/captcha challenges still get a long
exponential backoff, ordinary transient errors a short one.
"""
from __future__ import annotations


def is_waf_error(exc) -> bool:
    """True if the exception looks like an AWS WAF captcha challenge (or a bare
    405, which Lighter returns for the challenge)."""
    s = str(exc).lower()
    return "captcha" in s or "waf" in s or "405" in s


def compute_backoff(consecutive_errs: int, is_waf: bool = False) -> float:
    """Seconds to wait after `consecutive_errs` consecutive failures.

    WAF: start 60s, double each error, cap 600s (10-min cooldown lets the sticky
    captcha TTL expire). Transient: start 10s, double, cap 180s.
    """
    n = max(1, consecutive_errs)
    base, cap = (60.0, 600.0) if is_waf else (10.0, 180.0)
    return min(base * (2 ** (n - 1)), cap)
