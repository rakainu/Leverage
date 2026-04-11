"""Wallet registry — reads the shared meme-trading/config/wallets.json."""
import json
from pathlib import Path


class WalletRegistry:
    """Loads wallet entries from a shared JSON file.

    The file format matches the existing meme-trading/config/wallets.json:
    {
      "wallets": [
        {"address": "...", "name": "...", "source": "...",
         "tags": [...], "active": true, "added_at": "..."},
        ...
      ]
    }
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._wallets: dict[str, dict] = {}

    def load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"wallets file not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._wallets = {
            w["address"]: w for w in (data.get("wallets") or []) if "address" in w
        }

    def active_addresses(self) -> set[str]:
        return {addr for addr, w in self._wallets.items() if w.get("active")}

    def active_count(self) -> int:
        return len(self.active_addresses())

    def get(self, address: str) -> dict | None:
        return self._wallets.get(address)

    def all_addresses(self) -> set[str]:
        return set(self._wallets.keys())
