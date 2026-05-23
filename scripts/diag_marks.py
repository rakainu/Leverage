"""Print PaperClient's current mark for each open position vs Lighter's last trade
+ order-book mid. Tells us if the mark feed is stale and trail_high is frozen."""
import asyncio
from datetime import datetime, timezone

import lighter

MARKETS = [
    {"symbol": "SOL", "market_id": 2,  "entry": 87.518,  "base": 86.636,  "side": "short"},
    {"symbol": "ZEC", "market_id": 90, "entry": 642.437, "base": 11.093,  "side": "short"},
]


async def main() -> None:
    api = lighter.ApiClient(configuration=lighter.Configuration(host="https://mainnet.zklighter.elliot.ai"))
    paper = lighter.PaperClient(api, initial_collateral_usdc=2000)
    for m in MARKETS:
        await paper.track_market(market_id=m["market_id"])
    # Give the WS a moment
    await asyncio.sleep(3)

    # Open a test short at current price, wait, check whether pos.mark_price evolves
    print(f"now utc: {datetime.now(tz=timezone.utc).isoformat()}")
    test_market = MARKETS[0]["market_id"]
    print(f"\n=== open test short on market_id={test_market} ===")
    r = await paper.create_paper_order(lighter.PaperOrderRequest(
        market_id=test_market, side=lighter.PaperOrderSide.SELL, base_amount=1.0,
    ))
    print(f"fill avg={float(r.avg_price):.4f}")
    for i in range(4):
        await asyncio.sleep(3)
        cfg = paper.market_configs.get(test_market)
        pos_obj = paper.get_position(test_market)
        last_trade = float(cfg.last_trade_price) if cfg else float("nan")
        mark = float(pos_obj.mark_price) if pos_obj is not None else float("nan")
        print(f"  t+{(i+1)*3}s  pos.mark_price={mark:.4f}  config.last_trade_price={last_trade:.4f}")
    # Flatten
    await paper.create_paper_order(lighter.PaperOrderRequest(
        market_id=test_market, side=lighter.PaperOrderSide.BUY, base_amount=1.0,
    ))

    await paper.close()
    await api.close()


asyncio.run(main())
