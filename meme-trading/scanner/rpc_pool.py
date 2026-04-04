"""Round-robin RPC endpoint manager with health tracking."""

import logging

logger = logging.getLogger("smc.scanner.rpc_pool")


class RpcPool:
    """Manages multiple RPC/WS endpoints with round-robin and failure tracking."""

    def __init__(self, urls: list[str], max_failures: int = 3):
        self.urls = urls
        self.max_failures = max_failures
        self._index = 0
        self._failures: dict[str, int] = {}

    def next(self) -> str:
        """Return next healthy endpoint via round-robin."""
        for _ in range(len(self.urls)):
            url = self.urls[self._index % len(self.urls)]
            self._index += 1
            if self._failures.get(url, 0) < self.max_failures:
                return url
        # All failing — reset and try first
        logger.warning("All RPC endpoints failing, resetting failure counts")
        self._failures.clear()
        return self.urls[0]

    def mark_failed(self, url: str):
        self._failures[url] = self._failures.get(url, 0) + 1
        logger.warning(f"RPC {url[:40]}... failed ({self._failures[url]}x)")

    def mark_healthy(self, url: str):
        if url in self._failures:
            del self._failures[url]
