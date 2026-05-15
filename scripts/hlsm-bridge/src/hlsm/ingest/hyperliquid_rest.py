"""Hyperliquid REST client + historical ingest.

Uses the public /info endpoint (POST, JSON body). No API key required.
Reference: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from hlsm.db import Fill, Wallet

log = logging.getLogger(__name__)

BASE_URL = "https://api.hyperliquid.xyz"
STATS_URL = "https://stats-data.hyperliquid.xyz"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Origin": "https://app.hyperliquid.xyz",
    "Referer": "https://app.hyperliquid.xyz/",
    "Accept": "application/json",
}


@dataclass
class RateLimit:
    requests_per_second: float = 2.0
    max_retries_on_429: int = 3
    backoff_seconds: float = 5.0


class HyperliquidREST:
    """Thin HTTP wrapper around the HL /info endpoint."""

    def __init__(self, *, base_url: str = BASE_URL, rate: RateLimit | None = None,
                 timeout_seconds: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate = rate or RateLimit()
        self.timeout = timeout_seconds
        self._client = httpx.Client(timeout=timeout_seconds)
        self._last_call_ts: float = 0.0

    def _throttle(self) -> None:
        gap = 1.0 / self.rate.requests_per_second
        now = time.monotonic()
        wait = gap - (now - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _info(self, body: dict[str, Any]) -> Any:
        attempt = 0
        while True:
            self._throttle()
            r = self._client.post(f"{self.base_url}/info", json=body)
            if r.status_code == 429 and attempt < self.rate.max_retries_on_429:
                wait = self.rate.backoff_seconds * (2 ** attempt)
                log.warning("HL 429 (attempt %d) — backing off %.1fs", attempt + 1, wait)
                time.sleep(wait)
                attempt += 1
                continue
            r.raise_for_status()
            return r.json()

    def user_fills(self, address: str, *, aggregate_by_time: bool = False) -> list[dict[str, Any]]:
        """Return the last batch of fills for the user (HL caps at ~2000 fills per call)."""
        body = {"type": "userFills", "user": address, "aggregateByTime": aggregate_by_time}
        result = self._info(body)
        if isinstance(result, list):
            return result
        return []

    def user_fills_by_time(self, address: str, *, start_ms: int, end_ms: int | None = None) -> list[dict[str, Any]]:
        body = {
            "type": "userFillsByTime",
            "user": address,
            "startTime": start_ms,
        }
        if end_ms is not None:
            body["endTime"] = end_ms
        result = self._info(body)
        return result if isinstance(result, list) else []

    def clearinghouse_state(self, address: str) -> dict[str, Any]:
        return self._info({"type": "clearinghouseState", "user": address})

    def meta(self) -> dict[str, Any]:
        return self._info({"type": "meta"})

    def leaderboard(self) -> list[dict[str, Any]]:
        """Return Hyperliquid's public leaderboard rows.

        The /info {type:'leaderBoard'} endpoint returns 422 in current API. The
        leaderboard is served via the stats-data subdomain instead, which is
        Cloudfront-gated and requires browser-style headers (Origin + Referer +
        UA). The shape returned matches the original (rows with ethAddress +
        windowPerformances).
        """
        self._throttle()
        try:
            r = self._client.get(
                f"{STATS_URL}/Mainnet/leaderboard",
                headers=BROWSER_HEADERS,
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("HL stats-data leaderboard fetch failed: %s", e)
            return []
        if isinstance(payload, dict):
            return payload.get("leaderboardRows") or []
        if isinstance(payload, list):
            return payload
        return []

    def close(self) -> None:
        self._client.close()


def _classify_direction(dir_str: str) -> str:
    d = (dir_str or "").lower()
    if "open" in d and "long" in d:
        return "open_long"
    if "close" in d and "long" in d:
        return "close_long"
    if "open" in d and "short" in d:
        return "open_short"
    if "close" in d and "short" in d:
        return "close_short"
    # Truncate any other HL direction string to fit the column (spot conversion, liquidations, etc.)
    return (d or "unknown")[:32]


def _upsert_fill(session: Session, row: Fill) -> None:
    """Idempotent insert keyed on (wallet_address, hash)."""
    if session.bind.dialect.name == "postgresql":
        stmt = pg_insert(Fill.__table__).values(
            wallet_address=row.wallet_address, ts=row.ts, coin=row.coin, side=row.side,
            direction=row.direction, px=row.px, sz=row.sz,
            start_position=row.start_position, hash=row.hash, fee=row.fee,
            closed_pnl=row.closed_pnl,
        ).on_conflict_do_nothing(index_elements=["wallet_address", "hash"])
        session.execute(stmt)
    else:
        stmt = sqlite_insert(Fill.__table__).values(
            wallet_address=row.wallet_address, ts=row.ts, coin=row.coin, side=row.side,
            direction=row.direction, px=row.px, sz=row.sz,
            start_position=row.start_position, hash=row.hash, fee=row.fee,
            closed_pnl=row.closed_pnl,
        ).on_conflict_do_nothing(index_elements=["wallet_address", "hash"])
        session.execute(stmt)


class HistoricalIngestor:
    """Pulls N days of fills for a set of wallets, idempotent on re-run."""

    def __init__(self, client: HyperliquidREST, *, days: int = 90) -> None:
        self.client = client
        self.days = days

    def ingest_wallet(self, session: Session, address: str) -> int:
        """Backfill `self.days` days of fills for one wallet. Returns count of new rows added."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days)
        start_ms = int(cutoff.timestamp() * 1000)

        wallet = session.get(Wallet, address)
        if wallet is None:
            session.add(Wallet(address=address, source="ingest", active=True))
            session.flush()

        rows = self.client.user_fills_by_time(address, start_ms=start_ms)
        if not rows:
            # Fallback to plain user_fills if by-time is empty
            rows = self.client.user_fills(address)
        added = 0
        for r in rows:
            ts_ms = int(r.get("time") or r.get("startPosition") or 0)
            if ts_ms <= 0:
                continue
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if ts < cutoff:
                continue
            coin = str(r.get("coin") or "").upper()
            side = str(r.get("side") or "").lower()
            direction = _classify_direction(str(r.get("dir") or ""))
            px = Decimal(str(r.get("px") or 0))
            sz = Decimal(str(r.get("sz") or 0))
            start_pos = r.get("startPosition")
            fill = Fill(
                wallet_address=address,
                ts=ts,
                coin=coin,
                side="buy" if side in {"b", "buy", "long"} else "sell",
                direction=direction,
                px=px,
                sz=sz,
                start_position=Decimal(str(start_pos)) if start_pos is not None else None,
                hash=str(r.get("hash") or f"{address}:{ts_ms}:{coin}"),
                fee=Decimal(str(r.get("fee") or 0)),
                closed_pnl=Decimal(str(r["closedPnl"])) if r.get("closedPnl") is not None else None,
            )
            _upsert_fill(session, fill)
            added += 1
        wallet = session.get(Wallet, address)
        wallet.last_seen_at = datetime.now(timezone.utc)
        session.flush()
        return added

    def ingest_many(self, session: Session, addresses: Iterable[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for addr in addresses:
            try:
                out[addr] = self.ingest_wallet(session, addr)
            except Exception:  # noqa: BLE001
                log.exception("ingest failed for %s", addr)
                out[addr] = -1
        return out
