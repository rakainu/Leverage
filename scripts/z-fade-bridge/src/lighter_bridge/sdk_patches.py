"""Runtime patches for the Lighter SDK's paper-client order book.

ROOT CAUSE (diagnosed 2026-05-29 via asyncio debug + book-depth probe):
the order-book WS consumer (`PaperOrderBookListener._run`) drains buffered
deltas in a tight loop without yielding to the event loop, and the SDK's
`InMemoryOrderBook._merge_levels` does heavy redundant work on EVERY delta —
it re-normalises (`from_any`) and re-sorts the ENTIRE, already-sorted book
*twice* per update. The book itself is bounded (~760 levels), so this is pure
wasted CPU, not a leak. With two live markets the per-delta cost climbed above
the inter-message interval, so the consumer fell permanently behind, monopolised
the loop for up to 24.5s (measured), and blew past the websockets 20s keepalive
ping timeout — killing both order-book sockets and driving the reconnect storm.

`_merge_levels_fast` is a drop-in replacement that produces an IDENTICAL book
(asks ascending, bids descending, zero-size levels removed, deduped by price)
with far less work: existing levels are already `OrderBookLevel` instances, so
we index them by numeric price directly (no re-`from_any`, no pre-sort);
`from_any` runs only on the NEW delta levels; the result is sorted exactly once.
Because the output is identical, paper marks and fills are unchanged and
backtest parity is preserved. Keying by numeric price (vs the SDK's raw price
string) also closes a latent dedup gap.
"""
from __future__ import annotations

import logging

from lighter.paper_client.order_book import InMemoryOrderBook, OrderBookLevel

log = logging.getLogger(__name__)

_PATCHED = False


def _merge_levels_fast(cls, existing_levels, new_levels, is_asks):
    by_price: dict[float, OrderBookLevel] = {}
    for lv in existing_levels:
        # existing levels are already normalised OrderBookLevel instances
        if lv.size_float > 0:
            by_price[lv.price_float] = lv
    for new in new_levels:
        nlv = OrderBookLevel.from_any(new)
        price = nlv.price_float
        if nlv.size_float == 0:
            by_price.pop(price, None)
        else:
            by_price[price] = nlv
    return sorted(
        by_price.values(),
        key=lambda level: level.price_float,
        reverse=not is_asks,
    )


def apply_order_book_patches() -> None:
    """Idempotently install the efficient order-book merge. Call once at startup
    BEFORE any PaperClient/track_market is created."""
    global _PATCHED
    if _PATCHED:
        return
    InMemoryOrderBook._merge_levels = classmethod(_merge_levels_fast)
    _PATCHED = True
    log.warning("SDK patch applied: InMemoryOrderBook._merge_levels -> incremental merge "
                "(fixes order-book consumer loop-starvation / WS reconnect storm)")
