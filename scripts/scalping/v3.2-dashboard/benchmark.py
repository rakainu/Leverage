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

# Primary reference: 181-day, zero-fee (demo) engine pass, ZEC, both sides.
ENGINE = {
    "source": "52k-bar ZEC 5m · 181d · V3.1 cfg · both sides · zero-fee",
    "win_rate": 0.71,
    "profit_factor": 5.67,
    "avg_r": 0.406,           # avg pnl / stop ($82.50)
    "avg_trade_usdt": 33.47,
    "trades_per_day": 5.36,   # 971 trades / 181 days
    "net_per_day_usdt": 179.5,
    "max_dd_usdt": -247.0,
    # Realistic live bands (0-slip .. 0.10% entry-slip, from the slip sweep).
    "band": {
        "win_rate": (0.67, 0.71),
        "profit_factor": (3.78, 5.67),
        "avg_r": (0.315, 0.406),
        "avg_trade_usdt": (25.97, 33.47),
        "trades_per_day": (5.0, 7.0),
    },
    # Exit-reason mix the engine produces (from the same-window 240-trade pass).
    "exit_mix": {"trail_sl": 0.675, "sl_be": 0.213, "sl": 0.113},
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
