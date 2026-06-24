"""Telegram control command parsing + authorization.

Commands (case-insensitive symbol): /off SYM, /on SYM, /close SYM, /status, /help.
Parsing is pure (no network). Unknown symbols and malformed input return a
user-facing error string rather than silently doing nothing.
"""
from apex_bridge.telegram_control import parse_command, is_authorized

KNOWN = {"SOL", "ETH", "ZEC", "HYPE"}


def test_off_parses_symbol():
    cmd = parse_command("/off HYPE", KNOWN)
    assert cmd.action == "off" and cmd.symbol == "HYPE" and cmd.error is None


def test_on_uppercases_symbol():
    cmd = parse_command("/on sol", KNOWN)
    assert cmd.action == "on" and cmd.symbol == "SOL" and cmd.error is None


def test_close_parses():
    cmd = parse_command("/close ZEC", KNOWN)
    assert cmd.action == "close" and cmd.symbol == "ZEC"


def test_status_needs_no_symbol():
    cmd = parse_command("/status", KNOWN)
    assert cmd.action == "status" and cmd.symbol is None and cmd.error is None


def test_help_recognized():
    cmd = parse_command("/help", KNOWN)
    assert cmd.action == "help"


def test_unknown_symbol_is_error():
    cmd = parse_command("/off DOGE", KNOWN)
    assert cmd.action == "off" and cmd.symbol is None and "DOGE" in cmd.error


def test_missing_symbol_is_error():
    cmd = parse_command("/off", KNOWN)
    assert cmd.error is not None


def test_non_command_returns_none():
    assert parse_command("hello there", KNOWN) is None


def test_command_strips_bot_mention():
    # Telegram delivers "/status@scalpbigbot" in groups; tolerate the @mention.
    cmd = parse_command("/status@scalpbigbot", KNOWN)
    assert cmd.action == "status"


def test_authorized_only_for_configured_chat():
    assert is_authorized(6421609315, "6421609315") is True
    assert is_authorized(999, "6421609315") is False


def test_authorization_handles_int_or_str_config():
    assert is_authorized(6421609315, 6421609315) is True
