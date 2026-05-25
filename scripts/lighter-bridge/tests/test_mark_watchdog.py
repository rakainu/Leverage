"""Standalone tests for the mark-feed watchdog / WS self-healing supervisor.

No pytest dependency on the VPS — run with the bridge venv:

    venv/Scripts/python.exe tests/test_mark_watchdog.py     # Windows
    venv/bin/python tests/test_mark_watchdog.py             # Linux/VPS

Exit code 0 = all passed. Covers the two bugs fixed 2026-05-25:
  Bug 1 — SDK listener has no reconnect (dead task → frozen mark).
  Bug 2 — old re-track was a silent no-op because track_market() early-returns
          while the dead listener is still registered. The fix must call
          stop_tracking() FIRST, then track_market().
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make `lighter_bridge` importable when run from the bridge root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lighter_bridge.main import Bridge  # noqa: E402


# ---- fakes -------------------------------------------------------------

class FakeTask:
    def __init__(self, done: bool):
        self._done = done

    def done(self) -> bool:
        return self._done


class FakeListener:
    def __init__(self, task):
        self._task = task


class FakePaper:
    """Minimal stand-in for lighter.PaperClient for watchdog tests."""

    def __init__(self, listeners=None, track_fails: int = 0):
        # listeners: dict market_id -> FakeListener (or absent = untracked)
        self._live_listeners = dict(listeners or {})
        self.calls: list[tuple[str, int]] = []
        self._track_fails_remaining = track_fails  # raise this many times first

    async def stop_tracking(self, market_id: int) -> None:
        self.calls.append(("stop_tracking", market_id))
        self._live_listeners.pop(market_id, None)

    async def track_market(self, market_id: int) -> None:
        self.calls.append(("track_market", market_id))
        if self._track_fails_remaining > 0:
            self._track_fails_remaining -= 1
            raise RuntimeError("simulated track_market failure")
        self._live_listeners[market_id] = FakeListener(FakeTask(done=False))


def make_bridge(paper) -> Bridge:
    b = Bridge.__new__(Bridge)  # bypass __init__ (no DB / config side effects)
    b.paper = paper
    return b


# ---- assertions --------------------------------------------------------

_passed = 0
_failed = 0


def check(cond: bool, label: str) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


# ---- tests -------------------------------------------------------------

def test_watchdog_action():
    print("test_watchdog_action")
    A = Bridge._watchdog_action
    reconnect_s, fatal_s = 60, 300
    # Healthy.
    check(A(5.0, False, reconnect_s, fatal_s) == "ok", "fresh mark, live task -> ok")
    check(A(59.0, False, reconnect_s, fatal_s) == "ok", "just under reconnect -> ok")
    # Reconnect triggers.
    check(A(60.0, False, reconnect_s, fatal_s) == "reconnect", "stale >= reconnect_s -> reconnect")
    check(A(5.0, True, reconnect_s, fatal_s) == "reconnect", "dead task, fresh age -> reconnect")
    check(A(None, False, reconnect_s, fatal_s) == "reconnect", "age None -> reconnect")
    # Fatal takes priority over everything.
    check(A(300.0, False, reconnect_s, fatal_s) == "fatal", "stale >= fatal_s -> fatal")
    check(A(305.0, True, reconnect_s, fatal_s) == "fatal", "dead AND past fatal -> fatal (not reconnect)")


def test_listener_dead():
    print("test_listener_dead")
    b_untracked = make_bridge(FakePaper(listeners={}))
    check(b_untracked._listener_dead(90) is True, "untracked market -> dead")

    b_done = make_bridge(FakePaper(listeners={90: FakeListener(FakeTask(done=True))}))
    check(b_done._listener_dead(90) is True, "finished task -> dead")

    b_none = make_bridge(FakePaper(listeners={90: FakeListener(None)}))
    check(b_none._listener_dead(90) is True, "no task -> dead")

    b_live = make_bridge(FakePaper(listeners={90: FakeListener(FakeTask(done=False))}))
    check(b_live._listener_dead(90) is False, "running task -> alive")


def test_reconnect_calls_stop_then_track():
    print("test_reconnect_calls_stop_then_track (Bug 2 regression)")
    paper = FakePaper(listeners={90: FakeListener(FakeTask(done=True))})
    b = make_bridge(paper)
    ok = asyncio.run(b._reconnect_market("ZEC", 90))
    check(ok is True, "reconnect returns True on success")
    check(paper.calls == [("stop_tracking", 90), ("track_market", 90)],
          "calls stop_tracking THEN track_market (not track-only no-op)")
    check(not b._listener_dead(90), "listener is alive again after reconnect")


def test_reconnect_retries_then_succeeds():
    print("test_reconnect_retries_then_succeeds")
    paper = FakePaper(listeners={90: FakeListener(FakeTask(done=True))}, track_fails=1)
    b = make_bridge(paper)
    ok = asyncio.run(b._reconnect_market("ZEC", 90, retries=2))
    check(ok is True, "succeeds on 2nd attempt after one failure")
    track_calls = [c for c in paper.calls if c[0] == "track_market"]
    check(len(track_calls) == 2, "track_market attempted twice")


def test_reconnect_gives_up_after_retries():
    print("test_reconnect_gives_up_after_retries")
    paper = FakePaper(listeners={90: FakeListener(FakeTask(done=True))}, track_fails=99)
    b = make_bridge(paper)
    ok = asyncio.run(b._reconnect_market("ZEC", 90, retries=2))
    check(ok is False, "returns False when all attempts fail")
    track_calls = [c for c in paper.calls if c[0] == "track_market"]
    check(len(track_calls) == 2, "stops after `retries` attempts")


def main():
    for t in (
        test_watchdog_action,
        test_listener_dead,
        test_reconnect_calls_stop_then_track,
        test_reconnect_retries_then_succeeds,
        test_reconnect_gives_up_after_retries,
    ):
        t()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
