"""SqueezeWatch CLI.

Usage:
    python -m src.main scan [--config PATH] [--top N] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import alerts, compare, history, scanner, scoring
from .binance_client import BinanceFuturesClient


ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list = None) -> int:
    # Windows consoles default to cp1252 which can't print em-dashes etc.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="squeezewatch")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="Run a full scan")
    scan_p.add_argument("--config", default=None,
                        help="Path to config.json (default: config/config.json, "
                             "fallback to config/config.example.json)")
    scan_p.add_argument("--dry-run", action="store_true",
                        help="Don't write snapshot/history/digest files")
    scan_p.add_argument("--top", type=int, default=None,
                        help="Override scanner.top_n_digest")
    scan_p.add_argument("--limit", type=int, default=None,
                        help="For testing — limit universe to first N symbols")
    scan_p.add_argument("--base-url", default=None,
                        help="Override binance.base_url (e.g. "
                             "https://testnet.binancefuture.com if prod is geo-blocked)")
    scan_p.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("squeezewatch")

    config = _resolve_config(args.config, log)
    if args.top:
        config["scanner"]["top_n_digest"] = args.top
    if args.base_url:
        config["binance"]["base_url"] = args.base_url
        log.info("Using base_url override: %s", args.base_url)

    if args.cmd == "scan":
        return run_scan(config, args, log)
    parser.print_help()
    return 1


def _resolve_config(path_arg: str, log) -> dict:
    if path_arg:
        return load_config(Path(path_arg))
    local = ROOT / "config" / "config.json"
    example = ROOT / "config" / "config.example.json"
    if local.exists():
        return load_config(local)
    log.warning("config/config.json missing — using config/config.example.json")
    return load_config(example)


def run_scan(config: dict, args, log) -> int:
    client = BinanceFuturesClient(
        base_url=config["binance"]["base_url"],
        timeout=config["binance"]["timeout_seconds"],
        max_retries=config["binance"]["max_retries"],
    )

    log.info("Fetching universe...")
    universe = scanner.fetch_universe(client, config)
    universe_size = len(universe)
    log.info("Universe: %d symbols", universe_size)

    if args.limit:
        universe = universe[: args.limit]
        log.info("Limited to %d symbols (--limit)", len(universe))

    log.info("Fetching bulk funding + tickers...")
    funding_map, ticker_map = scanner.fetch_bulk_data(client)

    floor = config["scoring"]["liquidity"]["floor_quote_volume_24h"]
    pre_filtered = [
        u for u in universe
        if ticker_map.get(u["symbol"], {}).get("quote_volume_24h", 0) >= floor
    ]
    log.info("After liquidity floor (>=$%.0f 24h): %d symbols", floor, len(pre_filtered))

    pace = config["binance"].get("pace_ms", 80) / 1000.0
    scored: list = []
    errors: list = []

    started = time.monotonic()
    for i, sym in enumerate(pre_filtered):
        features = scanner.extract_features(client, sym, funding_map, ticker_map)
        if "_error" in features:
            errors.append({"symbol": sym["symbol"], "reason": features["_error"]})
        else:
            scored.append(_score_one(features, config))
        if (i + 1) % 25 == 0:
            log.info("Scored %d/%d (%.0fs elapsed)",
                     i + 1, len(pre_filtered), time.monotonic() - started)
        time.sleep(pace)

    # Rank with deterministic tiebreakers (per docs/scoring-rules.md)
    scored.sort(key=lambda s: (
        -s["squeeze_score_100"],
        -(s.get("oi_growth_7d") or -999),
        s["symbol"],
    ))
    for i, s in enumerate(scored):
        s["rank"] = i + 1

    config_hash = "sha256:" + hashlib.sha256(
        json.dumps(config, sort_keys=True).encode()
    ).hexdigest()[:16]

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    prior_snap = history.load_previous_snapshot(date_str, config)

    diff_data = compare.diff(scored, prior_snap, config)
    triggered = compare.check_triggers(scored, prior_snap, config)

    digest = alerts.format_daily_digest(
        scored, diff_data, triggered, date_str, config,
        universe_size=universe_size, errors=errors,
    )

    if not args.dry_run:
        history.write_snapshot(scored, date_str, errors, config_hash, config)
        history.append_history(scored, date_str, config_hash, config)
        out_dir = ROOT / config["paths"]["outputs_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{date_str}.md"
        out_path.write_text(digest, encoding="utf-8")
        log.info("Digest written: %s", out_path)
    else:
        log.info("DRY RUN — no files written")

    print(digest)
    log.info("Done in %.1fs. %d scored, %d errors, %d triggered alerts.",
             time.monotonic() - started, len(scored), len(errors), len(triggered))
    return 0


def _score_one(features: dict, config: dict) -> dict:
    weights = config["scoring"]["weights"]
    fl = scoring.flatness_score(
        features["highs_14d"], features["lows_14d"], features["closes_21d"]
    )
    fu = scoring.funding_score(
        features["funding_now"],
        features["funding_avg_14d"],
        features.get("funding_recent_flip_negative", False),
    )
    oi = scoring.oi_growth_score(
        features.get("oi_now"),
        features.get("oi_7d_ago"),
        features.get("oi_14d_ago"),
    )
    npump = scoring.non_pumped_score(features["return_7d"], features["return_30d"])
    liq = scoring.liquidity_score(features["quote_volume_24h"])

    components = {
        "flatness": fl,
        "funding": fu,
        "oi_growth": oi,
        "non_pumped": npump,
        "liquidity": liq,
    }

    if liq == 0:
        composite_raw = 0.0
    else:
        composite_raw = scoring.composite(components, weights)

    majors = set(config.get("bias", {}).get("majors", []))
    bias = config.get("bias", {}).get("major_multiplier", 0.9) if features["symbol"] in majors else 1.0
    squeeze_100 = max(0.0, min(100.0, composite_raw * bias))

    oi_growth_7d = oi_growth_14d = None
    if features.get("oi_now") and features.get("oi_7d_ago"):
        oi_growth_7d = (features["oi_now"] - features["oi_7d_ago"]) / features["oi_7d_ago"]
    if features.get("oi_now") and features.get("oi_14d_ago"):
        oi_growth_14d = (features["oi_now"] - features["oi_14d_ago"]) / features["oi_14d_ago"]

    return {
        "symbol": features["symbol"],
        "base_asset": features["base_asset"],
        "age_days": features.get("age_days"),
        "onboard_date": features.get("onboard_date"),
        "price_last": features["price_last"],
        "return_7d": features["return_7d"],
        "return_30d": features["return_30d"],
        "funding_now": features["funding_now"],
        "funding_avg_14d": features["funding_avg_14d"],
        "oi_now": features.get("oi_now"),
        "oi_7d_ago": features.get("oi_7d_ago"),
        "oi_14d_ago": features.get("oi_14d_ago"),
        "oi_growth_7d": oi_growth_7d,
        "oi_growth_14d": oi_growth_14d,
        "quote_volume_24h": features["quote_volume_24h"],
        "component_scores": components,
        "composite_raw": round(composite_raw, 2),
        "bias_multiplier": bias,
        "squeeze_score_100": round(squeeze_100, 2),
        "squeeze_score": round(squeeze_100 / 10.0, 1),
    }


if __name__ == "__main__":
    sys.exit(main())
