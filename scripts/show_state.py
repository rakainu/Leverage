"""Pull lowest 5m low for each open position since open time, compute trail SL position.
Uses the bridge's lighter SDK to query candles. Runs inside lighter-bridge container."""
import asyncio
from datetime import datetime, timezone

import lighter

POSITIONS = [
    {"id": 13, "symbol": "SOL", "market_id": 2,  "side": "short", "entry": 87.518,
     "base_amount": 86.636, "notional": 7582.21, "opened_at": "2026-05-22T13:35:21+00:00"},
    {"id": 14, "symbol": "ZEC", "market_id": 90, "side": "short", "entry": 642.437,
     "base_amount": 11.093, "notional": 7126.55, "opened_at": "2026-05-22T15:30:00+00:00"},
]
TRAIL_DIST_USD = 15.0


async def main() -> None:
    api = lighter.ApiClient(configuration=lighter.Configuration(host="https://mainnet.zklighter.elliot.ai"))
    candle_api = lighter.CandlestickApi(api)
    for p in POSITIONS:
        opened = datetime.fromisoformat(p["opened_at"]).timestamp()
        now = datetime.now(tz=timezone.utc).timestamp()
        resp = await candle_api.candlesticks(
            market_id=p["market_id"], resolution="5m",
            start_timestamp=int(opened), end_timestamp=int(now), count_back=500,
        )
        d = resp.to_dict()
        candles = d.get("candlesticks") or d.get("c") or d.get("candles") or []
        if not candles:
            print(f"{p['symbol']}: NO CANDLES; resp keys={list(d.keys())}")
            continue

        side = p["side"]
        base = p["base_amount"]
        entry = p["entry"]
        if side == "short":
            extreme = min(float(c.get("low") or c.get("l")) for c in candles)
        else:
            extreme = max(float(c.get("high") or c.get("h")) for c in candles)
        last_close = float(candles[-1].get("close") or candles[-1].get("c"))

        if side == "short":
            pnl_now = (entry - last_close) * base
            peak_pnl = (entry - extreme) * base
            trail_sl_px = extreme + (TRAIL_DIST_USD / base)
            locked_in_pnl = (entry - trail_sl_px) * base
        else:
            pnl_now = (last_close - entry) * base
            peak_pnl = (extreme - entry) * base
            trail_sl_px = extreme - (TRAIL_DIST_USD / base)
            locked_in_pnl = (trail_sl_px - entry) * base

        bounce_room_per_coin = abs(trail_sl_px - last_close)

        print(f"#{p['id']} {p['symbol']} {side.upper()}:")
        print(f"  entry=${entry:.4f}  notional=${p['notional']:,.0f}  size={base}")
        print(f"  trail_high (extreme price seen)=${extreme:.4f}")
        print(f"  current mark=${last_close:.4f}")
        print(f"  current unrealized PnL = ${pnl_now:+.2f}")
        print(f"  peak unrealized PnL    = ${peak_pnl:+.2f}")
        print(f"  computed trail SL price=${trail_sl_px:.4f}")
        print(f"  if SL triggers now -> locked PnL = ${locked_in_pnl:+.2f}")
        print(f"  bounce room before SL fires      = ${bounce_room_per_coin:.4f}/coin "
              f"(={bounce_room_per_coin/last_close*100:.2f}% mark move)")
        print()
    await api.close()


asyncio.run(main())
