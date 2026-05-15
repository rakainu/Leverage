"""Hot-reload of config/weights.yaml using watchfiles. Picked up within 30s by polling.

Uses a poll-based watcher (not inotify) so it works identically on Windows + Linux containers.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml

log = logging.getLogger(__name__)


class WeightsWatcher:
    """Polls a YAML file. Calls :func:`on_change(new_dict)` whenever the file mtime changes."""

    def __init__(self, path: Path, on_change: Callable[[dict[str, Any]], None],
                 poll_interval_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self.on_change = on_change
        self.poll_interval = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_mtime: float | None = None

    def _read(self) -> dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _loop(self) -> None:
        # Initial read
        try:
            data = self._read()
            self._last_mtime = self.path.stat().st_mtime
            self.on_change(data)
        except FileNotFoundError:
            log.warning("weights.yaml not found at %s; watcher will retry", self.path)

        while not self._stop.wait(self.poll_interval):
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                continue
            if self._last_mtime is None or mtime != self._last_mtime:
                try:
                    data = self._read()
                except Exception:  # noqa: BLE001
                    log.exception("failed to re-read weights.yaml; keeping previous values")
                    continue
                self._last_mtime = mtime
                try:
                    self.on_change(data)
                    log.info("weights.yaml reloaded (mtime=%s)", mtime)
                except Exception:  # noqa: BLE001
                    log.exception("on_change callback failed")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="weights-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def poll_once(self) -> bool:
        """Manual single-shot poll (for tests). Returns True if a reload fired."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        if self._last_mtime is None or mtime != self._last_mtime:
            try:
                data = self._read()
            except Exception:
                return False
            self._last_mtime = mtime
            self.on_change(data)
            return True
        return False
