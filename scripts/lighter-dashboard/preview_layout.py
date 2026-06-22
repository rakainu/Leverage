"""Offline preview of the scalper dashboard layout.

Renders templates/index.html with a mock state (no jinja2 / no DB needed) so the
bottom-block rearrangement can be eyeballed before deploy. Writes preview.html.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
TPL = (HERE / "templates" / "index.html").read_text(encoding="utf-8")

# Mock state mirroring the live numbers visible in the screenshot.
STATE = {
    "meta": {"hb_state": "live", "updated": "13:04", "equity": 3768.42,
             "equity_pct": 4.7, "days": 5, "coins": ["ETH", "BTC", "XMR", "HYPE", "SOL", "BNB"]},
    "edge": {"verdict": "HOLDING", "cls": "good", "chip": "edge intact",
             "n": 154, "trades_per_day": 31, "pf_bt": "1.49", "wr_bt": 88,
             "be_pct": 84.0, "wr_live": 88.0, "cushion": 4.0, "pf_live": 1.52,
             "pf_roll": 1.47, "avg_trade": 1.1, "avg_win": 12.5, "avg_loss": -33.4},
    "stat": {"net": 168.0, "net_per_day": 33.6, "max_dd": -292.0,
             "max_consec": 3, "withdrawn": 0},
    "sides": [{"side": "long", "n": 70, "net": 90, "wr": 87},
              {"side": "short", "n": 84, "net": 78, "wr": 89}],
    "protections": [{"name": "Cooldown", "value": "3 losses / 180m"},
                    {"name": "Trend gate", "value": "0.08"},
                    {"name": "Max gap", "value": "0.05%"}],
    "cooldown_active": False, "streak": "W W L",
    "equity_curve": [3600, 3620, 3590, 3660, 3700, 3680, 3740, 3768],
    "per_coin": [{"verdict": "keep", "symbol": "ETH", "n": 30, "win_pct": 90, "pf": 1.92, "net": 55, "cushion": 6},
                 {"verdict": "keep", "symbol": "BTC", "n": 24, "win_pct": 88, "pf": 1.7, "net": 40, "cushion": 4},
                 {"verdict": "watch", "symbol": "SOL", "n": 28, "win_pct": 85, "pf": 1.3, "net": 18, "cushion": 1}],
    "positions": [{"upnl": 4.2, "side": "short", "symbol": "HYPE", "entry": 38.1234, "mark": 38.0012}],
    "recent": [{"symbol": ["HYPE", "HYPE", "SUI", "HYPE", "DOGE", "DOGE", "XMR", "XMR", "BNB", "SUI", "ETH", "PYC", "SOL", "ETH", "BTC"][i],
                "side": ["short", "short", "short", "short", "short", "short", "long", "long", "long", "short", "long", "long", "short", "long", "short"][i],
                "exit_reason": ["tp", "tp", "tp", "tp", "tp", "tp", "tp", "sl", "sl", "tp", "tp", "tp", "tp", "tp", "sl"][i],
                "pnl": [7.39, 30.57, 16.96, 3.11, -1.99, 0.85, 10.62, -66.0, -21.10, 4.84, 8.11, 8.15, 5.2, 9.0, -33.0][i],
                "closed": ["06-21 13:03", "06-21 12:55", "06-21 09:13", "06-21 07:12", "06-21 07:01", "06-21 05:53",
                           "06-21 05:40", "06-21 07:28", "06-21 03:15", "06-21 03:55", "06-21 02:20", "06-21 02:18",
                           "06-21 01:40", "06-21 01:02", "06-20 23:50"][i]}
               for i in range(15)],
    "exits": [{"reason": "tp", "frac": 0.86, "n": 132, "net": 1654},
              {"reason": "sl", "frac": 0.13, "n": 20, "net": -1470},
              {"reason": "time", "frac": 0.01, "n": 2, "net": -16}],
    "signals": [
        {"outcome": "fired", "symbol": "HYPE", "side": "short", "slope": None, "time": "13:03"},
        {"outcome": "detected", "symbol": "HYPE", "side": "short", "slope": -0.23, "time": "11:51"},
        {"outcome": "entry_unfilled", "symbol": "HYPE", "side": "short", "slope": None, "time": "10:01"},
        {"outcome": "detected", "symbol": "HYPE", "side": "short", "slope": -0.28, "time": "09:31"},
        {"outcome": "fired", "symbol": "HYPE", "side": "short", "slope": None, "time": "09:16"},
        {"outcome": "detected", "symbol": "HYPE", "side": "short", "slope": -0.29, "time": "09:14"},
        {"outcome": "fired", "symbol": "SUI", "side": "short", "slope": None, "time": "09:00"},
        {"outcome": "detected", "symbol": "SUI", "side": "short", "slope": -0.00, "time": "08:45"},
        {"outcome": "fired", "symbol": "XMR", "side": "short", "slope": None, "time": "08:30"},
        {"outcome": "entry_unfilled", "symbol": "XMR", "side": "long", "slope": None, "time": "08:16"},
    ],
    "withdrawals": {"total": 0, "account_now": 3768, "target": 10800},
    "fillq": {"maker_pct": 0, "slip": 0, "live_wr": 88, "bt_wr": 88, "n": 0},
}

html = TPL
html = html.replace("{{ state|tojson }}", json.dumps(STATE))
html = re.sub(r"\{\{ cfg\.title\|default\('Scalper', true\) \}\}", "Scalper", html)
html = re.sub(r"\{\{ cfg\.title\|default\('SCALPER', true\)\|upper \}\}", "SCALPER", html)
html = re.sub(r"\{\{ cfg\.subtitle\|default\('[^']*', true\) \}\}",
              "regime_mr · VWAP-z fade · Lighter zero-fee", html)
# any leftover jinja -> blank, so nothing renders raw
html = re.sub(r"\{\{.*?\}\}", "", html)

out = HERE / "preview.html"
out.write_text(html, encoding="utf-8")
print("wrote", out)
