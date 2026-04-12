"""PaperExecutor — opens paper positions for eligible scored candidates."""
import asyncio
import json

from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.scoring.models import ScoredCandidate
from runner.utils.logging import get_logger

logger = get_logger("runner.executor.paper")

_ELIGIBLE_VERDICTS = ("strong_candidate", "probable_runner")


class PaperExecutor:
    def __init__(
        self,
        scored_bus: asyncio.Queue,
        alert_bus: asyncio.Queue,
        weights: WeightsLoader,
        price_fetcher,
        db: Database,
        enable_executor: bool = True,
    ):
        self.scored_bus = scored_bus
        self.alert_bus = alert_bus
        self.weights = weights
        self.price_fetcher = price_fetcher
        self.db = db
        self.enable_executor = enable_executor

    async def run(self) -> None:
        logger.info("paper_executor_start", enabled=self.enable_executor)
        while True:
            sc: ScoredCandidate = await self.scored_bus.get()
            await self._process_one(sc)

    async def _process_one(self, sc: ScoredCandidate) -> None:
        if sc.verdict not in _ELIGIBLE_VERDICTS:
            logger.debug("skip_verdict", mint=sc.filtered.enriched.token_mint, verdict=sc.verdict)
            return
        if not self.enable_executor:
            logger.debug("executor_disabled", mint=sc.filtered.enriched.token_mint)
            return
        if sc.runner_score_db_id is None:
            logger.warning("skip_no_db_id", mint=sc.filtered.enriched.token_mint)
            return

        mint = sc.filtered.enriched.token_mint
        symbol = sc.filtered.enriched.symbol

        price_data = await self.price_fetcher.fetch(mint)
        if price_data is None:
            logger.warning("skip_price_fetch_failed", mint=mint)
            return
        price_sol = price_data.get("price_sol")
        price_usd = price_data.get("price_usd")
        if not price_sol or price_sol <= 0:
            logger.warning("skip_invalid_price", mint=mint, price_sol=price_sol)
            return

        if sc.verdict == "probable_runner":
            amount_sol = float(self.weights.get("position_sizing.probable_runner_sol", 0.375))
        else:
            amount_sol = float(self.weights.get("position_sizing.strong_candidate_sol", 0.25))

        notes = json.dumps({"entry_price_source": "dexscreener"})

        assert self.db.conn is not None
        try:
            cursor = await self.db.conn.execute(
                """INSERT INTO paper_positions
                   (token_mint, symbol, runner_score_id, verdict, runner_score,
                    entry_price_sol, entry_price_usd, amount_sol, signal_time, notes_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mint, symbol, sc.runner_score_db_id, sc.verdict,
                 sc.runner_score, price_sol, price_usd, amount_sol,
                 sc.scored_at.isoformat(), notes),
            )
            await self.db.conn.commit()
            position_id = cursor.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                logger.info("skip_duplicate_score_id", mint=mint, score_id=sc.runner_score_db_id)
            else:
                logger.warning("paper_position_insert_failed", mint=mint, error=str(e))
            return

        sig = sc.filtered.enriched.cluster_signal
        alert = {
            "type": "runner_entry",
            "paper_position_id": position_id,
            "runner_score_id": sc.runner_score_db_id,
            "token_mint": mint,
            "symbol": symbol,
            "verdict": sc.verdict,
            "runner_score": sc.runner_score,
            "amount_sol": amount_sol,
            "entry_price_sol": price_sol,
            "entry_price_usd": price_usd,
            "cluster_summary": {
                "wallet_count": sig.wallet_count,
                "tier_counts": sig.tier_counts,
                "convergence_minutes": sig.convergence_seconds / 60.0,
            },
            "explanation": sc.explanation,
        }
        await self.alert_bus.put(alert)
        logger.info("paper_position_opened", mint=mint, symbol=symbol,
                     verdict=sc.verdict, score=sc.runner_score, amount_sol=amount_sol,
                     price_sol=price_sol, position_id=position_id)
