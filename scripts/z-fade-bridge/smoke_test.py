"""Lighter SDK smoke test — verify connectivity, find market IDs, place a paper trade.

Goals:
  1. Connect to Lighter mainnet (read-only, no credentials)
  2. Discover markets — find ZEC-PERP and SOL-PERP (and their market_ids)
  3. Print contract specs for our target symbols (price decimals, size decimals, min size)
  4. Run a paper trade on each: buy 0.01 → sell 0.01, log the fills
  5. Confirm PaperClient reports realistic fills against live order book

If this passes, Phase C foundation is solid.
"""
from __future__ import annotations

import asyncio
import sys

import lighter

# Force utf-8 stdout for Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_URL = "https://mainnet.zklighter.elliot.ai"
INITIAL_COLLATERAL = 2_000   # matches Rich's $2k account target


async def discover_markets(api_client) -> list[dict]:
    """Pull the list of markets from Lighter's REST API.

    The OrderApi exposes order_book_details which returns market specs.
    We'll inspect the structure first time, then filter for our symbols.
    """
    order_api = lighter.OrderApi(api_client)
    print("\nFetching market list via OrderApi.order_book_details()...")
    try:
        # Lighter exposes a market list — try a few likely entry points
        details = await order_api.order_book_details()
        # The response is typed; convert to dict
        if hasattr(details, "to_dict"):
            data = details.to_dict()
        else:
            data = details
        # Pretty print top-level keys + a sample entry
        if isinstance(data, dict):
            print(f"  Top-level keys: {list(data.keys())}")
            books = data.get("order_book_details") or data.get("orderBookDetails") or data
            if isinstance(books, list) and books:
                print(f"  Found {len(books)} markets. Sample fields: {list(books[0].keys())}")
                return books
        print(f"  Raw response: {data}")
        return []
    except Exception as exc:
        print(f"  order_book_details failed: {exc}")
        return []


async def find_target_markets(markets: list[dict]) -> dict[str, dict]:
    """Filter markets for our targets — ZEC, SOL."""
    targets = {}
    # Try a few common key names
    for m in markets:
        symbol = (m.get("symbol") or m.get("name") or m.get("market_symbol") or "").upper()
        if symbol == "ZEC":
            targets["ZEC"] = m
        elif symbol == "SOL":
            targets["SOL"] = m
    return targets


async def paper_smoke(api_client, market_id: int, label: str) -> None:
    """Open a tiny paper trade on `market_id` to verify the executor works."""
    print(f"\n{'=' * 60}")
    print(f"PAPER SMOKE — {label} (market_id={market_id})")
    print(f"{'=' * 60}")

    paper = lighter.PaperClient(api_client, initial_collateral_usdc=INITIAL_COLLATERAL)

    print("  Loading order book snapshot...")
    await paper.track_market_snapshot(market_id=market_id)
    config = paper.market_configs.get(market_id)
    if config:
        print(f"  last_trade_price: {config.last_trade_price}")
        print(f"  size_decimals: {config.size_decimals}")
        print(f"  min_base_amount: {config.min_base_amount}")
        print(f"  price_decimals: {getattr(config, 'price_decimals', 'n/a')}")

    # Tiny test order — match min_base_amount or use a small absolute size
    test_size = max(config.min_base_amount if config else 0.01, 0.01)
    print(f"\n  BUY {test_size}...")
    buy_res = await paper.create_paper_order(
        lighter.PaperOrderRequest(
            market_id=market_id,
            side=lighter.PaperOrderSide.BUY,
            base_amount=test_size,
        )
    )
    print(f"    filled={buy_res.filled_size}  avg_price={buy_res.avg_price}  fee={buy_res.total_fee}")

    print(f"\n  SELL {test_size}...")
    sell_res = await paper.create_paper_order(
        lighter.PaperOrderRequest(
            market_id=market_id,
            side=lighter.PaperOrderSide.SELL,
            base_amount=test_size,
        )
    )
    print(f"    filled={sell_res.filled_size}  avg_price={sell_res.avg_price}  fee={sell_res.total_fee}")

    account = paper.get_account()
    pnl = paper.get_portfolio_value() - INITIAL_COLLATERAL
    print(f"\n  Final collateral: {account.collateral:.4f} USDC")
    print(f"  Portfolio value: {paper.get_portfolio_value():.4f} USDC")
    print(f"  Round-trip PnL: {pnl:+.4f} USDC")


async def main():
    print("=" * 60)
    print("LIGHTER SMOKE TEST")
    print("=" * 60)
    print(f"Host: {BASE_URL}")
    print(f"Paper collateral: ${INITIAL_COLLATERAL}")

    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=BASE_URL))
    try:
        markets = await discover_markets(api_client)
        if not markets:
            print("\n[!] Market discovery failed — see above. Aborting smoke test.")
            return

        print("\nAll markets (symbol / market_id):")
        for m in markets:
            sym = m.get("symbol") or m.get("name") or m.get("market_symbol") or "?"
            mid = m.get("market_id") or m.get("marketId") or m.get("id") or "?"
            print(f"  [{mid:>3}]  {sym}")

        targets = await find_target_markets(markets)
        print(f"\nMatched targets: {list(targets.keys())}")
        for label, m in targets.items():
            print(f"  {label}: {m.get('symbol') or m.get('name')} -> market_id={m.get('market_id') or m.get('id')}")

        # Try a paper smoke on each found target
        for label, m in targets.items():
            mid = m.get("market_id") or m.get("id")
            if mid is None:
                continue
            await paper_smoke(api_client, int(mid), label)
    finally:
        await api_client.close()


if __name__ == "__main__":
    asyncio.run(main())
