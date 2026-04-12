"""TelegramAlerter unit tests — mock Telegram bot."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from runner.alerts.telegram import TelegramAlerter


def _entry_alert():
    return {
        "type": "runner_entry", "paper_position_id": 1, "runner_score_id": 42,
        "token_mint": "MINT1", "symbol": "$TEST", "verdict": "strong_candidate",
        "runner_score": 72.0, "amount_sol": 0.25, "entry_price_sol": 0.0006,
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
        "type": "runner_close", "paper_position_id": 1, "runner_score_id": 42,
        "token_mint": "MINT1", "symbol": "$TEST", "verdict": "strong_candidate",
        "runner_score": 72.0, "entry_price_sol": 0.0006, "entry_price_usd": 0.096,
        "exit_price_sol": 0.0008,
        "milestones": {"5m": 8.1, "30m": 22.4, "1h": 45.2, "4h": 31.0, "24h": 33.3},
        "max_favorable_pct": 52.1, "max_adverse_pct": -3.2,
    }


@pytest.mark.asyncio
async def test_routes_entry_to_formatter():
    alerter = TelegramAlerter(asyncio.Queue(), "fake_token", "12345")
    with patch("runner.alerts.telegram.Bot") as MockBot:
        mock_bot = AsyncMock()
        MockBot.return_value = mock_bot
        await alerter._process_one(_entry_alert())
        mock_bot.send_message.assert_called_once()
        args = mock_bot.send_message.call_args
        text = args.kwargs.get("text", "")
        assert "STRONG CANDIDATE" in text


@pytest.mark.asyncio
async def test_routes_close_to_formatter():
    alerter = TelegramAlerter(asyncio.Queue(), "fake_token", "12345")
    with patch("runner.alerts.telegram.Bot") as MockBot:
        mock_bot = AsyncMock()
        MockBot.return_value = mock_bot
        await alerter._process_one(_close_alert())
        mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_drains_silently_when_no_token():
    alerter = TelegramAlerter(asyncio.Queue(), "", "12345")
    await alerter._process_one(_entry_alert())
    # Should not crash, no send attempted


@pytest.mark.asyncio
async def test_handles_send_failure():
    alerter = TelegramAlerter(asyncio.Queue(), "fake_token", "12345")
    with patch("runner.alerts.telegram.Bot") as MockBot:
        mock_bot = AsyncMock()
        mock_bot.send_message.side_effect = Exception("Network error")
        MockBot.return_value = mock_bot
        await alerter._process_one(_entry_alert())
        # Should not crash
