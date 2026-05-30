"""Scale-out exit model — the validated Pro V3 SOL config (see
scripts/scalping/analysis/pro_v3_real/FINDINGS.md, 2026-05-29).

Levels are fixed at entry from ATR(14):
  SL  = entry -/+ sl_atr  * atr
  TPk = entry +/- tp_atr[k] * atr
A fraction ratios[k] of the ORIGINAL position is closed at TPk. Once TP1 fills,
the stop moves to breakeven (be_after_tp1) — the real loss-cutter.

This is the live (tick-sampled) twin of the bar-based replay in
pro_v3_real/replay.py. At tick granularity the mark is a single price, so there
is no intrabar SL/TP ambiguity: TPs are checked first (a favorable gap can clear
several at once), then the stop. The level arithmetic is identical to the backtest,
so paper/live behavior is the same strategy that was validated — only the price
sampling (5s mark vs bar wick) differs, which is the honest real-execution model.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScaleOutParams:
    sl_atr: float = 3.5
    tp_atr: tuple[float, float, float] = (1.0, 2.0, 3.0)
    ratios: tuple[float, float, float] = (0.34, 0.33, 0.33)
    be_after_tp1: bool = True


@dataclass
class ScaleOutState:
    """Mutable exit state for one open position. Seeded by `init_levels`."""
    entry_price: float
    side: str                       # "long" | "short"
    sl_price: float
    tp_px: tuple[float, float, float]
    next_tp: int = 0                # index of the next TP not yet hit (0..3)
    remaining: float = 1.0          # fraction of ORIGINAL position still open
    be_set: bool = False
    max_tp_reached: int = 0         # diagnostics: highest TP filled (0..3)


@dataclass
class ScaleDecision:
    """What to do this tick. `closes` are fractions of the ORIGINAL position."""
    closes: list[tuple[float, str]] = field(default_factory=list)
    be_moved: bool = False
    done: bool = False


def init_levels(side: str, entry_price: float, atr: float,
                p: ScaleOutParams) -> ScaleOutState:
    long = side == "long"
    sl = entry_price - p.sl_atr * atr if long else entry_price + p.sl_atr * atr
    tp = tuple(entry_price + d * atr if long else entry_price - d * atr
               for d in p.tp_atr)
    return ScaleOutState(entry_price=entry_price, side=side, sl_price=sl, tp_px=tp)


def step(st: ScaleOutState, mark: float, p: ScaleOutParams) -> ScaleDecision:
    """Evaluate one mark tick. Mutates `st`; returns the actions to execute."""
    d = ScaleDecision()
    if st.remaining <= 1e-9:
        d.done = True
        return d
    long = st.side == "long"

    # 1) Take-profits — a favorable gap can clear several levels in one tick.
    while st.next_tp < 3:
        tp = st.tp_px[st.next_tp]
        hit = (mark >= tp) if long else (mark <= tp)
        if not hit:
            break
        ratio = p.ratios[st.next_tp]
        if ratio > 0:
            d.closes.append((ratio, f"tp{st.next_tp + 1}"))
            st.remaining -= ratio
        st.max_tp_reached = st.next_tp + 1
        st.next_tp += 1
        if st.next_tp >= 1 and p.be_after_tp1 and not st.be_set:
            st.sl_price = st.entry_price
            st.be_set = True
            d.be_moved = True

    if st.remaining <= 1e-9:
        d.done = True
        return d

    # 2) Stop — at tick granularity the mark cannot be on both sides at once.
    sl_hit = (mark <= st.sl_price) if long else (mark >= st.sl_price)
    if sl_hit:
        reason = "sl_be" if (st.be_set and abs(st.sl_price - st.entry_price) < 1e-9) else "sl"
        d.closes.append((st.remaining, reason))
        st.remaining = 0.0
        d.done = True
    return d
