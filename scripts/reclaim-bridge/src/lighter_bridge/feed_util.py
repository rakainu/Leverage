"""Pure helpers for the bar feed (no SDK imports → unit-testable in isolation).

Backoff policy after the 2026-06-04 WAF incident: Lighter's CloudFront challenges
the /candlesticks path with a captcha (HTTP 405 + x-amzn-waf-action: captcha) when
our request rate is too high. The flag is sticky per-IP-per-path, so the ONLY way
to clear it is to STOP hitting the path for a cooldown. We therefore back off
exponentially (long) on a WAF challenge, and gently on ordinary transient errors.
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
