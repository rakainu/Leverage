"""Per-ticker entry switch — persistence + gating predicate.

The switch lets us block NEW entries on a ticker (Telegram /off SYM) while any
open position keeps being managed to exit. State must survive a bridge restart,
so it lives in SQLite. Unknown/never-touched symbols default to ON (enabled).
"""
import sqlite3

from apex_bridge.db import TradeLogDB
from apex_bridge.telegram_control import entries_allowed


# ---------- persistence (db.py) ----------

def test_get_switches_empty_by_default(tmp_path):
    db = TradeLogDB(tmp_path / "s.db")
    assert db.get_switches() == {}
    db.close()


def test_set_switch_persists_and_reads_back(tmp_path):
    db = TradeLogDB(tmp_path / "s.db")
    db.set_switch("HYPE", False)
    db.set_switch("SOL", True)
    assert db.get_switches() == {"HYPE": False, "SOL": True}
    db.close()


def test_set_switch_is_upsert(tmp_path):
    db = TradeLogDB(tmp_path / "s.db")
    db.set_switch("HYPE", False)
    db.set_switch("HYPE", True)          # flip it back on
    assert db.get_switches() == {"HYPE": True}
    db.close()


def test_switch_survives_reopen(tmp_path):
    path = tmp_path / "s.db"
    db = TradeLogDB(path)
    db.set_switch("ZEC", False)
    db.close()
    db2 = TradeLogDB(path)               # simulate a restart
    assert db2.get_switches() == {"ZEC": False}
    db2.close()


# ---------- gating predicate (pure) ----------

def test_entries_allowed_defaults_true_for_unknown():
    assert entries_allowed({}, "SOL") is True


def test_entries_allowed_respects_explicit_off():
    assert entries_allowed({"HYPE": False}, "HYPE") is False


def test_entries_allowed_respects_explicit_on():
    assert entries_allowed({"HYPE": True}, "HYPE") is True


def test_entries_allowed_other_symbols_unaffected():
    switches = {"HYPE": False}
    assert entries_allowed(switches, "SOL") is True
