"""ScoringEngine — single-class scoring pipeline stage.

Consumes FilteredCandidate from filtered_bus, computes a weighted
Runner Score (0-100), assigns a verdict, persists to runner_scores,
and emits ScoredCandidate onto scored_bus.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from runner.cluster.wallet_tier import WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS, ScoredCandidate, Verdict
from runner.utils.logging import get_logger

logger = get_logger("runner.scoring.engine")


class ScoringEngine:
    """Scores filtered candidates and assigns verdicts.

    Public interface:
        run()  — long-lived async bus consumer (operational glue)
        score(fc) — pure scoring logic (no I/O, no side effects)
    """

    def __init__(
        self,
        filtered_bus: asyncio.Queue,
        scored_bus: asyncio.Queue,
        weights: WeightsLoader,
        tier_cache: WalletTierCache,
        db: Database | None = None,
    ):
        self.filtered_bus = filtered_bus
        self.scored_bus = scored_bus
        self.weights = weights
        self.tier_cache = tier_cache
        self.db = db
        self._reload_interval_sec: float = float(
            weights.get("scoring.reload_interval_sec", 30)
        )
        self._last_reload_check: float = time.monotonic()
        # Validate on startup — fail fast if initial weights are invalid.
        self._validate_weights()

    # ── public ─────────────────────────────────────────────────────

    def score(self, fc: FilteredCandidate) -> ScoredCandidate:
        """Pure scoring — no I/O, no reload checks. Primary test target."""
        now = datetime.now(timezone.utc)

        if not fc.gate_passed:
            return self._short_circuit(fc, now)

        dimensions = self._derive_dimensions(fc)
        runner_score = self._combine_scores(dimensions)
        verdict = self._assign_verdict(runner_score)
        explanation = self._build_explanation(fc, dimensions, runner_score, verdict)

        return ScoredCandidate(
            filtered=fc,
            runner_score=runner_score,
            verdict=verdict,
            dimension_scores=dimensions,
            explanation=explanation,
            scored_at=now,
        )

    # ── verdict + combine ──────────────────────────────────────────

    def _assign_verdict(self, score: float) -> Verdict:
        """Map score to verdict using threshold bands from weights.yaml."""
        pr = float(self.weights.get("verdict_thresholds.probable_runner", 78))
        sc = float(self.weights.get("verdict_thresholds.strong_candidate", 60))
        w = float(self.weights.get("verdict_thresholds.watch", 40))
        if score >= pr:
            return "probable_runner"
        if score >= sc:
            return "strong_candidate"
        if score >= w:
            return "watch"
        return "ignore"

    def _combine_scores(self, dimensions: dict[str, float]) -> float:
        """Weighted sum of dimension scores. Returns 0-100."""
        total = 0.0
        for key, dim_score in dimensions.items():
            weight = float(self.weights.get(f"weights.{key}", 0.0))
            total += weight * dim_score
        return max(0.0, min(100.0, total))

    def _validate_weights(self) -> None:
        """Check that dimension weights sum to ~1.0. Raises ValueError if not."""
        total = sum(
            float(self.weights.get(f"weights.{k}", 0.0)) for k in DIMENSION_KEYS
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Scoring dimension weights sum to {total:.4f}, expected ~1.0 "
                f"(tolerance 0.01). Check weights.yaml [weights] section."
            )

    # ── dimension derivation ───────────────────────────────────────

    def _derive_dimensions(self, fc: FilteredCandidate) -> dict[str, float]:
        """Build all 7 dimension scores from filter results + cluster signal."""
        missing: list[str] = []

        entry_quality = self._lookup_sub_score(fc, "entry_quality", "entry_quality", missing)
        holder_quality = self._lookup_sub_score(fc, "holder_filter", "holder_quality", missing)
        follow_through = self._lookup_sub_score(fc, "follow_through", "follow_through", missing)

        raw_rug = self._lookup_sub_score(fc, "rug_gate", "rug_risk", missing)
        raw_insider = self._lookup_sub_score(fc, "insider_filter", "insider_risk", missing)
        rug_risk = self._combine_rug_risk(raw_rug, raw_insider)

        wallet_quality = self._derive_wallet_quality(fc, missing)
        cluster_quality = self._derive_cluster_quality(fc)
        narrative = 50.0  # neutral placeholder

        return {
            "wallet_quality": wallet_quality,
            "cluster_quality": cluster_quality,
            "entry_quality": entry_quality,
            "holder_quality": holder_quality,
            "rug_risk": rug_risk,
            "follow_through": follow_through,
            "narrative": narrative,
        }

    def _lookup_sub_score(
        self,
        fc: FilteredCandidate,
        filter_name: str,
        score_key: str,
        missing: list[str],
    ) -> float:
        """Look up a sub-score from filter results. Returns neutral fallback if missing."""
        fallback = float(self.weights.get("scoring.neutral_fallback", 50))
        for result in fc.filter_results:
            if result.filter_name == filter_name:
                val = result.sub_scores.get(score_key)
                if val is not None:
                    return float(val)
                missing.append(score_key)
                return fallback
        missing.append(score_key)
        return fallback

    def _combine_rug_risk(self, raw_rug: float, raw_insider: float) -> float:
        """Weighted average of rug + insider with severe insider cap."""
        rug_w = float(self.weights.get("scoring.rug_insider_rug_weight", 0.70))
        insider_w = 1.0 - rug_w
        combined = rug_w * raw_rug + insider_w * raw_insider

        cap_threshold = float(self.weights.get("scoring.insider_cap_threshold", 25))
        cap_value = float(self.weights.get("scoring.insider_cap_value", 35))
        if raw_insider < cap_threshold:
            combined = min(combined, cap_value)

        return max(0.0, min(100.0, combined))

    def _derive_wallet_quality(
        self, fc: FilteredCandidate, missing: list[str]
    ) -> float:
        """Mean tier points of wallets in the cluster."""
        wallets = fc.enriched.cluster_signal.wallets
        if not wallets:
            missing.append("wallet_quality")
            fallback = float(self.weights.get("scoring.neutral_fallback", 50))
            return fallback
        points = [self.tier_cache.tier_of(w).points for w in wallets]
        return max(0.0, min(100.0, mean(points)))

    def _derive_cluster_quality(self, fc: FilteredCandidate) -> float:
        """Heuristic v1 cluster quality from wallet count + convergence speed."""
        signal = fc.enriched.cluster_signal
        base = 50.0
        wallet_bonus = min((signal.wallet_count - 3) * 10, 30)

        conv_min = signal.convergence_seconds / 60.0
        sweet_min = float(self.weights.get("cluster.speed_bonus_sweet_spot_min", 10))
        sweet_max = float(self.weights.get("cluster.speed_bonus_sweet_spot_max", 20))

        if sweet_min <= conv_min <= sweet_max:
            speed_bonus = 20.0
        elif 5.0 <= conv_min < sweet_min:
            speed_bonus = 10.0
        elif sweet_max < conv_min <= 30.0:
            speed_bonus = 10.0
        elif conv_min < 5.0:
            speed_bonus = -20.0
        else:
            speed_bonus = 0.0

        return max(0.0, min(100.0, base + wallet_bonus + speed_bonus))

    # ── short circuit ──────────────────────────────────────────────

    def _short_circuit(
        self, fc: FilteredCandidate, now: datetime
    ) -> ScoredCandidate:
        """Score a gate-failed candidate: score=0, verdict=ignore."""
        dimensions = {k: 0.0 for k in DIMENSION_KEYS}
        explanation = self._build_explanation(fc, dimensions, 0.0, "ignore")
        explanation["short_circuited"] = True
        explanation["failed_gate"] = fc.hard_fail_filter_name
        explanation["failed_reason"] = fc.hard_fail_reason
        return ScoredCandidate(
            filtered=fc,
            runner_score=0.0,
            verdict="ignore",
            dimension_scores=dimensions,
            explanation=explanation,
            scored_at=now,
        )

    # ── explanation ────────────────────────────────────────────────

    def _build_explanation(
        self,
        fc: FilteredCandidate,
        dimensions: dict[str, float],
        score: float,
        verdict: Verdict,
    ) -> dict[str, Any]:
        """Build full explanation dict for DB persistence + alerts."""
        missing = self._find_missing_subscores(fc) if fc.gate_passed else []

        scoring_cfg = self.weights.get("scoring", {})
        weights_cfg = self.weights.get("weights", {})
        hash_input = json.dumps({"scoring": scoring_cfg, "weights": weights_cfg}, sort_keys=True)
        weights_hash = hashlib.md5(hash_input.encode()).hexdigest()[:6]

        dim_detail: dict[str, dict[str, Any]] = {}
        for key in DIMENSION_KEYS:
            w = float(self.weights.get(f"weights.{key}", 0.0))
            s = dimensions[key]
            detail: dict[str, Any] = {}

            if key == "wallet_quality" and fc.gate_passed:
                wallets = fc.enriched.cluster_signal.wallets
                tiers = [self.tier_cache.tier_of(w_addr).label for w_addr in wallets]
                pts = [self.tier_cache.tier_of(w_addr).points for w_addr in wallets]
                detail = {"wallets": len(wallets), "tiers": tiers, "points": pts}
            elif key == "cluster_quality" and fc.gate_passed:
                sig = fc.enriched.cluster_signal
                conv_min = sig.convergence_seconds / 60.0
                detail = {
                    "wallet_count": sig.wallet_count,
                    "convergence_minutes": conv_min,
                }
            elif key == "rug_risk" and fc.gate_passed:
                raw_rug = self._lookup_sub_score_raw(fc, "rug_gate", "rug_risk")
                raw_ins = self._lookup_sub_score_raw(fc, "insider_filter", "insider_risk")
                cap_threshold = float(self.weights.get("scoring.insider_cap_threshold", 25))
                detail = {
                    "raw_rug": raw_rug,
                    "raw_insider": raw_ins,
                    "rug_weight": float(self.weights.get("scoring.rug_insider_rug_weight", 0.70)),
                    "insider_capped": raw_ins is not None and raw_ins < cap_threshold,
                }
            elif key == "narrative":
                detail = {"placeholder": True}

            dim_detail[key] = {
                "score": s,
                "weight": w,
                "weighted": round(w * s, 4),
                "detail": detail,
            }

        return {
            "scoring_version": self.weights.get("scoring.version", "v1"),
            "weights_mtime": self.weights.last_loaded_mtime,
            "weights_hash": weights_hash,
            "short_circuited": not fc.gate_passed,
            "data_degraded": len(missing) > 0,
            "missing_subscores": missing,
            "failed_gate": fc.hard_fail_filter_name if not fc.gate_passed else None,
            "failed_reason": fc.hard_fail_reason if not fc.gate_passed else None,
            "dimensions": dim_detail,
            "verdict_thresholds": {
                "watch": float(self.weights.get("verdict_thresholds.watch", 40)),
                "strong_candidate": float(self.weights.get("verdict_thresholds.strong_candidate", 60)),
                "probable_runner": float(self.weights.get("verdict_thresholds.probable_runner", 78)),
            },
        }

    def _find_missing_subscores(self, fc: FilteredCandidate) -> list[str]:
        """Identify sub-scores that fell back to neutral due to missing data."""
        expected = {
            "rug_gate": "rug_risk",
            "holder_filter": "holder_quality",
            "insider_filter": "insider_risk",
            "entry_quality": "entry_quality",
            "follow_through": "follow_through",
        }
        result_map = {r.filter_name: r for r in fc.filter_results}
        missing = []
        for filter_name, score_key in expected.items():
            result = result_map.get(filter_name)
            if result is None or result.sub_scores.get(score_key) is None:
                missing.append(score_key)
        return missing

    def _lookup_sub_score_raw(
        self, fc: FilteredCandidate, filter_name: str, score_key: str
    ) -> float | None:
        """Raw lookup without fallback — for explanation detail only."""
        for result in fc.filter_results:
            if result.filter_name == filter_name:
                return result.sub_scores.get(score_key)
        return None

    # ── async run loop ─────────────────────────────────────────────

    async def run(self) -> None:
        """Long-lived consumer: read filtered_bus, score, persist, emit."""
        logger.info("scoring_engine_start")
        while True:
            fc: FilteredCandidate = await self.filtered_bus.get()

            now_mono = time.monotonic()
            if now_mono - self._last_reload_check >= self._reload_interval_sec:
                self._last_reload_check = now_mono
                if self.weights.check_and_reload():
                    try:
                        self._validate_weights()
                        logger.info("weights_reloaded")
                    except ValueError as e:
                        logger.warning(
                            "weights_reload_invalid_reverting",
                            error=str(e),
                        )

            scored = self.score(fc)
            await self._persist(scored)
            await self.scored_bus.put(scored)

            logger.info(
                "candidate_scored",
                mint=scored.filtered.enriched.token_mint,
                score=round(scored.runner_score, 2),
                verdict=scored.verdict,
                short_circuited=scored.explanation.get("short_circuited", False),
            )

    async def _persist(self, sc: ScoredCandidate) -> None:
        """Insert scored candidate into runner_scores table."""
        if self.db is None or self.db.conn is None:
            return

        sub_scores = dict(sc.dimension_scores)
        raw_rug = self._lookup_sub_score_raw(sc.filtered, "rug_gate", "rug_risk")
        raw_insider = self._lookup_sub_score_raw(sc.filtered, "insider_filter", "insider_risk")
        sub_scores["raw_rug_risk"] = raw_rug if raw_rug is not None else 0.0
        sub_scores["raw_insider_risk"] = raw_insider if raw_insider is not None else 0.0

        try:
            await self.db.conn.execute(
                """
                INSERT INTO runner_scores
                (token_mint, cluster_signal_id, runner_score, verdict,
                 short_circuited, sub_scores_json, explanation_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sc.filtered.enriched.token_mint,
                    sc.filtered.enriched.cluster_signal_id,
                    sc.runner_score,
                    sc.verdict,
                    1 if sc.explanation.get("short_circuited") else 0,
                    json.dumps(sub_scores),
                    json.dumps(sc.explanation, default=str),
                ),
            )
            await self.db.conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "runner_scores_persist_failed",
                mint=sc.filtered.enriched.token_mint,
                error=str(e),
            )
