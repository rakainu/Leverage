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
    base = {
        "scoring_version": "v1", "weights_mtime": 1744451400.0, "weights_hash": "abc123",
        "short_circuited": False, "data_degraded": False, "missing_subscores": [],
        "failed_gate": None, "failed_reason": None,
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

def test_cautions_shows_low_dimension():
    cautions = format_cautions(_explanation())
    assert any("holder" in c.lower() for c in cautions)

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
    exp["dimensions"]["holder_quality"]["score"] = 50
    cautions = format_cautions(exp)
    assert cautions == ["No major cautions"]

def _entry_alert():
    return {
        "type": "runner_entry", "paper_position_id": 1, "runner_score_id": 42,
        "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
        "symbol": "$WIFHAT", "verdict": "strong_candidate", "runner_score": 72.0,
        "amount_sol": 0.25, "entry_price_sol": 0.00042, "entry_price_usd": 0.067,
        "cluster_summary": {"wallet_count": 4, "tier_counts": {"A": 2, "B": 1, "U": 1}, "convergence_minutes": 14.0},
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

def _close_alert():
    return {
        "type": "runner_close", "paper_position_id": 1, "runner_score_id": 42,
        "token_mint": "5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1",
        "symbol": "$WIFHAT", "verdict": "strong_candidate", "runner_score": 72.0,
        "entry_price_sol": 0.00042, "entry_price_usd": 0.067, "exit_price_sol": 0.00050,
        "milestones": {"5m": 8.1, "30m": 22.4, "1h": 45.2, "4h": 31.0, "24h": 18.3},
        "max_favorable_pct": 52.1, "max_adverse_pct": -3.2,
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
    assert "  4h:" not in html  # "24h:" contains "4h:" so match with indent
    assert "24h:" in html

def test_close_alert_shows_mfe_mae():
    html = format_close_alert(_close_alert())
    assert "MFE" in html
    assert "MAE" in html

def test_mint_short():
    assert mint_short("5HpYvuTgQoG8TePPBQ4Cfbfmrd9RVBz1v5aodqLkabc1") == "5HpY...abc1"

def test_escape_html():
    assert escape_html("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"
    assert escape_html("A & B") == "A &amp; B"
