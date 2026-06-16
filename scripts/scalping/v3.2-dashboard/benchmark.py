"""Engine benchmark — what the backtest predicts V3.2 should do live.

The dashboard's whole job is to answer "is the live demo tracking the engine?"
so these numbers are the reference line. They come from the 2026-06-16 audit:
the V3.1 config (slope0.15 + Sunday + body filters, 5-stage trail, ZEC SL 1.1%,
both sides) run through the honest engine on 52k ZEC 5m bars (181 days).

V3.2 runs on BloFin DEMO (zero fee), so the zero-fee engine pass is the primary
target. The entry-slip test showed realistic fills land between the 0-slip and
0.10%-slip rows, so each metric carries a `band` (low, high) = the realistic
live range, not a single brittle point.
"""
from __future__ import annotations

# Reference: 7-coin basket engine pass (ZEC/XRP/DOGE/SOL/BTC/BNB/HYPE), demo
# (zero-fee). Per-trade quality (WR/PF/avg_R) is similar across coins, so those
# bands hold; throughput (trades/day, net/day) is the SUM across the basket.
#   per-coin demo trades/day: ZEC 5.3 XRP 1.3 DOGE 1.5 SOL 1.7 BTC 0.8 BNB 0.6
#   HYPE 6.7  → ~18/day. net/day sum ≈ $378 (idealized demo — live fills lower).
ENGINE = {
    "source": "7-coin basket · demo (zero-fee) · idealized fills",
    "win_rate": 0.69,
    "profit_factor": 3.0,
    "avg_r": 0.255,           # avg pnl / stop ($82.50)
    "avg_trade_usdt": 21.0,
    "trades_per_day": 18.0,   # sum across the 7-coin basket
    "net_per_day_usdt": 378.0,
    "max_dd_usdt": -400.0,    # basket (concurrent positions) — rough
    # Realistic live bands (idealized .. fee/slip-haircut).
    "band": {
        "win_rate": (0.62, 0.72),
        "profit_factor": (2.2, 3.4),
        "avg_r": (0.16, 0.28),
        "avg_trade_usdt": (13.0, 21.0),
        "trades_per_day": (12.0, 24.0),
    },
    # Exit-reason mix the engine produces (representative).
    "exit_mix": {"trail_sl": 0.67, "sl_be": 0.21, "sl": 0.12},
}

# Secondary reference (if ever flipped to a fee'd/live book): BloFin 0.06%/side.
ENGINE_FEE = {
    "win_rate": 0.71,
    "profit_factor": 2.80,
    "avg_r": 0.242,
    "avg_trade_usdt": 19.97,
    "net_per_day_usdt": 107.1,
    "max_dd_usdt": -354.0,
}

# Minimum closed trades before tracking verdicts are statistically meaningful.
TRACKING_MIN_N = 30
