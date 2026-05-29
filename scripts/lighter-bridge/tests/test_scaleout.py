"""Unit tests for the scale-out exit model (validated Pro V3 SOL config)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lighter_bridge.scaleout import ScaleOutParams, init_levels, step


P = ScaleOutParams(sl_atr=3.5, tp_atr=(1.0, 2.0, 3.0), ratios=(0.34, 0.33, 0.33),
                   be_after_tp1=True)


def feed(side, entry, atr, marks, p=P):
    """Replay a sequence of marks; return (closes list, final state)."""
    st = init_levels(side, entry, atr, p)
    closes = []
    for m in marks:
        d = step(st, m, p)
        closes.extend(d.closes)
        if d.done:
            break
    return closes, st


def test_levels_long():
    st = init_levels("long", 100.0, 2.0, P)
    assert st.sl_price == 100.0 - 3.5 * 2.0      # 93.0
    assert st.tp_px == (102.0, 104.0, 106.0)


def test_levels_short():
    st = init_levels("short", 100.0, 2.0, P)
    assert st.sl_price == 100.0 + 3.5 * 2.0      # 107.0
    assert st.tp_px == (98.0, 96.0, 94.0)


def test_full_loss_before_any_tp():
    # long, price walks straight down to SL — one full-size 'sl' close
    closes, st = feed("long", 100.0, 2.0, [99, 96, 93.0])
    assert closes == [(1.0, "sl")]
    assert st.remaining == 0.0


def test_tp1_then_breakeven_scratch():
    # hit TP1 (102) -> close 0.34 + move SL to BE(100); then fall to 100 -> sl_be on remainder
    closes, st = feed("long", 100.0, 2.0, [102.0, 101.0, 100.0])
    assert closes[0] == (0.34, "tp1")
    assert st.be_set is True and st.sl_price == 100.0
    assert closes[1][1] == "sl_be"
    assert abs(closes[1][0] - 0.66) < 1e-9       # remaining after TP1
    assert st.remaining == 0.0


def test_all_three_tps():
    closes, st = feed("long", 100.0, 2.0, [102.0, 104.0, 106.0])
    reasons = [c[1] for c in closes]
    assert reasons == ["tp1", "tp2", "tp3"]
    assert abs(sum(c[0] for c in closes) - 1.0) < 1e-9
    assert st.remaining <= 1e-9


def test_gap_clears_multiple_tps_one_tick():
    # a single favorable gap to 106 should bank TP1+TP2+TP3 at once
    closes, st = feed("long", 100.0, 2.0, [106.5])
    assert [c[1] for c in closes] == ["tp1", "tp2", "tp3"]
    assert st.remaining <= 1e-9


def test_short_tp1_then_runner_to_tp3():
    closes, st = feed("short", 100.0, 2.0, [98.0, 96.0, 94.0])
    assert [c[1] for c in closes] == ["tp1", "tp2", "tp3"]
    assert st.be_set is True


def test_no_action_when_flat_in_range():
    closes, st = feed("long", 100.0, 2.0, [100.5, 101.0, 99.5])
    assert closes == []
    assert st.remaining == 1.0 and st.next_tp == 0


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
