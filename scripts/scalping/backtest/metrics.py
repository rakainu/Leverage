"""Risk-adjusted metrics + the go/no-go guardrail gate.

The base engine.metrics() returns PF / WR / avgR / net% / maxDD / streak / liq.
That's enough to rank, not enough to *judge* an aggressive, drawdown-guarded,
(eventually) compounding leverage book. This module layers on:

  - CAGR        : compound annual growth (equity-multiple based when compounding)
  - Sharpe      : per-trade return mean/std, annualized by realized trade cadence
  - Sortino     : same but downside-deviation only (ignores upside vol)
  - Calmar/MAR  : annual return / max drawdown  (the headline number for profile #2)
  - Ulcer index : RMS of the drawdown path — punishes long, deep underwater stretches
  - recovery    : net profit / max-drawdown dollars

`passes_guardrails()` encodes Rich's chosen risk profile (#2: high return WITH
hard drawdown + zero-liquidation guardrails) as a single gate the optimizer and
reports both call, so "is this strategy acceptable?" has exactly one definition.

Annualization note: crypto trades 24/7, so we annualize on a 365-day year using
the strategy's *own* realized trade frequency (trades/day * 365), not a fixed
252. Assumptions are explicit so the numbers are comparable across strategies.
"""
from __future__ import annotations

import numpy as np

from engine import metrics as base_metrics  # base per-trade metrics (same dir on path)

CAL_DAYS = 365.0  # crypto: 24/7


def _equity_curve(trades, starting_equity):
    """Equity after each trade. engine.Trade already carries equity_after."""
    return np.array([starting_equity] + [t.equity_after for t in trades], dtype=float)


def _per_trade_returns(trades):
    """Each trade's PnL as a fraction of equity BEFORE that trade was opened."""
    rets = []
    for t in trades:
        eq_before = t.equity_after - t.pnl_usd
        rets.append(t.pnl_usd / eq_before if eq_before > 0 else 0.0)
    return np.array(rets, dtype=float)


def _span_days(trades) -> float:
    start, end = trades[0].entry_time, trades[-1].exit_time
    return max((end - start).total_seconds() / 86400.0, 1e-9)


def extended_metrics(trades, starting_equity, compounding: bool = True, rf: float = 0.0) -> dict:
    """Base metrics enriched with risk-adjusted + survival numbers."""
    m = base_metrics(trades, starting_equity)
    if not trades:
        m.update(cagr=0.0, sharpe=0.0, sortino=0.0, calmar=0.0, mar=0.0,
                 ulcer=0.0, recovery_factor=0.0, trades_per_day=0.0, span_days=0.0)
        return m

    eq = _equity_curve(trades, starting_equity)
    rets = _per_trade_returns(trades)
    days = _span_days(trades)
    tpd = len(trades) / days
    ann = CAL_DAYS * tpd                      # trades per year -> annualization factor
    final = eq[-1]

    if compounding and starting_equity > 0 and final > 0:
        cagr = ((final / starting_equity) ** (CAL_DAYS / days) - 1.0) * 100.0
    else:  # fixed sizing: linear annualization of realized net %
        cagr = m["net_pct"] * (CAL_DAYS / days)

    mu = rets.mean()
    sd = rets.std(ddof=1) if len(rets) > 1 else 0.0
    downside = rets[rets < 0]
    dsd = downside.std(ddof=1) if len(downside) > 1 else 0.0
    sharpe = (mu - rf) / sd * np.sqrt(ann) if sd > 0 else 0.0
    sortino = (mu - rf) / dsd * np.sqrt(ann) if dsd > 0 else 0.0

    maxdd = m["max_dd_pct"]
    calmar = cagr / maxdd if maxdd > 0 else float("inf")

    peak = np.maximum.accumulate(eq)
    dd_path = (peak - eq) / peak * 100.0
    ulcer = float(np.sqrt(np.mean(dd_path ** 2)))
    recovery = (m["net_pnl"] / (maxdd / 100.0 * starting_equity)) if maxdd > 0 else float("inf")

    m.update(cagr=cagr, sharpe=sharpe, sortino=sortino, calmar=calmar, mar=calmar,
             ulcer=ulcer, recovery_factor=recovery, trades_per_day=tpd, span_days=days)
    return m


# --- Risk profile #2: high return WITH drawdown + zero-liquidation guardrails ---
# These are the *default* gate. Tighten/loosen per hunt; the optimizer reads the
# same thresholds so search and reporting never disagree on "acceptable".
GUARDRAILS = dict(
    max_dd_pct=25.0,    # reject books that draw down more than this
    min_pf=1.3,         # profit factor floor
    min_trades=30,      # statistical-significance floor
    min_sharpe=1.0,     # risk-adjusted floor
    allow_liq=False,    # ANY modeled liquidation breach is an automatic fail
)


def passes_guardrails(m: dict, **overrides) -> tuple[bool, list[str]]:
    """Return (ok, reasons_failed). Reasons empty => strategy clears the gate."""
    g = {**GUARDRAILS, **overrides}
    reasons = []
    if m["n"] < g["min_trades"]:
        reasons.append(f"trades {m['n']}<{g['min_trades']}")
    if m["max_dd_pct"] > g["max_dd_pct"]:
        reasons.append(f"DD {m['max_dd_pct']:.1f}%>{g['max_dd_pct']}%")
    pf = m["profit_factor"]
    if pf < g["min_pf"]:
        reasons.append(f"PF {pf:.2f}<{g['min_pf']}")
    if not g["allow_liq"] and m.get("liq_hits", 0) > 0:
        reasons.append(f"{m['liq_hits']} liquidation breach(es)")
    if m.get("sharpe", 0.0) < g["min_sharpe"]:
        reasons.append(f"Sharpe {m.get('sharpe', 0.0):.2f}<{g['min_sharpe']}")
    return (len(reasons) == 0, reasons)
