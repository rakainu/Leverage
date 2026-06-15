"""Position sizing + profit-withdrawal policy — pure, side-effect-free math.

Separated from the bridge runtime so it is unit-testable and *identical for paper
and live trading*: feed it the account's current equity (paper book value, or the
real exchange collateral) and it returns the margin to post / the amount to skim.

Compounding sizing
------------------
Each position's margin scales linearly with account equity relative to a fixed
base, bounded above and floored at zero:

    margin = clamp(base_margin * equity / base_equity, 0, base_margin * cap_mult)

- Grows the position as the account grows (compounding).
- Scales DOWN on drawdown (equity < base_equity) — correct de-risking, not a bug.
- cap_mult bounds notional so a runaway account can't outgrow book/venue liquidity.

Withdrawal
----------
Skim only REALIZED equity above a target; never touch unrealized (open) P&L:

    surplus = max(0, realized_equity - base_equity * target_mult)

Holding the account at the target keeps sizing pinned at the cap, so the book
trades full-size while everything above target is paid out.
"""
from __future__ import annotations


def compound_margin(base_margin: float, equity: float, base_equity: float,
                    cap_mult: float) -> float:
    """Margin to post for one position under compounding, in account currency.

    base_margin : margin posted when equity == base_equity (e.g. $500).
    equity      : current account equity (book value incl. unrealized, or live collateral).
    base_equity : equity at which a position trades exactly base_margin.
    cap_mult    : upper-bound multiple; margin never exceeds base_margin * cap_mult.
    """
    if base_equity <= 0 or base_margin <= 0:
        return max(0.0, base_margin)
    scaled = base_margin * (equity / base_equity)
    if scaled <= 0.0:
        return 0.0
    return min(scaled, base_margin * cap_mult)


def withdrawal_surplus(realized_equity: float, base_equity: float,
                       target_mult: float) -> float:
    """Amount to withdraw: REALIZED equity above target (>= 0). Ignores unrealized.

    realized_equity : initial_collateral + realized_pnl - already_withdrawn.
    target          : base_equity * target_mult (the cap-supporting account size).
    """
    if target_mult <= 0:
        return 0.0
    return max(0.0, realized_equity - base_equity * target_mult)
