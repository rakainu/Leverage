"""Hot-reloadable YAML weights loader.

Watches file mtime; callers must invoke check_and_reload() periodically
(or on a fixed schedule). We deliberately do not spawn a background thread.
"""
from pathlib import Path
from typing import Any

import yaml


class WeightsLoader:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"weights file not found: {self.path}")
        self._data: dict[str, Any] = {}
        self.last_loaded_mtime: float = 0.0
        self._load()

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        self.last_loaded_mtime = self.path.stat().st_mtime

    def check_and_reload(self) -> bool:
        """Reload if the file has been modified since last load.

        Returns True if a reload happened, False otherwise.
        """
        mtime = self.path.stat().st_mtime
        if mtime > self.last_loaded_mtime:
            self._load()
            return True
        return False

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Look up a dotted key like 'weights.wallet_quality'.

        Returns default if any segment is missing.
        """
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data(self) -> dict[str, Any]:
        """Return the whole config tree (read-only snapshot)."""
        return self._data
