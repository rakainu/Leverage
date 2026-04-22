"""Day-over-day diff + alert trigger evaluation."""
from __future__ import annotations

from typing import Optional


def _by_symbol(snap: Optional[dict]) -> dict:
    if not snap:
        return {}
    return {s["symbol"]: s for s in snap.get("symbols", [])}


def _yesterday_top_n(snap: Optional[dict], n: int) -> set:
    if not snap:
        return set()
    ranked = sorted(snap.get("symbols", []), key=lambda s: s.get("rank") or 9999)
    return {s["symbol"] for s in ranked[:n]}


def diff(today_scored: list, yesterday_snap: Optional[dict], config: dict) -> dict:
    """Build the diff buckets shown in the digest's "Changes" section."""
    top_n = config["scanner"].get("top_n_digest", 15)
    alerts_cfg = config.get("alerts", {})

    yday_by_sym = _by_symbol(yesterday_snap)
    today_by_sym = {s["symbol"]: s for s in today_scored}

    today_top = {s["symbol"] for s in today_scored[:top_n]}
    yday_top = _yesterday_top_n(yesterday_snap, top_n)

    new_entries_min = alerts_cfg.get("new_entries_min_score_100", 60)
    new_entries = []
    if yday_by_sym:  # day-over-day comparison only meaningful with a yesterday
        for s in today_scored[:top_n]:
            if s["symbol"] not in yday_top and s["squeeze_score_100"] >= new_entries_min:
                new_entries.append({
                    "symbol": s["symbol"],
                    "score": s["squeeze_score"],
                    "rank": s["rank"],
                })

    risers_min_delta = alerts_cfg.get("risers_min_delta_100", 20)
    risers_min_score = alerts_cfg.get("risers_min_score_100", 50)
    risers = []
    for s in today_scored:
        y = yday_by_sym.get(s["symbol"])
        if not y:
            continue
        delta_100 = s["squeeze_score_100"] - (y.get("squeeze_score_100") or 0)
        if delta_100 >= risers_min_delta and s["squeeze_score_100"] >= risers_min_score:
            risers.append({
                "symbol": s["symbol"],
                "score": s["squeeze_score"],
                "delta": round(delta_100 / 10.0, 1),
                "rank": s["rank"],
            })
    risers.sort(key=lambda r: -r["delta"])

    grad_min_ret = alerts_cfg.get("graduations_min_return_7d", 0.15)
    graduations = []
    for y in sorted(yday_by_sym.values(), key=lambda s: s.get("rank") or 9999):
        y_rank = y.get("rank")
        if not y_rank or y_rank > 20:
            break
        if y["symbol"] in today_top:
            continue
        t = today_by_sym.get(y["symbol"])
        if t and t.get("return_7d", 0) > grad_min_ret:
            graduations.append({
                "symbol": y["symbol"],
                "yesterday_rank": y_rank,
                "today_return_7d": round(t["return_7d"] * 100, 1),
            })

    return {
        "new_entries": new_entries,
        "risers": risers,
        "graduations": graduations,
        "has_yesterday": bool(yday_by_sym),
    }


def check_triggers(
    today_scored: list,
    yesterday_snap: Optional[dict],
    config: dict,
) -> list:
    """Evaluate the four Phase 1 alert conditions per symbol."""
    triggered = []
    yday_by_sym = _by_symbol(yesterday_snap)
    top_n = config["scanner"].get("top_n_digest", 15)
    today_top = {s["symbol"] for s in today_scored[:top_n]}
    yday_top = _yesterday_top_n(yesterday_snap, top_n)

    fund_more_negative_eps = config.get("alerts", {}).get(
        "combo_funding_drop_threshold", 0.00005
    )
    combo_oi_min = config.get("alerts", {}).get("combo_oi_growth_7d_min", 0.05)
    combo_contained_max = config.get("alerts", {}).get("combo_contained_return_7d_max", 0.10)

    for s in today_scored:
        sym = s["symbol"]
        fired = []
        y = yday_by_sym.get(sym)

        # 1) New top-15 entry (only meaningful when we have a yesterday)
        if yday_by_sym and sym in today_top and sym not in yday_top:
            fired.append("new_top_15")

        # 2) Score crosses 8.0+
        if s["squeeze_score"] >= 8.0:
            y_score = (y.get("squeeze_score") if y else None) or 0
            if not y or y_score < 8.0:
                fired.append("score_crossed_8")

        # 3) Score jumps 2.0+ day-over-day
        if y is not None:
            delta = s["squeeze_score"] - (y.get("squeeze_score") or 0)
            if delta >= 2.0:
                fired.append("score_jump_2")

        # 4) Combo: funding more negative + OI rising + price still contained
        if y is not None:
            y_fund = y.get("funding_avg_14d") or 0
            fund_more_neg = s["funding_avg_14d"] < (y_fund - fund_more_negative_eps)
            oi7 = s.get("oi_growth_7d")
            oi_rising = oi7 is not None and oi7 > combo_oi_min
            contained = abs(s.get("return_7d", 0)) < combo_contained_max
            if fund_more_neg and oi_rising and contained:
                fired.append("combo_coil_tightening")

        if fired:
            triggered.append({
                "symbol": sym,
                "rank": s["rank"],
                "score": s["squeeze_score"],
                "triggers": fired,
            })

    return triggered
