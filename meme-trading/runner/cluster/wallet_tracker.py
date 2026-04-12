"""Periodic wallet registry diff-on-reload tracker.

Reloads wallets.json every N minutes, diffs against the previous
snapshot, and logs changes to wallet_registry_events table.
Observational only — does not affect scoring or trading.
"""
import asyncio
import json
from datetime import datetime, timezone

from runner.cluster.wallet_registry import WalletRegistry
from runner.db.database import Database
from runner.utils.logging import get_logger

logger = get_logger("runner.cluster.wallet_tracker")


class WalletRegistryTracker:
    """Tracks wallet registry changes over time via periodic reload + diff."""

    def __init__(
        self,
        registry: WalletRegistry,
        db: Database,
        reload_interval_sec: float = 300.0,  # 5 minutes
    ):
        self.registry = registry
        self.db = db
        self.reload_interval_sec = reload_interval_sec
        self._prev_snapshot: dict[str, dict] = {}
        self._last_sync: datetime | None = None

    async def run(self) -> None:
        """Long-lived loop: snapshot current state, then reload + diff periodically."""
        logger.info("wallet_tracker_start", interval=self.reload_interval_sec)
        # Take initial snapshot (registry already loaded by main.py)
        self._prev_snapshot = self._take_snapshot()
        self._last_sync = datetime.now(timezone.utc)
        await self._persist_sync_time()

        while True:
            await asyncio.sleep(self.reload_interval_sec)
            await self._reload_and_diff()

    async def _reload_and_diff(self) -> None:
        """Reload the registry file, diff against previous, log events."""
        try:
            self.registry.load()
        except Exception as e:  # noqa: BLE001
            logger.warning("wallet_reload_failed", error=str(e))
            return

        new_snapshot = self._take_snapshot()
        events = self._diff(self._prev_snapshot, new_snapshot)

        if events:
            await self._persist_events(events)
            logger.info("wallet_registry_changes", count=len(events))

        self._prev_snapshot = new_snapshot
        self._last_sync = datetime.now(timezone.utc)
        await self._persist_sync_time()

    def _take_snapshot(self) -> dict[str, dict]:
        """Capture current registry state as {address: wallet_dict}."""
        return {
            addr: self.registry.get(addr) or {}
            for addr in self.registry.all_addresses()
        }

    def _diff(
        self, old: dict[str, dict], new: dict[str, dict]
    ) -> list[dict]:
        """Compare two snapshots, return list of change events."""
        events = []
        old_addrs = set(old.keys())
        new_addrs = set(new.keys())

        # New wallets added
        for addr in new_addrs - old_addrs:
            w = new[addr]
            events.append({
                "wallet_address": addr,
                "action": "added",
                "source": w.get("source"),
                "label": w.get("name"),
                "detail_json": json.dumps({"active": w.get("active", True)}),
            })

        # Wallets removed from file entirely (rare but possible)
        for addr in old_addrs - new_addrs:
            w = old[addr]
            events.append({
                "wallet_address": addr,
                "action": "deactivated",
                "source": w.get("source"),
                "label": w.get("name"),
                "detail_json": json.dumps({"reason": "removed_from_file"}),
            })

        # Wallets in both — check for active/inactive changes
        for addr in old_addrs & new_addrs:
            old_active = old[addr].get("active", True)
            new_active = new[addr].get("active", True)
            if old_active and not new_active:
                events.append({
                    "wallet_address": addr,
                    "action": "deactivated",
                    "source": new[addr].get("source"),
                    "label": new[addr].get("name"),
                    "detail_json": json.dumps({"was_active": True}),
                })
            elif not old_active and new_active:
                events.append({
                    "wallet_address": addr,
                    "action": "reactivated",
                    "source": new[addr].get("source"),
                    "label": new[addr].get("name"),
                    "detail_json": None,
                })

        return events

    async def _persist_events(self, events: list[dict]) -> None:
        assert self.db.conn is not None
        for ev in events:
            await self.db.conn.execute(
                """INSERT INTO wallet_registry_events
                   (wallet_address, action, source, label, detail_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (ev["wallet_address"], ev["action"], ev["source"],
                 ev["label"], ev.get("detail_json")),
            )
        await self.db.conn.commit()

    async def _persist_sync_time(self) -> None:
        """Store last sync time in a lightweight way (reuse schema_version or just log)."""
        # We just log it — the dashboard can query MAX(created_at) from events
        # or we track it in memory. No extra table needed.
        pass

    @property
    def last_sync(self) -> datetime | None:
        return self._last_sync

    @property
    def active_count(self) -> int:
        return self.registry.active_count()
