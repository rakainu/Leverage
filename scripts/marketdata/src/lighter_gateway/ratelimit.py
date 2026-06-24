from __future__ import annotations
import time
from typing import Callable


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float,
                 clock: Callable[[], float] = time.monotonic):
        self._rate = float(rate_per_s)
        self._burst = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()

    def try_acquire(self) -> bool:
        now = self._clock()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
