"""FilterPipeline orchestrator.

Consumes EnrichedToken from `enriched_bus`, runs the sync filters in order,
short-circuits on any hard-gate failure, then runs the (async) probe filter
on survivors. Each candidate runs as its own asyncio task so slow probes
do not block other candidates. Persists all FilterResults to the
`filter_results` table. Emits FilteredCandidate on `filtered_bus`.
"""
import asyncio
import json

from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.pipeline")


class FilterPipeline:
    def __init__(
        self,
        enriched_bus: asyncio.Queue,
        filtered_bus: asyncio.Queue,
        sync_filters: list[BaseFilter],
        probe_filter: BaseFilter | None,
        db: Database | None = None,
    ):
        self.enriched_bus = enriched_bus
        self.filtered_bus = filtered_bus
        self.sync_filters = sync_filters
        self.probe_filter = probe_filter
        self.db = db
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        logger.info(
            "filter_pipeline_start",
            sync_filters=[f.name for f in self.sync_filters],
            probe_filter=self.probe_filter.name if self.probe_filter else None,
        )
        while True:
            enriched: EnrichedToken = await self.enriched_bus.get()
            # Spawn a per-candidate task so probes don't block other candidates.
            # Strong-ref via self._tasks to prevent GC mid-execution (RUF006).
            task = asyncio.create_task(self._process_one(enriched))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _process_one(self, enriched: EnrichedToken) -> None:
        results: list[FilterResult] = []
        gate_passed = True
        hard_fail_reason: str | None = None
        hard_fail_filter_name: str | None = None

        for f in self.sync_filters:
            try:
                result = await f.apply(enriched)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "filter_crashed",
                    filter_name=f.name,
                    mint=enriched.token_mint,
                    error=str(e),
                )
                result = FilterResult(
                    filter_name=f.name,
                    passed=False,
                    hard_fail_reason=f"filter_crashed: {e}",
                    sub_scores={},
                    evidence={},
                )
            results.append(result)
            if not result.passed:
                gate_passed = False
                hard_fail_reason = result.hard_fail_reason
                hard_fail_filter_name = f.name
                break

        # Only run probe if all sync filters passed.
        if gate_passed and self.probe_filter is not None:
            try:
                probe_result = await self.probe_filter.apply(enriched)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "probe_crashed",
                    mint=enriched.token_mint,
                    error=str(e),
                )
                probe_result = FilterResult(
                    filter_name=self.probe_filter.name,
                    passed=True,
                    hard_fail_reason=None,
                    sub_scores={},
                    evidence={"errors": [f"probe_crashed: {e}"]},
                )
            results.append(probe_result)

        fc = FilteredCandidate(
            enriched=enriched,
            filter_results=results,
            gate_passed=gate_passed,
            hard_fail_reason=hard_fail_reason,
            hard_fail_filter_name=hard_fail_filter_name,
        )

        await self._persist(fc)
        await self.filtered_bus.put(fc)
        logger.info(
            "candidate_filtered",
            mint=enriched.token_mint,
            gate_passed=gate_passed,
            hard_fail_reason=hard_fail_reason,
            filter_count=len(results),
        )

    async def _persist(self, fc: FilteredCandidate) -> None:
        if self.db is None or self.db.conn is None:
            return
        try:
            for result in fc.filter_results:
                await self.db.conn.execute(
                    """
                    INSERT INTO filter_results
                    (token_mint, filter_name, passed, hard_fail_reason,
                     sub_scores_json, evidence_json, cluster_signal_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fc.enriched.token_mint,
                        result.filter_name,
                        1 if result.passed else 0,
                        result.hard_fail_reason,
                        json.dumps(result.sub_scores),
                        json.dumps(result.evidence, default=str),
                        fc.enriched.cluster_signal_id,
                    ),
                )
            await self.db.conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "filter_results_persist_failed",
                mint=fc.enriched.token_mint,
                error=str(e),
            )
