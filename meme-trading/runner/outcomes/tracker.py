"""OutcomeTracker — polls DexScreener for every scored mint, records peak mcap.

Answers the question: "Did any of our scored coins moon?"

For every distinct token_mint in runner_scores, this background task fetches the
current price + FDV from DexScreener on a polling loop, updates `token_outcomes`,
and tracks the running peak mcap. The first time an `ignore`-verdict token crosses
the moonshot threshold (default $1M FDV), it pushes a Telegram alert tagged
"FILTER MISS" so we know our gates leaked a winner.

This is intentionally separate from the milestone snapshotter (which tracks paper
positions on a rigid schedule). Outcomes track every candidate, including the ones
we rejected — that's the only way to audit the filter pipeline.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from runner.db.database import Database
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.outcomes.tracker")

DEXSCREENER_BATCH = "https://api.dexscreener.com/latest/dex/tokens/{mints}"
BATCH_SIZE = 30  # DexScreener accepts up to 30 comma-separated mints per request

# Verdict ranking for "best verdict ever recorded for this mint"
_VERDICT_RANK = {
    "ignore": 0,
    "watch": 1,
    "strong_candidate": 2,
    "probable_runner": 3,
}


class OutcomeTracker:
    """Background task that polls DexScreener and updates token_outcomes."""

    def __init__(
        self,
        db: Database,
        http: RateLimitedClient,
        alert_bus: asyncio.Queue,
        poll_interval_sec: float = 300.0,
        moonshot_mcap_usd: float = 1_000_000.0,
    ):
        self.db = db
        self.http = http
        self.alert_bus = alert_bus
        self.poll_interval_sec = poll_interval_sec
        self.moonshot_mcap_usd = moonshot_mcap_usd

    async def run(self) -> None:
        logger.info(
            "outcome_tracker_start",
            interval=self.poll_interval_sec,
            moonshot_threshold_usd=self.moonshot_mcap_usd,
        )
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.error("outcome_tracker_poll_error", error=str(e))
            await asyncio.sleep(self.poll_interval_sec)

    async def poll_once(self) -> dict[str, int]:
        """Refresh outcomes for every scored mint. Returns counters for tests."""
        await self._sync_rows()
        mints = await self._all_tracked_mints()
        if not mints:
            return {"checked": 0, "updated": 0, "moonshots": 0}

        updated = 0
        moonshots = 0
        for batch in _chunks(mints, BATCH_SIZE):
            pair_map = await self._fetch_batch(batch)
            for mint in batch:
                pair = pair_map.get(mint)
                changed, mooned = await self._apply_pair(mint, pair)
                if changed:
                    updated += 1
                if mooned:
                    moonshots += 1

        logger.info(
            "outcome_tracker_poll_done",
            tracked=len(mints),
            updated=updated,
            moonshots_alerted=moonshots,
        )
        return {"checked": len(mints), "updated": updated, "moonshots": moonshots}

    async def _sync_rows(self) -> None:
        """Make sure token_outcomes has a row for every distinct scored mint."""
        assert self.db.conn is not None
        await self.db.conn.execute(
            """
            INSERT OR IGNORE INTO token_outcomes
                (token_mint, first_scored_at, best_verdict, best_score)
            SELECT token_mint, MIN(created_at), 'ignore', 0
            FROM runner_scores
            GROUP BY token_mint
            """
        )
        # Keep best_verdict / best_score in sync with current max in runner_scores
        async with self.db.conn.execute(
            """
            SELECT token_mint, verdict, MAX(runner_score)
            FROM runner_scores
            GROUP BY token_mint
            """
        ) as cur:
            rows = await cur.fetchall()
        for mint, verdict, best_score in rows:
            # Pick the highest-rank verdict ever seen for this mint
            async with self.db.conn.execute(
                "SELECT verdict FROM runner_scores WHERE token_mint = ?",
                (mint,),
            ) as cur:
                all_verdicts = [r[0] async for r in cur]
            top_verdict = max(all_verdicts, key=lambda v: _VERDICT_RANK.get(v, 0))
            await self.db.conn.execute(
                """
                UPDATE token_outcomes
                SET best_verdict = ?, best_score = ?
                WHERE token_mint = ?
                """,
                (top_verdict, best_score, mint),
            )
        await self.db.conn.commit()

    async def _all_tracked_mints(self) -> list[str]:
        assert self.db.conn is not None
        async with self.db.conn.execute(
            "SELECT token_mint FROM token_outcomes ORDER BY first_scored_at"
        ) as cur:
            return [r[0] async for r in cur]

    async def _fetch_batch(self, mints: list[str]) -> dict[str, dict[str, Any] | None]:
        """Hit DexScreener batch endpoint, return {mint: best_pair_or_None}."""
        url = DEXSCREENER_BATCH.format(mints=",".join(mints))
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("dexscreener_batch_error", error=str(e), count=len(mints))
            return {m: None for m in mints}
        if resp.status_code != 200:
            logger.warning(
                "dexscreener_batch_non_200",
                status=resp.status_code,
                count=len(mints),
            )
            return {m: None for m in mints}
        try:
            body = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("dexscreener_batch_bad_json", error=str(e))
            return {m: None for m in mints}

        pairs = body.get("pairs") or []
        # Group pairs by base token mint, keep highest-liquidity pair per mint
        by_mint: dict[str, dict[str, Any]] = {}
        for p in pairs:
            base = (p.get("baseToken") or {}).get("address")
            if not base:
                continue
            liq = _as_float(((p.get("liquidity") or {}).get("usd"))) or 0.0
            existing = by_mint.get(base)
            existing_liq = (
                _as_float(((existing.get("liquidity") or {}).get("usd"))) or 0.0
                if existing
                else -1.0
            )
            if liq > existing_liq:
                by_mint[base] = p

        return {m: by_mint.get(m) for m in mints}

    async def _apply_pair(
        self, mint: str, pair: dict[str, Any] | None
    ) -> tuple[bool, bool]:
        """Update token_outcomes for one mint. Returns (row_changed, moonshot_fired)."""
        assert self.db.conn is not None
        now_iso = datetime.now(timezone.utc).isoformat()

        if pair is None:
            await self.db.conn.execute(
                """
                UPDATE token_outcomes
                SET last_checked_at = ?, last_error = 'no_pair_found'
                WHERE token_mint = ?
                """,
                (now_iso, mint),
            )
            await self.db.conn.commit()
            return False, False

        price_usd = _as_float(pair.get("priceUsd"))
        # Prefer fdv (FDV mcap) — it's what memecoin culture quotes. Fall back to marketCap.
        mcap_usd = _as_float(pair.get("fdv")) or _as_float(pair.get("marketCap"))
        pair_address = pair.get("pairAddress")
        dex_id = pair.get("dexId")

        if price_usd is None or mcap_usd is None:
            await self.db.conn.execute(
                """
                UPDATE token_outcomes
                SET last_checked_at = ?, last_error = 'incomplete_pair_data'
                WHERE token_mint = ?
                """,
                (now_iso, mint),
            )
            await self.db.conn.commit()
            return False, False

        # Read current row to compute peak + entry-once
        async with self.db.conn.execute(
            """
            SELECT entry_price_usd, entry_mcap_usd, peak_mcap_usd,
                   moonshot_alerted, best_verdict, best_score
            FROM token_outcomes WHERE token_mint = ?
            """,
            (mint,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return False, False

        entry_price, entry_mcap, peak_mcap, alerted, best_verdict, best_score = row
        new_entry_price = entry_price if entry_price is not None else price_usd
        new_entry_mcap = entry_mcap if entry_mcap is not None else mcap_usd

        moonshot_fired = False
        if peak_mcap is None or mcap_usd > peak_mcap:
            new_peak_mcap = mcap_usd
            new_peak_price = price_usd
            new_peak_seen = now_iso
        else:
            new_peak_mcap = peak_mcap
            new_peak_price = None  # unchanged path uses COALESCE below
            new_peak_seen = None

        # Moonshot fires once per mint, only on first crossing
        if (
            not alerted
            and mcap_usd >= self.moonshot_mcap_usd
        ):
            moonshot_fired = True

        await self.db.conn.execute(
            """
            UPDATE token_outcomes
            SET entry_price_usd = COALESCE(entry_price_usd, ?),
                entry_mcap_usd  = COALESCE(entry_mcap_usd, ?),
                current_price_usd = ?,
                current_mcap_usd = ?,
                peak_price_usd = CASE WHEN ? IS NOT NULL THEN ? ELSE peak_price_usd END,
                peak_mcap_usd  = ?,
                peak_seen_at   = CASE WHEN ? IS NOT NULL THEN ? ELSE peak_seen_at END,
                pair_address   = ?,
                dex_id         = ?,
                moonshot_alerted = CASE WHEN ? THEN 1 ELSE moonshot_alerted END,
                last_checked_at = ?,
                last_error = NULL
            WHERE token_mint = ?
            """,
            (
                new_entry_price, new_entry_mcap,
                price_usd, mcap_usd,
                new_peak_price, new_peak_price,
                new_peak_mcap,
                new_peak_seen, new_peak_seen,
                pair_address, dex_id,
                1 if moonshot_fired else 0,
                now_iso,
                mint,
            ),
        )
        await self.db.conn.commit()

        if moonshot_fired:
            await self._fire_moonshot_alert(
                mint=mint,
                peak_mcap=mcap_usd,
                entry_mcap=new_entry_mcap,
                best_verdict=best_verdict,
                best_score=best_score,
                pair_address=pair_address,
            )

        return True, moonshot_fired

    async def _fire_moonshot_alert(
        self,
        mint: str,
        peak_mcap: float,
        entry_mcap: float,
        best_verdict: str,
        best_score: float,
        pair_address: str | None,
    ) -> None:
        """Push a Telegram alert. Tag as FILTER_MISS if we ignored it."""
        was_missed = best_verdict == "ignore"
        kind = "filter_miss" if was_missed else "moonshot_caught"
        multiple = peak_mcap / entry_mcap if entry_mcap else None
        payload = {
            "type": "moonshot",
            "kind": kind,
            "token_mint": mint,
            "best_verdict": best_verdict,
            "best_score": best_score,
            "entry_mcap_usd": entry_mcap,
            "peak_mcap_usd": peak_mcap,
            "multiple": multiple,
            "dexscreener_url": (
                f"https://dexscreener.com/solana/{pair_address}"
                if pair_address
                else f"https://dexscreener.com/solana/{mint}"
            ),
        }
        try:
            await self.alert_bus.put(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("moonshot_alert_enqueue_failed", error=str(e), mint=mint)

        logger.info(
            "moonshot_detected",
            kind=kind,
            mint=mint,
            verdict=best_verdict,
            entry_mcap=entry_mcap,
            peak_mcap=peak_mcap,
            multiple=multiple,
        )


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _chunks(lst: list[str], size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
