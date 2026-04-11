"""Logging emits JSON lines to stdout."""
import json
import logging

from runner.utils.logging import configure_logging, get_logger


def test_logger_emits_json(capsys):
    configure_logging(level="INFO")
    log = get_logger("test.module")

    log.info("hello_event", token="So111", amount=0.25)

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) >= 1

    payload = json.loads(lines[-1])
    assert payload["event"] == "hello_event"
    assert payload["token"] == "So111"
    assert payload["amount"] == 0.25
    assert payload["level"] == "info"
    assert payload["logger"] == "test.module"


def test_logger_respects_level(capsys):
    configure_logging(level="WARNING")
    log = get_logger("test.quiet")

    log.debug("debug_event")
    log.warning("warn_event")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    events = [p.get("event") for p in payloads]

    assert "debug_event" not in events
    assert "warn_event" in events


def test_configure_is_idempotent(capsys):
    configure_logging(level="INFO")
    configure_logging(level="INFO")
    log = get_logger("test.idem")
    log.info("only_once")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "only_once" in line]
    assert len(lines) == 1
