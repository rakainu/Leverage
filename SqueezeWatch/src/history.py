"""Snapshot persistence + append-only daily history."""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _paths(config: dict) -> dict:
    root = _root()
    return {
        "snapshots": root / config["paths"]["snapshots_dir"],
        "history_csv": root / config["paths"]["history_csv"],
    }


def write_snapshot(
    scored: list,
    date_str: str,
    errors: list,
    config_hash: str,
    config: dict,
) -> Path:
    paths = _paths(config)
    paths["snapshots"].mkdir(parents=True, exist_ok=True)
    out = paths["snapshots"] / f"{date_str}.json"
    payload = {
        "schema_version": 1,
        "run_timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "date": date_str,
        "universe_size": len(scored),
        "config_hash": config_hash,
        "errors": errors,
        "symbols": scored,
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("Snapshot written: %s", out)
    return out


def load_snapshot(date_str: str, config: dict) -> Optional[dict]:
    paths = _paths(config)
    p = paths["snapshots"] / f"{date_str}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_previous_snapshot(today_str: str, config: dict) -> Optional[dict]:
    """Most recent snapshot strictly before today_str. None if first run."""
    paths = _paths(config)
    paths["snapshots"].mkdir(parents=True, exist_ok=True)
    candidates = sorted(paths["snapshots"].glob("*.json"), reverse=True)
    for f in candidates:
        # filenames are ISO dates — lexicographic order matches chronological
        if f.stem >= today_str:
            continue
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not load snapshot %s: %s", f, exc)
    return None


HISTORY_HEADER = [
    "date", "symbol", "rank",
    "squeeze_score", "squeeze_score_100",
    "composite_raw", "bias_multiplier",
    "flatness_score", "funding_score", "oi_growth_score",
    "non_pumped_score", "liquidity_score",
    "price_last", "return_7d", "return_30d",
    "funding_now", "funding_avg_14d",
    "oi_growth_7d", "oi_growth_14d",
    "quote_volume_24h", "config_hash",
]


def append_history(scored: list, date_str: str, config_hash: str, config: dict) -> Path:
    paths = _paths(config)
    csv_path = paths["history_csv"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(HISTORY_HEADER)
        for s in scored:
            cs = s.get("component_scores", {})
            w.writerow([
                date_str,
                s["symbol"],
                s.get("rank", ""),
                s["squeeze_score"],
                s["squeeze_score_100"],
                s["composite_raw"],
                s["bias_multiplier"],
                _csv_val(cs.get("flatness")),
                _csv_val(cs.get("funding")),
                _csv_val(cs.get("oi_growth")),
                _csv_val(cs.get("non_pumped")),
                _csv_val(cs.get("liquidity")),
                s["price_last"],
                s["return_7d"],
                s["return_30d"],
                s["funding_now"],
                s["funding_avg_14d"],
                _csv_val(s.get("oi_growth_7d")),
                _csv_val(s.get("oi_growth_14d")),
                s["quote_volume_24h"],
                config_hash,
            ])
    log.info("History appended: %s (%d rows)", csv_path, len(scored))
    return csv_path


def _csv_val(v):
    return "" if v is None else v
