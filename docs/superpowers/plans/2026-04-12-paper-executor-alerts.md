# Paper Executor + Alerts + Outcome Tracking (Plan 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add paper trade execution, rich Telegram alerts, and milestone-based outcome tracking so the scoring engine's signal quality can be evaluated analytically.

**Architecture:** Three new pipeline stages replace `_drain_scored`: PaperExecutor opens paper positions for eligible verdicts, MilestoneSnapshotter captures price performance at fixed intervals (5m/30m/1h/4h/24h) with MFE/MAE tracking, TelegramAlerter sends formatted HTML entry + close alerts. All communicate via structured dicts on an asyncio Queue alert_bus.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, python-telegram-bot, PyYAML, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-paper-executor-alerts-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `meme-trading/runner/scoring/models.py` | Add `runner_score_db_id` field to ScoredCandidate |
| Modify | `meme-trading/runner/scoring/engine.py` | Return DB ID from `_persist()`, thread into ScoredCandidate |
| Modify | `meme-trading/runner/db/schema.sql` | Add `paper_positions` table |
| Modify | `meme-trading/runner/config/weights.yaml` | Add `executor` section |
| Create | `meme-trading/runner/executor/__init__.py` | Package marker |
| Create | `meme-trading/runner/executor/paper.py` | PaperExecutor class |
| Create | `meme-trading/runner/executor/snapshotter.py` | MilestoneSnapshotter class |
| Create | `meme-trading/runner/alerts/__init__.py` | Package marker |
| Create | `meme-trading/runner/alerts/formatting.py` | Pure formatting helpers |
| Create | `meme-trading/runner/alerts/telegram.py` | TelegramAlerter class |
| Modify | `meme-trading/runner/main.py` | Wire executor + snapshotter + alerter, remove `_drain_scored` |
| Create | `meme-trading/runner/tests/unit/test_alert_formatting.py` | Formatting tests |
| Create | `meme-trading/runner/tests/unit/test_paper_executor.py` | Executor tests |
| Create | `meme-trading/runner/tests/unit/test_snapshotter.py` | Snapshotter tests |
| Create | `meme-trading/runner/tests/unit/test_telegram_alerter.py` | Alerter tests |
| Create | `meme-trading/runner/tests/integration/test_scoring_to_executor.py` | End-to-end test |

---

### Task 1: Thread `runner_score_db_id` into ScoredCandidate + update ScoringEngine

**Files:**
- Modify: `meme-trading/runner/scoring/models.py`
- Modify: `meme-trading/runner/scoring/engine.py`
- Test: `meme-trading/runner/tests/unit/test_scoring_engine.py`

- [ ] **Step 1: Add `runner_score_db_id` to ScoredCandidate**

In `meme-trading/runner/scoring/models.py`, add the field at the end of the dataclass:

```python
@dataclass(frozen=True, eq=False)
class ScoredCandidate:
    """A candidate that has been scored by the ScoringEngine.

    `dimension_scores` always has all 7 DIMENSION_KEYS present (zeroed
    for short-circuited candidates). Keys match weights.yaml weight keys.
    """

    filtered: FilteredCandidate
    runner_score: float
    verdict: Verdict
    dimension_scores: dict[str, float]
    explanation: dict[str, Any]
    scored_at: datetime
    runner_score_db_id: int | None = None
```

- [ ] **Step 2: Update `_persist()` to return the DB row ID**

In `meme-trading/runner/scoring/engine.py`, change `_persist` return type from `None` to `int | None`. After the successful INSERT+commit, return `cursor.lastrowid`. On failure, return `None`.

Replace the `_persist` method:

```python
    async def _persist(self, sc: ScoredCandidate) -> int | None:
        """Insert scored candidate into runner_scores table. Returns row ID or None."""
        if self.db is None or self.db.conn is None:
            return None

        sub_scores = dict(sc.dimension_scores)
        raw_rug = self._lookup_sub_score_raw(sc.filtered, "rug_gate", "rug_risk")
        raw_insider = self._lookup_sub_score_raw(sc.filtered, "insider_filter", "insider_risk")
        sub_scores["raw_rug_risk"] = raw_rug if raw_rug is not None else 0.0
        sub_scores["raw_insider_risk"] = raw_insider if raw_insider is not None else 0.0

        try:
            cursor = await self.db.conn.execute(
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
            return cursor.lastrowid
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "runner_scores_persist_failed",
                mint=sc.filtered.enriched.token_mint,
                error=str(e),
            )
            return None
```

- [ ] **Step 3: Update `run()` to thread the DB ID before emitting**

Add `from dataclasses import replace` to imports in `engine.py`. Update the `run()` loop:

```python
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
            db_id = await self._persist(scored)
            if db_id is not None:
                scored = replace(scored, runner_score_db_id=db_id)
            await self.scored_bus.put(scored)

            logger.info(
                "candidate_scored",
                mint=scored.filtered.enriched.token_mint,
                score=round(scored.runner_score, 2),
                verdict=scored.verdict,
                short_circuited=scored.explanation.get("short_circuited", False),
            )
```

- [ ] **Step 4: Run all tests to verify no regressions**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -15`

Expected: All 157 tests pass. The new field has a default so nothing breaks.

- [ ] **Step 5: Commit**

```bash
git add runner/scoring/models.py runner/scoring/engine.py
git commit -m "runner: thread runner_score_db_id into ScoredCandidate from persist"
```

---

### Task 2: Schema + weights.yaml additions

**Files:**
- Modify: `meme-trading/runner/db/schema.sql`
- Modify: `meme-trading/runner/config/weights.yaml`

- [ ] **Step 1: Add `paper_positions` table to schema.sql**

Append to `meme-trading/runner/db/schema.sql`, before the `schema_version` section:

```sql
-- Paper positions — one per scored candidate that reached execution threshold.
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    symbol TEXT,
    runner_score_id INTEGER NOT NULL REFERENCES runner_scores(id),
    verdict TEXT NOT NULL,
    runner_score REAL NOT NULL,
    entry_price_sol REAL NOT NULL,
    entry_price_usd REAL,
    amount_sol REAL NOT NULL,
    signal_time TIMESTAMP NOT NULL,
    entry_source TEXT NOT NULL DEFAULT 'paper_executor_v1',
    price_5m_sol REAL, pnl_5m_pct REAL,
    price_30m_sol REAL, pnl_30m_pct REAL,
    price_1h_sol REAL, pnl_1h_pct REAL,
    price_4h_sol REAL, pnl_4h_pct REAL,
    price_24h_sol REAL, pnl_24h_pct REAL,
    max_favorable_pct REAL DEFAULT 0.0,
    max_adverse_pct REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    close_reason TEXT CHECK (close_reason IN ('completed', 'error')),
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    notes_json TEXT,
    UNIQUE(runner_score_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_mint ON paper_positions(token_mint);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_verdict ON paper_positions(verdict);
```

- [ ] **Step 2: Add `executor` section to weights.yaml**

Append to `meme-trading/runner/config/weights.yaml`:

```yaml

executor:
  check_interval_sec: 30
  error_closure_hours: 36
```

- [ ] **Step 3: Run all tests to verify schema bootstraps correctly**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -15`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add runner/db/schema.sql runner/config/weights.yaml
git commit -m "runner: paper_positions schema + executor weights config"
```

---

### Task 3: Alert formatting helpers

**Files:**
- Create: `meme-trading/runner/alerts/__init__.py`
- Create: `meme-trading/runner/alerts/formatting.py`
- Create: `meme-trading/runner/tests/unit/test_alert_formatting.py`

- [ ] **Step 1: Write the formatting tests**

Create `meme-trading/runner/tests/unit/test_alert_formatting.py`:

```python
"""Alert formatting helper tests — pure functions, no I/O."""
from runner.alerts.formatting import (
    escape_html,
    format_cautions,
    format_close_alert,
    format_entry_alert,
    format_top_reasons,
    mint_short,
)


def _explanation(overrides=None):
    """Build a representative explanation dict."""
    base = {
        "scoring_version": "v1",
        "weights_mtime": 1744451400.0,
        "weights_hash": "abc123",
        "short_circuited": False,
        "data_degraded": False,
        "missing_subscores": [],
        "failed_gate": None,
        "failed_reason": None,
        "dimensions": {
            "wallet_quality": {"score": 87, "weight": 0.20, "weighted": 17.4, "detail": {}},
            "cluster_quality": {"score": 70, "weight": 0.15, "weighted": 10.5, "detail": {}},
            "entry_quality": {"score": 75, "weight": 0.15, "weighted": 11.25, "detail": {}},
            "holder_quality": {"score": 38, "weight": 0.15, "weighted": 5.7, "detail": {}},
            "rug_risk": {"score": 77, "weight": 0.15, "weighted": 11.55, "detail": {"insider_capped": False}},
            "follow_through": {"score": 60, "weight": 0.15, "weighted": 9.0, "detail": {}},
            "narrative": {"score": 50, "weight": 0.05, "weighted": 2.5, "detail": {"placeholder": True}},
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
    }
    if overrides:
        base.update(overrides)
    return base


# ──── format_top_reasons ─────────────────────────────────────────

def test_top_reasons_excludes_narrative_placeholder():
    reasons = format_top_reasons(_explanation())
    names = [r[0] for r in reasons]
    assert "narrative" not in names


def test_top_reasons_sorted_by_weighted_descending():
    reasons = format_top_reasons(_explanation())
    weighted_vals = [r[3] for r in reasons]
    assert weighted_vals == sorted(weighted_vals, reverse=True)


def test_top_reasons_returns_max_3():
    reasons = format_top_reasons(_explanation())
    assert len(reasons) <= 3


def test_top_reasons_first_is_wallet_quality():
    reasons = format_top_reasons(_explanation())
    assert reasons[0][0] == "wallet_quality"
    assert reasons[0][3] == 17.4


# ──── format_cautions ────────────────────────────────────────────

def test_cautions_shows_low_dimension():
    cautions = format_cautions(_explanation())
    assert any("holder_quality" in c.lower() or "Holder" in c for c in cautions)


def test_cautions_with_data_degraded():
    exp = _explanation({"data_degraded": True, "missing_subscores": ["follow_through"]})
    cautions = format_cautions(exp)
    assert any("degraded" in c.lower() or "missing" in c.lower() for c in cautions)


def test_cautions_with_insider_cap():
    exp = _explanation()
    exp["dimensions"]["rug_risk"]["detail"]["insider_capped"] = True
    cautions = format_cautions(exp)
    assert any("insider" in c.lower() for c in cautions)


def test_cautions_none_returns_no_major():
    exp = _explanation()
    exp["dimensions"]["holder_quality"]["score"] = 50  # no longer below 40
    cautions = format_cautions(exp)
    assert cautions == ["No major cautions"]


# ──── format_entry_alert ─────────────────────────────────────────

def _entry_alert():
    return {
        "type": "runner_entry",
        "paper_position_id": 1,
        "runner_score_id": 42,
        "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
        "symbol": "$WIFHAT",
        "verdict": "strong_candidate",
        "runner_score": 72.0,
        "amount_sol": 0.25,
        "entry_price_sol": 0.00042,
        "entry_price_usd": 0.067,
        "cluster_summary": {
            "wallet_count": 4,
            "tier_counts": {"A": 2, "B": 1, "U": 1},
            "convergence_minutes": 14.0,
        },
        "explanation": _explanation(),
    }


def test_entry_alert_contains_verdict():
    html = format_entry_alert(_entry_alert())
    assert "STRONG CANDIDATE" in html
    assert "(72" in html


def test_entry_alert_contains_token():
    html = format_entry_alert(_entry_alert())
    assert "$WIFHAT" in html
    assert "5HpY" in html


def test_entry_alert_contains_links():
    html = format_entry_alert(_entry_alert())
    assert "dexscreener.com/solana/" in html
    assert "solscan.io/token/" in html


def test_entry_alert_escapes_symbol():
    alert = _entry_alert()
    alert["symbol"] = "<script>bad</script>"
    html = format_entry_alert(alert)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ──── format_close_alert ─────────────────────────────────────────

def _close_alert():
    return {
        "type": "runner_close",
        "paper_position_id": 1,
        "runner_score_id": 42,
        "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
        "symbol": "$WIFHAT",
        "verdict": "strong_candidate",
        "runner_score": 72.0,
        "entry_price_sol": 0.00042,
        "entry_price_usd": 0.067,
        "exit_price_sol": 0.00050,
        "milestones": {
            "5m": 8.1,
            "30m": 22.4,
            "1h": 45.2,
            "4h": 31.0,
            "24h": 18.3,
        },
        "max_favorable_pct": 52.1,
        "max_adverse_pct": -3.2,
    }


def test_close_alert_contains_pnl():
    html = format_close_alert(_close_alert())
    assert "18.3%" in html


def test_close_alert_shows_milestones():
    html = format_close_alert(_close_alert())
    assert "5m:" in html
    assert "24h:" in html


def test_close_alert_skips_missing_milestones():
    alert = _close_alert()
    alert["milestones"]["4h"] = None
    html = format_close_alert(alert)
    assert "4h:" not in html
    assert "24h:" in html


def test_close_alert_shows_mfe_mae():
    html = format_close_alert(_close_alert())
    assert "MFE" in html
    assert "MAE" in html


# ──── helpers ────────────────────────────────────────────────────

def test_mint_short():
    assert mint_short("5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1") == "5HpY...abc1"


def test_escape_html():
    assert escape_html("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"
    assert escape_html("A & B") == "A &amp; B"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest runner/tests/unit/test_alert_formatting.py -v`

Expected: FAIL — `runner.alerts.formatting` does not exist.

- [ ] **Step 3: Create the alerts package and formatting module**

Create `meme-trading/runner/alerts/__init__.py` (empty).

Create `meme-trading/runner/alerts/formatting.py`:

```python
"""Pure alert formatting helpers — no I/O, fully testable."""
import html as html_lib


def escape_html(text: str) -> str:
    """Escape <, >, & in user/token-derived text."""
    return html_lib.escape(str(text))


def mint_short(mint: str) -> str:
    """Shorten a mint address to 4...4 form."""
    if len(mint) <= 10:
        return mint
    return f"{mint[:4]}...{mint[-4:]}"


def _truncate_symbol(symbol: str | None) -> str:
    """Cap symbol at 12 chars, default to empty."""
    if not symbol:
        return ""
    s = str(symbol)[:12]
    return s


def format_top_reasons(
    explanation: dict,
) -> list[tuple[str, float, float, float]]:
    """Top 3 dimensions by weighted contribution.

    Excludes placeholder dimensions (narrative v1).
    Returns [(name, score, weight, weighted), ...].
    """
    dims = explanation.get("dimensions", {})
    candidates = []
    for name, info in dims.items():
        if info.get("detail", {}).get("placeholder"):
            continue
        candidates.append((
            name,
            float(info.get("score", 0)),
            float(info.get("weight", 0)),
            float(info.get("weighted", 0)),
        ))
    candidates.sort(key=lambda x: x[3], reverse=True)
    return candidates[:3]


def format_cautions(explanation: dict) -> list[str]:
    """Build caution strings from explanation.

    Returns ["No major cautions"] if nothing notable.
    """
    cautions: list[str] = []
    dims = explanation.get("dimensions", {})

    for name, info in dims.items():
        score = float(info.get("score", 100))
        if score < 40:
            label = name.replace("_", " ").title()
            cautions.append(f"{label} {score:.0f} — below threshold")

    if explanation.get("data_degraded"):
        missing = explanation.get("missing_subscores", [])
        cautions.append(f"Data degraded — missing {', '.join(missing)}")

    rug_detail = dims.get("rug_risk", {}).get("detail", {})
    if rug_detail.get("insider_capped"):
        cautions.append("Insider risk cap triggered")

    return cautions if cautions else ["No major cautions"]


def _format_verdict_label(verdict: str) -> str:
    """Convert verdict to display label."""
    return verdict.upper().replace("_", " ")


def _format_pnl(pnl: float | None) -> str:
    """Format P&L percentage with sign."""
    if pnl is None:
        return "N/A"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.1f}%"


def format_entry_alert(alert: dict) -> str:
    """Format an entry alert dict as HTML for Telegram."""
    verdict_label = _format_verdict_label(alert["verdict"])
    score = alert["runner_score"]
    symbol = escape_html(_truncate_symbol(alert.get("symbol")))
    mint = alert["token_mint"]
    short = mint_short(mint)
    amount = alert["amount_sol"]
    price_usd = alert.get("entry_price_usd")
    price_str = f"${price_usd:.6g}" if price_usd else f"{alert['entry_price_sol']:.8g} SOL"

    cluster = alert.get("cluster_summary", {})
    wc = cluster.get("wallet_count", 0)
    tiers = cluster.get("tier_counts", {})
    tier_parts = [f"{v}{k}" for k, v in sorted(tiers.items())]
    tier_str = ", ".join(tier_parts) if tier_parts else ""
    conv_min = cluster.get("convergence_minutes", 0)

    reasons = format_top_reasons(alert.get("explanation", {}))
    cautions = format_cautions(alert.get("explanation", {}))

    lines = [
        f"<b>FROM: RUNNER • {verdict_label} ({score:.0f})</b>",
        "",
        f"<b>{symbol}</b> • <code>{short}</code>",
        f"Cluster: {wc} wallets ({tier_str}) in {conv_min:.0f} min",
        "",
        "<b>Why it scored well:</b>",
    ]
    for i, (name, s, w, wd) in enumerate(reasons, 1):
        label = name.replace("_", " ").title()
        lines.append(f"  {i}. {label}  {s:.0f} (x{w:.2f} = {wd:.1f})")

    lines.append("")
    lines.append("<b>Cautions:</b>")
    for c in cautions:
        lines.append(f"  {escape_html(c)}")

    lines.append("")
    lines.append(f"Paper entry: {amount} SOL @ {price_str}")
    lines.append("")
    lines.append(
        f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
        f' | <a href="https://solscan.io/token/{mint}">Solscan</a>'
    )

    return "\n".join(lines)


def format_close_alert(alert: dict) -> str:
    """Format a close alert dict as HTML for Telegram."""
    verdict_label = _format_verdict_label(alert["verdict"])
    score = alert["runner_score"]
    symbol = escape_html(_truncate_symbol(alert.get("symbol")))
    entry_usd = alert.get("entry_price_usd")
    exit_sol = alert.get("exit_price_sol")
    entry_str = f"${entry_usd:.6g}" if entry_usd else f"{alert.get('entry_price_sol', 0):.8g} SOL"

    milestones = alert.get("milestones", {})
    final_pnl = milestones.get("24h")
    if final_pnl is None and exit_sol and alert.get("entry_price_sol"):
        entry_p = alert["entry_price_sol"]
        if entry_p > 0:
            final_pnl = (exit_sol - entry_p) / entry_p * 100.0

    mfe = alert.get("max_favorable_pct", 0)
    mae = alert.get("max_adverse_pct", 0)

    lines = [
        f"<b>FROM: RUNNER • CLOSED • {symbol} ({score:.0f} → {verdict_label})</b>",
        "",
        f"Final P&L: {_format_pnl(final_pnl)}",
        f"Entry: {entry_str} → Exit: ${exit_sol:.6g}" if exit_sol else f"Entry: {entry_str}",
        "",
    ]

    # Only show milestones that were captured
    milestone_order = ["5m", "30m", "1h", "4h", "24h"]
    captured = [(k, milestones[k]) for k in milestone_order if milestones.get(k) is not None]
    if captured:
        lines.append("Milestones:")
        for label, pnl in captured:
            lines.append(f"  {label}:  {_format_pnl(pnl)}")
        lines.append("")

    lines.append(f"MFE: {_format_pnl(mfe)} | MAE: {_format_pnl(mae)}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest runner/tests/unit/test_alert_formatting.py -v`

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add runner/alerts/__init__.py runner/alerts/formatting.py runner/tests/unit/test_alert_formatting.py
git commit -m "runner: alert formatting helpers with top-3 reasons and cautions"
```

---

### Task 4: PaperExecutor

**Files:**
- Create: `meme-trading/runner/executor/__init__.py`
- Create: `meme-trading/runner/executor/paper.py`
- Create: `meme-trading/runner/tests/unit/test_paper_executor.py`

- [ ] **Step 1: Write the PaperExecutor tests**

Create `meme-trading/runner/tests/unit/test_paper_executor.py`:

```python
"""PaperExecutor unit tests — real DB, mock price fetcher."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS, ScoredCandidate


def _weights_file(tmp_path: Path) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring": {
            "rug_insider_rug_weight": 0.70, "insider_cap_threshold": 25,
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
        "position_sizing": {"strong_candidate_sol": 0.25, "probable_runner_sol": 0.375},
        "executor": {"check_interval_sec": 30, "error_closure_hours": 36},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _scored_candidate(verdict="strong_candidate", score=72.0, db_id=42):
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="MINT1", wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=7,
    )
    enriched = EnrichedToken(
        token_mint="MINT1", cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
        price_sol=0.0006, symbol="$TEST", cluster_signal_id=7,
    )
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=[
            FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
            FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
            FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
            FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
            FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
        ],
        gate_passed=True, hard_fail_reason=None,
    )
    dims = {k: 50.0 for k in DIMENSION_KEYS}
    explanation = {
        "short_circuited": False, "data_degraded": False, "missing_subscores": [],
        "failed_gate": None, "failed_reason": None,
        "dimensions": {k: {"score": 50, "weight": 0.15, "weighted": 7.5, "detail": {}} for k in DIMENSION_KEYS},
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring_version": "v1", "weights_mtime": 0, "weights_hash": "abc123",
    }
    return ScoredCandidate(
        filtered=fc, runner_score=score, verdict=verdict,
        dimension_scores=dims, explanation=explanation,
        scored_at=base + timedelta(minutes=16), runner_score_db_id=db_id,
    )


def _mock_price_fetcher(price_sol=0.0006, price_usd=0.096):
    fetcher = AsyncMock()
    fetcher.fetch.return_value = {"price_sol": price_sol, "price_usd": price_usd}
    return fetcher


async def _setup(tmp_path, enable=True):
    from runner.executor.paper import PaperExecutor

    db = Database(tmp_path / "r.db")
    await db.connect()
    # Insert a runner_scores row so FK is satisfied
    await db.conn.execute(
        """INSERT INTO runner_scores
           (id, token_mint, runner_score, verdict, short_circuited, sub_scores_json, explanation_json)
           VALUES (42, 'MINT1', 72.0, 'strong_candidate', 0, '{}', '{}')"""
    )
    await db.conn.commit()

    scored_bus = asyncio.Queue()
    alert_bus = asyncio.Queue()
    weights = WeightsLoader(_weights_file(tmp_path))
    price_fetcher = _mock_price_fetcher()

    executor = PaperExecutor(
        scored_bus=scored_bus, alert_bus=alert_bus,
        weights=weights, price_fetcher=price_fetcher,
        db=db, enable_executor=enable,
    )
    return executor, db, scored_bus, alert_bus, price_fetcher


@pytest.mark.asyncio
async def test_opens_position_for_strong_candidate(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="strong_candidate")
    await executor._process_one(sc)

    async with db.conn.execute("SELECT * FROM paper_positions WHERE token_mint='MINT1'") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[4] == "strong_candidate"  # verdict
    assert row[8] == 0.25  # amount_sol (strong_candidate_sol)

    alert = alert_bus.get_nowait()
    assert alert["type"] == "runner_entry"
    await db.close()


@pytest.mark.asyncio
async def test_opens_position_for_probable_runner(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    # Need to insert a runner_scores row with id=99 for FK
    await db.conn.execute(
        """INSERT INTO runner_scores
           (id, token_mint, runner_score, verdict, short_circuited, sub_scores_json, explanation_json)
           VALUES (99, 'MINT1', 82.0, 'probable_runner', 0, '{}', '{}')"""
    )
    await db.conn.commit()
    sc = _scored_candidate(verdict="probable_runner", score=82.0, db_id=99)
    await executor._process_one(sc)

    async with db.conn.execute("SELECT amount_sol FROM paper_positions WHERE runner_score_id=99") as cur:
        row = await cur.fetchone()
    assert row[0] == 0.375  # probable_runner_sol
    await db.close()


@pytest.mark.asyncio
async def test_skips_ignore_verdict(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="ignore", score=20.0)
    await executor._process_one(sc)

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_watch_verdict(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="watch", score=45.0)
    await executor._process_one(sc)

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    await db.close()


@pytest.mark.asyncio
async def test_skips_when_executor_disabled(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path, enable=False)
    sc = _scored_candidate()
    await executor._process_one(sc)

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    await db.close()


@pytest.mark.asyncio
async def test_skips_on_price_fetch_failure(tmp_path):
    executor, db, _, alert_bus, price_fetcher = await _setup(tmp_path)
    price_fetcher.fetch.return_value = None
    sc = _scored_candidate()
    await executor._process_one(sc)

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    await db.close()


@pytest.mark.asyncio
async def test_skips_duplicate_runner_score_id(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    sc = _scored_candidate()
    await executor._process_one(sc)
    # Second attempt with same runner_score_id should skip
    await executor._process_one(sc)

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 1
    await db.close()


@pytest.mark.asyncio
async def test_does_not_alert_if_no_db_id(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    sc = _scored_candidate(db_id=None)
    await executor._process_one(sc)

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_entry_alert_has_correct_fields(tmp_path):
    executor, db, _, alert_bus, _ = await _setup(tmp_path)
    sc = _scored_candidate()
    await executor._process_one(sc)

    alert = alert_bus.get_nowait()
    assert alert["type"] == "runner_entry"
    assert alert["token_mint"] == "MINT1"
    assert alert["verdict"] == "strong_candidate"
    assert alert["runner_score"] == 72.0
    assert alert["amount_sol"] == 0.25
    assert "entry_price_sol" in alert
    assert "cluster_summary" in alert
    assert "explanation" in alert
    assert "paper_position_id" in alert
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest runner/tests/unit/test_paper_executor.py -v`

Expected: FAIL — `runner.executor.paper` does not exist.

- [ ] **Step 3: Create the executor package and PaperExecutor**

Create `meme-trading/runner/executor/__init__.py` (empty).

Create `meme-trading/runner/executor/paper.py`:

```python
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
    """Consumes ScoredCandidate from scored_bus, opens paper positions."""

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

        # Fetch entry price
        price_data = await self.price_fetcher.fetch(mint)
        if price_data is None:
            logger.warning("skip_price_fetch_failed", mint=mint)
            return

        price_sol = price_data.get("price_sol")
        price_usd = price_data.get("price_usd")
        if not price_sol or price_sol <= 0:
            logger.warning("skip_invalid_price", mint=mint, price_sol=price_sol)
            return

        # Determine position size
        if sc.verdict == "probable_runner":
            amount_sol = float(self.weights.get("position_sizing.probable_runner_sol", 0.375))
        else:
            amount_sol = float(self.weights.get("position_sizing.strong_candidate_sol", 0.25))

        notes = json.dumps({"entry_price_source": "dexscreener"})

        # INSERT paper position
        assert self.db.conn is not None
        try:
            cursor = await self.db.conn.execute(
                """
                INSERT INTO paper_positions
                (token_mint, symbol, runner_score_id, verdict, runner_score,
                 entry_price_sol, entry_price_usd, amount_sol, signal_time, notes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mint, symbol, sc.runner_score_db_id, sc.verdict,
                    sc.runner_score, price_sol, price_usd, amount_sol,
                    sc.scored_at.isoformat(), notes,
                ),
            )
            await self.db.conn.commit()
            position_id = cursor.lastrowid
        except Exception as e:  # noqa: BLE001
            if "UNIQUE constraint failed" in str(e):
                logger.info("skip_duplicate_score_id", mint=mint, score_id=sc.runner_score_db_id)
            else:
                logger.warning("paper_position_insert_failed", mint=mint, error=str(e))
            return

        # Build entry alert
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

        logger.info(
            "paper_position_opened",
            mint=mint, symbol=symbol, verdict=sc.verdict,
            score=sc.runner_score, amount_sol=amount_sol,
            price_sol=price_sol, position_id=position_id,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest runner/tests/unit/test_paper_executor.py -v`

Expected: All 9 tests pass.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -15`

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add runner/executor/__init__.py runner/executor/paper.py runner/tests/unit/test_paper_executor.py
git commit -m "runner: PaperExecutor opens paper positions for eligible verdicts"
```

---

### Task 5: MilestoneSnapshotter

**Files:**
- Create: `meme-trading/runner/executor/snapshotter.py`
- Create: `meme-trading/runner/tests/unit/test_snapshotter.py`

- [ ] **Step 1: Write the snapshotter tests**

Create `meme-trading/runner/tests/unit/test_snapshotter.py`:

```python
"""MilestoneSnapshotter unit tests — real DB, mock price fetcher."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from runner.db.database import Database


def _weights_file(tmp_path: Path) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring": {
            "rug_insider_rug_weight": 0.70, "insider_cap_threshold": 25,
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
        "position_sizing": {"strong_candidate_sol": 0.25, "probable_runner_sol": 0.375},
        "executor": {"check_interval_sec": 30, "error_closure_hours": 36},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _mock_price_fetcher(price_sol=0.0008):
    fetcher = AsyncMock()
    fetcher.fetch.return_value = {"price_sol": price_sol, "price_usd": 0.128}
    return fetcher


async def _insert_position(db, signal_time, entry_price=0.0006, position_id=1, score_id=42):
    """Insert prerequisite runner_scores row + open paper position."""
    await db.conn.execute(
        """INSERT OR IGNORE INTO runner_scores
           (id, token_mint, runner_score, verdict, short_circuited, sub_scores_json, explanation_json)
           VALUES (?, 'MINT1', 72.0, 'strong_candidate', 0, '{}', '{}')""",
        (score_id,),
    )
    await db.conn.execute(
        """INSERT INTO paper_positions
           (id, token_mint, symbol, runner_score_id, verdict, runner_score,
            entry_price_sol, entry_price_usd, amount_sol, signal_time, status)
           VALUES (?, 'MINT1', '$TEST', ?, 'strong_candidate', 72.0, ?, 0.096, 0.25, ?, 'open')""",
        (position_id, score_id, entry_price, signal_time.isoformat()),
    )
    await db.conn.commit()


async def _setup(tmp_path):
    from runner.executor.snapshotter import MilestoneSnapshotter

    db = Database(tmp_path / "r.db")
    await db.connect()
    alert_bus = asyncio.Queue()
    price_fetcher = _mock_price_fetcher()

    snapshotter = MilestoneSnapshotter(
        alert_bus=alert_bus,
        price_fetcher=price_fetcher,
        db=db,
        check_interval_sec=0,  # no sleep in tests
    )
    return snapshotter, db, alert_bus, price_fetcher


@pytest.mark.asyncio
async def test_captures_5m_milestone(tmp_path):
    snapshotter, db, _, _ = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()

    async with db.conn.execute("SELECT price_5m_sol, pnl_5m_pct FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] is not None  # price_5m_sol captured
    assert row[1] is not None  # pnl_5m_pct captured
    # entry=0.0006, current=0.0008 → +33.3%
    assert abs(row[1] - 33.33) < 1.0
    await db.close()


@pytest.mark.asyncio
async def test_first_write_only(tmp_path):
    snapshotter, db, _, _ = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()

    # Change price, run again
    snapshotter.price_fetcher.fetch.return_value = {"price_sol": 0.0012, "price_usd": 0.192}
    await snapshotter._check_all()

    async with db.conn.execute("SELECT pnl_5m_pct FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    # Should still be ~33.3%, not updated to 100%
    assert abs(row[0] - 33.33) < 1.0
    await db.close()


@pytest.mark.asyncio
async def test_updates_mfe_mae(tmp_path):
    snapshotter, db, _, _ = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()  # +33.3%

    async with db.conn.execute("SELECT max_favorable_pct, max_adverse_pct FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] > 30.0  # MFE should be ~33.3%
    assert row[1] == 0.0  # MAE stays 0 since price went up
    await db.close()


@pytest.mark.asyncio
async def test_mae_is_negative_for_drawdowns(tmp_path):
    snapshotter, db, _, price_fetcher = await _setup(tmp_path)
    price_fetcher.fetch.return_value = {"price_sol": 0.0004, "price_usd": 0.064}  # below entry
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()

    async with db.conn.execute("SELECT max_adverse_pct FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] < 0  # MAE is negative
    await db.close()


@pytest.mark.asyncio
async def test_closes_at_24h(tmp_path):
    snapshotter, db, alert_bus, _ = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(hours=25)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()

    async with db.conn.execute("SELECT status, close_reason FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] == "closed"
    assert row[1] == "completed"

    alert = alert_bus.get_nowait()
    assert alert["type"] == "runner_close"
    assert "milestones" in alert
    await db.close()


@pytest.mark.asyncio
async def test_skips_on_price_fetch_failure(tmp_path):
    snapshotter, db, _, price_fetcher = await _setup(tmp_path)
    price_fetcher.fetch.return_value = None
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()

    async with db.conn.execute("SELECT price_5m_sol FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] is None  # no milestone written
    await db.close()


@pytest.mark.asyncio
async def test_error_closure_at_36h(tmp_path):
    snapshotter, db, _, price_fetcher = await _setup(tmp_path)
    price_fetcher.fetch.return_value = None  # persistent failure
    signal_time = datetime.now(timezone.utc) - timedelta(hours=37)
    await _insert_position(db, signal_time)

    await snapshotter._check_all()

    async with db.conn.execute("SELECT status, close_reason, notes_json FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] == "closed"
    assert row[1] == "error"
    notes = json.loads(row[2]) if row[2] else {}
    assert "error_closure_reason" in notes
    await db.close()


@pytest.mark.asyncio
async def test_skips_corrupted_entry_price(tmp_path):
    snapshotter, db, _, _ = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time, entry_price=0.0)

    await snapshotter._check_all()  # should not crash

    async with db.conn.execute("SELECT price_5m_sol FROM paper_positions WHERE id=1") as cur:
        row = await cur.fetchone()
    assert row[0] is None  # skipped
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest runner/tests/unit/test_snapshotter.py -v`

Expected: FAIL — `runner.executor.snapshotter` does not exist.

- [ ] **Step 3: Create MilestoneSnapshotter**

Create `meme-trading/runner/executor/snapshotter.py`:

```python
"""MilestoneSnapshotter — captures price performance at fixed intervals."""
import asyncio
import json
from datetime import datetime, timezone

from runner.db.database import Database
from runner.utils.logging import get_logger

logger = get_logger("runner.executor.snapshotter")

MILESTONES = [
    (5 * 60,    "5m",  "price_5m_sol",  "pnl_5m_pct"),
    (30 * 60,   "30m", "price_30m_sol", "pnl_30m_pct"),
    (60 * 60,   "1h",  "price_1h_sol",  "pnl_1h_pct"),
    (4 * 3600,  "4h",  "price_4h_sol",  "pnl_4h_pct"),
    (24 * 3600, "24h", "price_24h_sol", "pnl_24h_pct"),
]


class MilestoneSnapshotter:
    """Background loop that captures milestone price snapshots for open positions."""

    def __init__(
        self,
        alert_bus: asyncio.Queue,
        price_fetcher,
        db: Database,
        check_interval_sec: float = 30.0,
        error_closure_hours: float = 36.0,
    ):
        self.alert_bus = alert_bus
        self.price_fetcher = price_fetcher
        self.db = db
        self.check_interval_sec = check_interval_sec
        self.error_closure_hours = error_closure_hours

    async def run(self) -> None:
        logger.info("milestone_snapshotter_start", interval=self.check_interval_sec)
        while True:
            await asyncio.sleep(self.check_interval_sec)
            try:
                await self._check_all()
            except Exception as e:  # noqa: BLE001
                logger.warning("snapshotter_cycle_error", error=str(e))

    async def _check_all(self) -> None:
        assert self.db.conn is not None
        async with self.db.conn.execute(
            """SELECT id, token_mint, symbol, runner_score_id, verdict, runner_score,
                      entry_price_sol, entry_price_usd, signal_time,
                      price_5m_sol, price_30m_sol, price_1h_sol, price_4h_sol, price_24h_sol,
                      max_favorable_pct, max_adverse_pct, amount_sol
               FROM paper_positions WHERE status = 'open'"""
        ) as cur:
            rows = await cur.fetchall()

        for row in rows:
            pos = {
                "id": row[0], "token_mint": row[1], "symbol": row[2],
                "runner_score_id": row[3], "verdict": row[4], "runner_score": row[5],
                "entry_price_sol": row[6], "entry_price_usd": row[7],
                "signal_time": row[8],
                "price_5m_sol": row[9], "price_30m_sol": row[10],
                "price_1h_sol": row[11], "price_4h_sol": row[12],
                "price_24h_sol": row[13],
                "max_favorable_pct": row[14] or 0.0,
                "max_adverse_pct": row[15] or 0.0,
                "amount_sol": row[16],
            }
            await self._check_one(pos)

    async def _check_one(self, pos: dict) -> None:
        entry_price = pos["entry_price_sol"]
        if not entry_price or entry_price <= 0:
            logger.warning("skip_corrupted_entry_price", id=pos["id"], price=entry_price)
            return

        signal_time = datetime.fromisoformat(pos["signal_time"])
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed_sec = (now - signal_time).total_seconds()

        # Error closure check
        error_threshold_sec = self.error_closure_hours * 3600
        has_24h = pos["price_24h_sol"] is not None
        if elapsed_sec > error_threshold_sec and not has_24h:
            await self._error_close(pos)
            return

        # Fetch current price
        price_data = await self.price_fetcher.fetch(pos["token_mint"])
        if price_data is None or not price_data.get("price_sol"):
            logger.debug("price_fetch_failed", mint=pos["token_mint"], id=pos["id"])
            return

        current_price = float(price_data["price_sol"])
        pnl_pct = (current_price - entry_price) / entry_price * 100.0

        # Update MFE/MAE
        assert self.db.conn is not None
        await self.db.conn.execute(
            """UPDATE paper_positions
               SET max_favorable_pct = MAX(max_favorable_pct, ?),
                   max_adverse_pct = MIN(max_adverse_pct, ?)
               WHERE id = ?""",
            (pnl_pct, pnl_pct, pos["id"]),
        )
        await self.db.conn.commit()

        # Check milestones
        wrote_24h = False
        existing_prices = {
            "price_5m_sol": pos["price_5m_sol"],
            "price_30m_sol": pos["price_30m_sol"],
            "price_1h_sol": pos["price_1h_sol"],
            "price_4h_sol": pos["price_4h_sol"],
            "price_24h_sol": pos["price_24h_sol"],
        }

        for threshold_sec, label, price_col, pnl_col in MILESTONES:
            if elapsed_sec >= threshold_sec and existing_prices.get(price_col) is None:
                await self.db.conn.execute(
                    f"UPDATE paper_positions SET {price_col} = ?, {pnl_col} = ? "
                    f"WHERE id = ? AND {price_col} IS NULL",
                    (current_price, pnl_pct, pos["id"]),
                )
                await self.db.conn.commit()
                logger.info("milestone_captured", id=pos["id"], label=label, pnl=round(pnl_pct, 2))
                if label == "24h":
                    wrote_24h = True

        if wrote_24h:
            await self._complete_close(pos, current_price, pnl_pct)

    async def _complete_close(self, pos: dict, exit_price: float, final_pnl: float) -> None:
        assert self.db.conn is not None
        now = datetime.now(timezone.utc)
        await self.db.conn.execute(
            """UPDATE paper_positions
               SET status = 'closed', close_reason = 'completed', closed_at = ?
               WHERE id = ?""",
            (now.isoformat(), pos["id"]),
        )
        await self.db.conn.commit()

        # Read final milestone values for alert
        async with self.db.conn.execute(
            """SELECT pnl_5m_pct, pnl_30m_pct, pnl_1h_pct, pnl_4h_pct, pnl_24h_pct,
                      max_favorable_pct, max_adverse_pct
               FROM paper_positions WHERE id = ?""",
            (pos["id"],),
        ) as cur:
            row = await cur.fetchone()

        alert = {
            "type": "runner_close",
            "paper_position_id": pos["id"],
            "runner_score_id": pos["runner_score_id"],
            "token_mint": pos["token_mint"],
            "symbol": pos["symbol"],
            "verdict": pos["verdict"],
            "runner_score": pos["runner_score"],
            "entry_price_sol": pos["entry_price_sol"],
            "entry_price_usd": pos["entry_price_usd"],
            "exit_price_sol": exit_price,
            "milestones": {
                "5m": row[0], "30m": row[1], "1h": row[2],
                "4h": row[3], "24h": row[4],
            },
            "max_favorable_pct": row[5] or 0.0,
            "max_adverse_pct": row[6] or 0.0,
        }
        await self.alert_bus.put(alert)
        logger.info("paper_position_closed", id=pos["id"], pnl=round(final_pnl, 2))

    async def _error_close(self, pos: dict) -> None:
        assert self.db.conn is not None
        now = datetime.now(timezone.utc)
        notes = json.dumps({"error_closure_reason": "persistent_price_failures"})
        await self.db.conn.execute(
            """UPDATE paper_positions
               SET status = 'closed', close_reason = 'error', closed_at = ?, notes_json = ?
               WHERE id = ?""",
            (now.isoformat(), notes, pos["id"]),
        )
        await self.db.conn.commit()
        logger.warning("paper_position_error_closed", id=pos["id"], mint=pos["token_mint"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest runner/tests/unit/test_snapshotter.py -v`

Expected: All 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add runner/executor/snapshotter.py runner/tests/unit/test_snapshotter.py
git commit -m "runner: MilestoneSnapshotter with first-write-only milestones and MFE/MAE"
```

---

### Task 6: TelegramAlerter

**Files:**
- Create: `meme-trading/runner/alerts/telegram.py`
- Create: `meme-trading/runner/tests/unit/test_telegram_alerter.py`

- [ ] **Step 1: Write the TelegramAlerter tests**

Create `meme-trading/runner/tests/unit/test_telegram_alerter.py`:

```python
"""TelegramAlerter unit tests — mock Telegram bot."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from runner.alerts.telegram import TelegramAlerter


def _entry_alert():
    return {
        "type": "runner_entry",
        "paper_position_id": 1,
        "runner_score_id": 42,
        "token_mint": "MINT1",
        "symbol": "$TEST",
        "verdict": "strong_candidate",
        "runner_score": 72.0,
        "amount_sol": 0.25,
        "entry_price_sol": 0.0006,
        "entry_price_usd": 0.096,
        "cluster_summary": {"wallet_count": 3, "tier_counts": {"A": 2, "B": 1}, "convergence_minutes": 14},
        "explanation": {
            "dimensions": {
                "wallet_quality": {"score": 87, "weight": 0.20, "weighted": 17.4, "detail": {}},
                "cluster_quality": {"score": 70, "weight": 0.15, "weighted": 10.5, "detail": {}},
                "entry_quality": {"score": 75, "weight": 0.15, "weighted": 11.25, "detail": {}},
                "holder_quality": {"score": 60, "weight": 0.15, "weighted": 9.0, "detail": {}},
                "rug_risk": {"score": 77, "weight": 0.15, "weighted": 11.55, "detail": {"insider_capped": False}},
                "follow_through": {"score": 60, "weight": 0.15, "weighted": 9.0, "detail": {}},
                "narrative": {"score": 50, "weight": 0.05, "weighted": 2.5, "detail": {"placeholder": True}},
            },
            "data_degraded": False, "missing_subscores": [],
            "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
            "scoring_version": "v1", "weights_mtime": 0, "weights_hash": "abc123",
            "short_circuited": False, "failed_gate": None, "failed_reason": None,
        },
    }


def _close_alert():
    return {
        "type": "runner_close",
        "paper_position_id": 1,
        "runner_score_id": 42,
        "token_mint": "MINT1",
        "symbol": "$TEST",
        "verdict": "strong_candidate",
        "runner_score": 72.0,
        "entry_price_sol": 0.0006,
        "entry_price_usd": 0.096,
        "exit_price_sol": 0.0008,
        "milestones": {"5m": 8.1, "30m": 22.4, "1h": 45.2, "4h": 31.0, "24h": 33.3},
        "max_favorable_pct": 52.1,
        "max_adverse_pct": -3.2,
    }


@pytest.mark.asyncio
async def test_routes_entry_to_formatter():
    alert_bus = asyncio.Queue()
    alerter = TelegramAlerter(alert_bus, "fake_token", "12345")

    with patch("runner.alerts.telegram.Bot") as MockBot:
        mock_bot = AsyncMock()
        MockBot.return_value = mock_bot

        await alert_bus.put(_entry_alert())
        await alerter._process_one(await alert_bus.get())

        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args
        assert "STRONG CANDIDATE" in call_args.kwargs.get("text", call_args.args[1] if len(call_args.args) > 1 else "")


@pytest.mark.asyncio
async def test_routes_close_to_formatter():
    alert_bus = asyncio.Queue()
    alerter = TelegramAlerter(alert_bus, "fake_token", "12345")

    with patch("runner.alerts.telegram.Bot") as MockBot:
        mock_bot = AsyncMock()
        MockBot.return_value = mock_bot

        await alerter._process_one(_close_alert())

        mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_drains_silently_when_no_token():
    alert_bus = asyncio.Queue()
    alerter = TelegramAlerter(alert_bus, "", "12345")

    await alert_bus.put(_entry_alert())
    await alerter._process_one(await alert_bus.get())
    # Should not crash, no send attempted


@pytest.mark.asyncio
async def test_handles_send_failure():
    alert_bus = asyncio.Queue()
    alerter = TelegramAlerter(alert_bus, "fake_token", "12345")

    with patch("runner.alerts.telegram.Bot") as MockBot:
        mock_bot = AsyncMock()
        mock_bot.send_message.side_effect = Exception("Network error")
        MockBot.return_value = mock_bot

        await alerter._process_one(_entry_alert())
        # Should not crash
```

- [ ] **Step 2: Create TelegramAlerter**

Create `meme-trading/runner/alerts/telegram.py`:

```python
"""TelegramAlerter — sends formatted HTML alerts to Telegram."""
import asyncio

from runner.alerts.formatting import format_close_alert, format_entry_alert
from runner.utils.logging import get_logger

logger = get_logger("runner.alerts.telegram")

try:
    from telegram import Bot
except ImportError:
    Bot = None  # type: ignore[assignment, misc]


class TelegramAlerter:
    """Consumes alert dicts from alert_bus, formats and sends to Telegram."""

    def __init__(
        self,
        alert_bus: asyncio.Queue,
        bot_token: str,
        chat_id: str,
    ):
        self.alert_bus = alert_bus
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    async def run(self) -> None:
        if not self._enabled:
            logger.info("telegram_disabled", reason="bot_token or chat_id not set")
        else:
            logger.info("telegram_alerter_start")

        while True:
            alert = await self.alert_bus.get()
            await self._process_one(alert)

    async def _process_one(self, alert: dict) -> None:
        alert_type = alert.get("type", "")

        if alert_type == "runner_entry":
            html = format_entry_alert(alert)
        elif alert_type == "runner_close":
            html = format_close_alert(alert)
        else:
            logger.debug("unknown_alert_type", alert_type=alert_type)
            return

        if not self._enabled:
            return

        try:
            bot = Bot(token=self.bot_token)
            await bot.send_message(
                chat_id=self.chat_id,
                text=html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram_send_failed", error=str(e), alert_type=alert_type)
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest runner/tests/unit/test_telegram_alerter.py -v`

Expected: All 4 tests pass.

- [ ] **Step 4: Commit**

```bash
git add runner/alerts/telegram.py runner/tests/unit/test_telegram_alerter.py
git commit -m "runner: TelegramAlerter with HTML entry and close alerts"
```

---

### Task 7: Wire into main.py

**Files:**
- Modify: `meme-trading/runner/main.py`

- [ ] **Step 1: Replace `_drain_scored` with executor + snapshotter + alerter**

In `meme-trading/runner/main.py`:

Add imports:
```python
from runner.executor.paper import PaperExecutor
from runner.executor.snapshotter import MilestoneSnapshotter
from runner.alerts.telegram import TelegramAlerter
```

After the `scoring_engine` setup, add:
```python
    alert_bus: asyncio.Queue = asyncio.Queue()

    paper_executor = PaperExecutor(
        scored_bus=scored_bus,
        alert_bus=alert_bus,
        weights=weights,
        price_fetcher=price_fetcher,
        db=db,
        enable_executor=settings.enable_executor,
    )

    snapshotter = MilestoneSnapshotter(
        alert_bus=alert_bus,
        price_fetcher=price_fetcher,
        db=db,
        check_interval_sec=float(weights.get("executor.check_interval_sec", 30)),
        error_closure_hours=float(weights.get("executor.error_closure_hours", 36)),
    )

    telegram = TelegramAlerter(
        alert_bus=alert_bus,
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
```

In `asyncio.gather(...)`, replace:
```python
            _supervise(lambda: _drain_scored(scored_bus, logger), "drain_scored", logger),
```
with:
```python
            _supervise(paper_executor.run, "paper_executor", logger),
            _supervise(snapshotter.run, "milestone_snapshotter", logger),
            _supervise(telegram.run, "telegram_alerter", logger),
```

Update the task names in the `zip(...)`:
```python
        for name, result in zip(
            ["monitor", "detector", "enricher", "filter_pipeline", "scoring_engine",
             "paper_executor", "milestone_snapshotter", "telegram_alerter"],
            results,
        ):
```

Delete the `_drain_scored` function.

Remove the unused `ScoredCandidate` import if it's no longer needed (check — it may still be used elsewhere).

- [ ] **Step 2: Verify clean import**

Run: `python -c "from runner.main import _main; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -15`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add runner/main.py
git commit -m "runner: wire PaperExecutor + MilestoneSnapshotter + TelegramAlerter into main.py"
```

---

### Task 8: Integration test

**Files:**
- Create: `meme-trading/runner/tests/integration/test_scoring_to_executor.py`

- [ ] **Step 1: Write integration test**

Create `meme-trading/runner/tests/integration/test_scoring_to_executor.py`:

```python
"""Integration: ScoringEngine → PaperExecutor → verify paper_positions + alert_bus."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.executor.paper import PaperExecutor
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.engine import ScoringEngine
from runner.scoring.models import DIMENSION_KEYS


def _weights_file(tmp_path: Path) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring": {
            "rug_insider_rug_weight": 0.70, "insider_cap_threshold": 25,
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
        "position_sizing": {"strong_candidate_sol": 0.25, "probable_runner_sol": 0.375},
        "executor": {"check_interval_sec": 30, "error_closure_hours": 36},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _tier_cache_stub():
    class _Stub(WalletTierCache):
        def __init__(self):
            self._map = {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    return _Stub()


def _enriched():
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="MINT1", wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=7,
    )
    return EnrichedToken(
        token_mint="MINT1", cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
        price_sol=0.0006, symbol="$TEST", cluster_signal_id=7,
    )


def _all_pass_results():
    return [
        FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]


@pytest.mark.asyncio
async def test_scoring_to_executor_end_to_end(tmp_path):
    """Score a candidate, persist, executor opens paper position, emits alert."""
    db = Database(tmp_path / "r.db")
    await db.connect()

    weights = WeightsLoader(_weights_file(tmp_path))
    filtered_bus = asyncio.Queue()
    scored_bus = asyncio.Queue()
    alert_bus = asyncio.Queue()

    scoring = ScoringEngine(
        filtered_bus=filtered_bus, scored_bus=scored_bus,
        weights=weights, tier_cache=_tier_cache_stub(), db=db,
    )

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {"price_sol": 0.0006, "price_usd": 0.096}

    executor = PaperExecutor(
        scored_bus=scored_bus, alert_bus=alert_bus,
        weights=weights, price_fetcher=price_fetcher, db=db,
    )

    # Push a candidate through scoring
    fc = FilteredCandidate(
        enriched=_enriched(), filter_results=_all_pass_results(),
        gate_passed=True, hard_fail_reason=None,
    )
    scored = scoring.score(fc)
    db_id = await scoring._persist(scored)
    from dataclasses import replace
    scored = replace(scored, runner_score_db_id=db_id)

    # Feed to executor
    await executor._process_one(scored)

    # Verify paper_positions row
    async with db.conn.execute("SELECT * FROM paper_positions WHERE token_mint='MINT1'") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[3] == db_id  # runner_score_id

    # Verify alert emitted
    alert = alert_bus.get_nowait()
    assert alert["type"] == "runner_entry"
    assert alert["runner_score_id"] == db_id
    assert alert["verdict"] in ("strong_candidate", "probable_runner")
    assert "cluster_summary" in alert
    assert "explanation" in alert

    await db.close()
```

- [ ] **Step 2: Run test**

Run: `python -m pytest runner/tests/integration/test_scoring_to_executor.py -v`

Expected: PASS.

- [ ] **Step 3: Run full suite and count**

Run: `python -m pytest runner/tests/ -v 2>&1 | tail -5`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add runner/tests/integration/test_scoring_to_executor.py
git commit -m "runner: scoring→executor integration test with paper position verification"
```

---

### Task 9: Final push + verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest runner/tests/ -v 2>&1 | tail -5`

Expected: All tests pass (~190+).

- [ ] **Step 2: Verify clean import**

Run: `python -c "from runner.executor.paper import PaperExecutor; from runner.executor.snapshotter import MilestoneSnapshotter; from runner.alerts.telegram import TelegramAlerter; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Push all commits**

```bash
git push
```

---

## Summary

| Task | What it does | New tests |
|------|-------------|-----------|
| 1 | Thread `runner_score_db_id` into ScoredCandidate | 0 (existing pass) |
| 2 | Schema + weights.yaml | 0 (schema bootstraps) |
| 3 | Alert formatting helpers | ~18 |
| 4 | PaperExecutor | 9 |
| 5 | MilestoneSnapshotter | 9 |
| 6 | TelegramAlerter | 4 |
| 7 | Wire into main.py | 0 |
| 8 | Integration test | 1 |
| 9 | Final push | 0 |

**Total: 9 tasks, ~41 new tests, ~9 commits**
