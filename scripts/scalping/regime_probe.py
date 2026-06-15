"""One-off diagnostic: fetch live 15m candles for the 6-coin basket and print the
bridge's OWN regime gate state (slope / z / reg_long / reg_short) on the last
closed bar. Proves whether each coin SHOULD be firing right now, or is genuinely
in a no-fire zone (no clear trend + no VWAP extension). Read-only; no trading."""
import asyncio
import logging
import sys

logging.disable(logging.INFO)
sys.path.insert(0, "/app/src")

import lighter  # noqa: E402
from lighter_bridge.bar_feed import BarFeed, BarFeedConfig  # noqa: E402
from lighter_bridge.regime import prepare_regime  # noqa: E402

MARKETS = {"ETH": 0, "BTC": 1, "SOL": 2, "HYPE": 24, "BNB": 25, "XMR": 77}
P = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, atr_period=14)


async def main():
    api = lighter.ApiClient(configuration=lighter.Configuration(
        host="https://mainnet.zklighter.elliot.ai"))
    print(f"{'coin':5}{'close':>10}{'slope':>11}{'trend':>7}{'z':>7}{'|z|>=1.5':>10}   why no signal / FIRES")
    print("-" * 78)
    for sym, mid in MARKETS.items():
        try:
            bf = BarFeed(api, BarFeedConfig(market_id=mid, symbol=sym,
                                            resolution="15m", history_bars=300))
            df = await bf.bootstrap()
            r = prepare_regime(df, **P)
            row = r.iloc[-2]                       # last CLOSED bar (-1 is in-progress)
            slope, z, close = row["slope"], row["zscore"], row["Close"]
            up = slope > 0
            rl, rs = bool(row["reg_long"]), bool(row["reg_short"])
            if rl:
                why = "FIRES LONG"
            elif rs:
                why = "FIRES SHORT"
            elif up:
                why = "uptrend -> need z<=-1.50 (price below VWAP); it's not"
            else:
                why = "downtrend -> need z>=+1.50 (price above VWAP); it's not"
            zhit = "yes" if abs(z) >= 1.5 else "no"
            print(f"{sym:5}{close:>10.2f}{slope:>+11.4f}{('up' if up else 'down'):>7}"
                  f"{z:>+7.2f}{zhit:>10}   {why}")
        except Exception as exc:
            print(f"{sym:5}  ERROR: {exc}")
    await api.close()


asyncio.run(main())
