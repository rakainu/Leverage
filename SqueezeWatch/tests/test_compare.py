"""Unit tests for src/compare.py — diff buckets + alert triggers."""

from src import compare


CONFIG = {
    "scanner": {"top_n_digest": 15},
    "alerts": {
        "new_entries_min_score_100": 60,
        "risers_min_score_100": 50,
        "risers_min_delta_100": 20,
        "graduations_min_return_7d": 0.15,
        "combo_funding_drop_threshold": 0.00005,
        "combo_oi_growth_7d_min": 0.05,
        "combo_contained_return_7d_max": 0.10,
    },
}


def _make_scored_row(symbol, rank, score_100, **kwargs):
    base = {
        "symbol": symbol,
        "rank": rank,
        "squeeze_score_100": score_100,
        "squeeze_score": round(score_100 / 10.0, 1),
        "funding_avg_14d": 0.0,
        "return_7d": 0.0,
        "return_30d": 0.0,
        "oi_growth_7d": 0.0,
    }
    base.update(kwargs)
    return base


# ---------- diff() ----------

def test_diff_first_run_returns_empty_buckets():
    today = [_make_scored_row(f"S{i}USDT", i, 90 - i) for i in range(1, 16)]
    d = compare.diff(today, None, CONFIG)
    assert d["new_entries"] == []
    assert d["risers"] == []
    assert d["graduations"] == []
    assert d["has_yesterday"] is False


def test_diff_new_entries_above_min_score():
    today = [_make_scored_row("AUSDT", 1, 80), _make_scored_row("BUSDT", 2, 65)]
    yesterday = {"symbols": [_make_scored_row("ZUSDT", 1, 70)]}
    d = compare.diff(today, yesterday, CONFIG)
    new_syms = {e["symbol"] for e in d["new_entries"]}
    assert "AUSDT" in new_syms and "BUSDT" in new_syms


def test_diff_new_entries_skips_below_min_score():
    today = [_make_scored_row("AUSDT", 1, 50)]  # below 60 floor
    yesterday = {"symbols": [_make_scored_row("ZUSDT", 1, 70)]}
    d = compare.diff(today, yesterday, CONFIG)
    assert d["new_entries"] == []


def test_diff_risers_meets_delta_and_score_thresholds():
    today = [_make_scored_row("AUSDT", 1, 70)]
    yesterday = {"symbols": [_make_scored_row("AUSDT", 5, 45)]}
    d = compare.diff(today, yesterday, CONFIG)
    assert len(d["risers"]) == 1
    assert d["risers"][0]["symbol"] == "AUSDT"
    assert d["risers"][0]["delta"] == 2.5  # (70-45)/10


def test_diff_risers_skip_under_delta_threshold():
    today = [_make_scored_row("AUSDT", 1, 65)]
    yesterday = {"symbols": [_make_scored_row("AUSDT", 5, 50)]}  # delta 15
    d = compare.diff(today, yesterday, CONFIG)
    assert d["risers"] == []


def test_diff_graduations_detects_runners():
    # Was top-20 yesterday, dropped out today, and ran 20% in last 7d
    today = [_make_scored_row(f"S{i}USDT", i, 90 - i) for i in range(1, 16)]
    today.append(_make_scored_row("RUNNERUSDT", 50, 30, return_7d=0.20))
    yesterday = {"symbols": [_make_scored_row("RUNNERUSDT", 5, 75)]}
    d = compare.diff(today, yesterday, CONFIG)
    assert len(d["graduations"]) == 1
    assert d["graduations"][0]["symbol"] == "RUNNERUSDT"


# ---------- check_triggers() ----------

def test_trigger_score_crosses_8_first_run():
    today = [_make_scored_row("AUSDT", 1, 85)]
    triggered = compare.check_triggers(today, None, CONFIG)
    fired = {t["symbol"]: t["triggers"] for t in triggered}
    assert "score_crossed_8" in fired["AUSDT"]


def test_trigger_score_crosses_8_only_on_actual_cross():
    today = [_make_scored_row("AUSDT", 1, 85)]
    yesterday = {"symbols": [_make_scored_row("AUSDT", 1, 82)]}  # already 8.2
    triggered = compare.check_triggers(today, yesterday, CONFIG)
    triggers = next((t["triggers"] for t in triggered if t["symbol"] == "AUSDT"), [])
    assert "score_crossed_8" not in triggers


def test_trigger_score_jump_2():
    today = [_make_scored_row("AUSDT", 1, 75)]
    yesterday = {"symbols": [_make_scored_row("AUSDT", 5, 50)]}  # +2.5
    triggered = compare.check_triggers(today, yesterday, CONFIG)
    triggers = next(t["triggers"] for t in triggered if t["symbol"] == "AUSDT")
    assert "score_jump_2" in triggers


def test_trigger_new_top_15():
    today = [_make_scored_row(f"S{i}USDT", i, 90 - i) for i in range(1, 16)]
    yesterday = {"symbols": [_make_scored_row("OLDUSDT", 1, 90)]}
    triggered = compare.check_triggers(today, yesterday, CONFIG)
    new_top = [t for t in triggered if "new_top_15" in t["triggers"]]
    assert len(new_top) >= 14  # all the S1..S15 are new vs yesterday's OLDUSDT


def test_trigger_new_top_15_not_fired_for_rank_shuffle_within_top_n():
    # A symbol already in yesterday's top-N must NOT fire new_top_15, even if
    # it jumps ranks wildly inside the top-N. The 2026-04-23 eval initially
    # suspected this was broken for RUNEUSDT — the real cause was a stale
    # local snapshot; the logic was correct. This pins the behavior.
    today = [_make_scored_row(f"S{i}USDT", i, 90 - i) for i in range(1, 16)]
    # Yesterday's top 15 is the same symbol set in reverse: S15 at rank 1, S1 at rank 15.
    yesterday_rows = [_make_scored_row(f"S{i}USDT", 16 - i, 90 - (16 - i)) for i in range(1, 16)]
    yesterday = {"symbols": yesterday_rows}
    triggered = compare.check_triggers(today, yesterday, CONFIG)
    offenders = [t["symbol"] for t in triggered if "new_top_15" in t["triggers"]]
    assert offenders == [], f"symbols in yesterday's top 15 wrongly flagged: {offenders}"


def test_trigger_combo_coil_tightening():
    today = [_make_scored_row(
        "AUSDT", 1, 60,
        funding_avg_14d=-0.0002,
        return_7d=0.02,
        oi_growth_7d=0.10,
    )]
    yesterday = {"symbols": [_make_scored_row(
        "AUSDT", 1, 55,
        funding_avg_14d=-0.0001,  # less negative yesterday => more negative today
    )]}
    triggered = compare.check_triggers(today, yesterday, CONFIG)
    triggers = next(t["triggers"] for t in triggered if t["symbol"] == "AUSDT")
    assert "combo_coil_tightening" in triggers


def test_trigger_combo_skipped_when_price_running():
    today = [_make_scored_row(
        "AUSDT", 1, 60,
        funding_avg_14d=-0.0002,
        return_7d=0.20,  # already running
        oi_growth_7d=0.10,
    )]
    yesterday = {"symbols": [_make_scored_row("AUSDT", 1, 55, funding_avg_14d=-0.0001)]}
    triggered = compare.check_triggers(today, yesterday, CONFIG)
    found = next((t for t in triggered if t["symbol"] == "AUSDT"), None)
    if found:
        assert "combo_coil_tightening" not in found["triggers"]
