"""Round-robin pool of RPC endpoints with health tracking."""
from itertools import cycle


class RpcPool:
    """Holds a list of RPC URLs, rotates through healthy ones.

    If all URLs are marked unhealthy, falls back to rotating the full list
    (so we keep retrying instead of deadlocking).
    """

    def __init__(self, urls: list[str]):
        if not urls:
            raise ValueError("RpcPool requires at least one URL")
        self._urls = list(urls)
        self._unhealthy: set[str] = set()
        self._iter = cycle(self._urls)

    def next(self) -> str:
        healthy = [u for u in self._urls if u not in self._unhealthy]
        if not healthy:
            # Everyone is flagged unhealthy — fall back to the full list
            # so the system keeps trying. Health resets as callers mark them.
            return next(self._iter)

        while True:
            candidate = next(self._iter)
            if candidate in healthy:
                return candidate

    def mark_unhealthy(self, url: str) -> None:
        self._unhealthy.add(url)

    def mark_healthy(self, url: str) -> None:
        self._unhealthy.discard(url)

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    @property
    def healthy_urls(self) -> list[str]:
        return [u for u in self._urls if u not in self._unhealthy]
