"""TradingView → ccxt timeframe normalization.

TV's {{interval}} placeholder substitutes as bare digits for intraday ("5" =
5m, "240" = 4h) and single letters for daily/weekly/monthly ("D"/"W"/"M").
ccxt and BloFin's REST API expect ccxt-format strings ("5m","4h","1d","1w","1M").

Shared module so the webhook validator (ingress) and the BloFin client
(defensive guard) can use the same canonical set without a circular import.
"""
from __future__ import annotations
from typing import Any, Optional


CCXT_TIMEFRAMES: frozenset[str] = frozenset({
    "1m", "3m", "5m", "15m", "30m", "45m",
    "1h", "2h", "3h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
})

_TV_MINUTES_TO_CCXT_HOURS = {
    60: "1h", 120: "2h", 180: "3h", 240: "4h",
    360: "6h", 480: "8h", 720: "12h",
}


def normalize_tv_timeframe(v: Any) -> Optional[str]:
    """Map a TradingView {{interval}} value to a ccxt timeframe string.

    Returns None for empty / unsubstituted placeholder / unrecognized junk —
    callers should then fall back to the configured default timeframe.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        v = str(int(v))
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    if s.startswith("{{") and s.endswith("}}"):
        return None

    if s in CCXT_TIMEFRAMES:
        return s

    upper = s.upper()
    if upper in ("D", "1D"):
        return "1d"
    if upper in ("W", "1W"):
        return "1w"
    if upper in ("M", "1M"):
        return "1M"

    if s.isdigit():
        n = int(s)
        if n in _TV_MINUTES_TO_CCXT_HOURS:
            return _TV_MINUTES_TO_CCXT_HOURS[n]
        candidate = f"{n}m"
        if candidate in CCXT_TIMEFRAMES:
            return candidate

    return None
