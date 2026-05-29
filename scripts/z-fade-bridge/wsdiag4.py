"""Confirm the order-book bloat: does the SDK's InMemoryOrderBook grow without
bound, and would keying levels by NUMERIC price (instead of the raw price
string) collapse it?

asyncio debug fingered PaperOrderBookListener._run / apply_delta blocking the
loop for up to 24.5s — the signature of a book that keeps growing. _merge_levels
keys by `level.price` (raw string); if the delta stream varies price formatting,
deletes miss and stale levels pile up forever. This tracks ONE market and prints
book depth every few seconds plus the depth after de-duping by float price. If
raw depth climbs while float-deduped depth stays flat → string-key bloat
confirmed and the fix is: key by float. Read-only, no orders.
"""
import asyncio
import time

import lighter

HOST = "https://mainnet.zklighter.elliot.ai"
MARKET = 2          # SOL
DURATION_S = 90
SAMPLE_S = 10


def _depths(book):
    raw = len(book.asks) + len(book.bids)
    # how many distinct price levels survive if we key by float instead of string
    a = len({lv.price_float for lv in book.asks})
    b = len({lv.price_float for lv in book.bids})
    return raw, a + b


async def main():
    api = lighter.ApiClient(configuration=lighter.Configuration(host=HOST))
    paper = lighter.PaperClient(api, initial_collateral_usdc=2000.0)
    await paper.track_market(market_id=MARKET)
    book = paper.order_books.get(MARKET)
    print(f"[{time.strftime('%H:%M:%S')}] tracking mkt{MARKET}", flush=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < DURATION_S:
        await asyncio.sleep(SAMPLE_S)
        raw, deduped = _depths(book)
        print(f"[{time.strftime('%H:%M:%S')}] +{time.monotonic()-t0:4.0f}s  "
              f"raw_levels={raw:6d}  float_deduped={deduped:6d}  "
              f"bloat_factor={raw/max(deduped,1):.1f}x", flush=True)
    await paper.close()
    await api.close()


if __name__ == "__main__":
    asyncio.run(main())
