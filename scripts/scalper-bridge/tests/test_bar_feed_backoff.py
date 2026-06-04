"""Bar-feed backoff + WAF detection.

Incident 2026-06-04: 10 synchronized candle feeds tripped Lighter's CloudFront
WAF on the /candlesticks path (HTTP 405 + x-amzn-waf-action: captcha). The old
feed retried with a gentle LINEAR backoff, so it kept hammering the flagged path
and never let the sticky captcha TTL expire. Fix: detect WAF challenges and back
off EXPONENTIALLY (long cooldown) so the flag clears, gentler for transient errors.
"""
from lighter_bridge.feed_util import compute_backoff, is_waf_error


# ---------- WAF detection ----------

def test_detects_405_captcha():
    exc = Exception("(405)\nHTTP response headers: ... 'x-amzn-waf-action': 'captcha'")
    assert is_waf_error(exc) is True


def test_detects_bare_405():
    assert is_waf_error(Exception("ApiException: (405) Not Allowed")) is True


def test_transient_error_is_not_waf():
    assert is_waf_error(Exception("Connection timeout")) is False
    assert is_waf_error(Exception("(500) Internal Server Error")) is False


# ---------- backoff schedule ----------

def test_waf_backoff_is_exponential():
    assert compute_backoff(1, is_waf=True) == 60
    assert compute_backoff(2, is_waf=True) == 120
    assert compute_backoff(3, is_waf=True) == 240


def test_waf_backoff_caps():
    assert compute_backoff(20, is_waf=True) == 600     # 10-min cap


def test_transient_backoff_is_gentler_and_capped():
    assert compute_backoff(1, is_waf=False) == 10
    assert compute_backoff(20, is_waf=False) == 180    # 3-min cap


def test_waf_always_backs_off_at_least_as_hard_as_transient():
    for n in range(1, 10):
        assert compute_backoff(n, is_waf=True) >= compute_backoff(n, is_waf=False)


def test_backoff_floor_at_one_error():
    # zero/negative treated as one
    assert compute_backoff(0, is_waf=True) == 60
