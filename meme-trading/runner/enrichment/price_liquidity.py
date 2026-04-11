"""DexScreener + Jupiter price/liquidity/slippage fetcher."""
from datetime import datetime, timezone
from typing import Any

from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.price_liquidity")

DEXSCREENER_BASE = "https://api.dexscreener.com"
JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"


class PriceLiquidityFetcher:
    """Combine DexScreener pair data with Jupiter quote-based slippage checks."""

    def __init__(self, http: RateLimitedClient):
        self.http = http

    async def fetch(
        self,
        mint: str,
        sizes_sol: list[float] | None = None,
    ) -> dict[str, Any] | None:
        sizes_sol = sizes_sol or [0.25]

        pair = await self._best_pair(mint)
        if pair is None:
            return None

        slippage_map: dict[float, float] = {}
        for size in sizes_sol:
            slip = await self._jupiter_slippage_pct(mint, size)
            if slip is not None:
                slippage_map[size] = slip

        return {
            "price_sol": _as_float(pair.get("priceNative")),
            "price_usd": _as_float(pair.get("priceUsd")),
            "liquidity_usd": _as_float(((pair.get("liquidity") or {}).get("usd"))),
            "volume_24h_usd": _as_float(((pair.get("volume") or {}).get("h24"))),
            "pair_age_seconds": _pair_age_seconds(pair.get("pairCreatedAt")),
            "slippage_at_size_pct": slippage_map,
            "dex_id": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
        }

    async def _best_pair(self, mint: str) -> dict | None:
        url = f"{DEXSCREENER_BASE}/tokens/v1/solana/{mint}"
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("dexscreener_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("dexscreener_bad_json", mint=mint, error=str(e))
            return None

        # DexScreener returns either a list of pairs or {"pairs": [...]}.
        pairs = body if isinstance(body, list) else (body.get("pairs") or [])
        if not pairs:
            return None

        return max(
            pairs,
            key=lambda p: _as_float(((p.get("liquidity") or {}).get("usd"))) or 0.0,
        )

    async def _jupiter_slippage_pct(self, mint: str, size_sol: float) -> float | None:
        # inAmount is lamports
        in_amount = int(size_sol * 1_000_000_000)
        params = {
            "inputMint": SOL_MINT,
            "outputMint": mint,
            "amount": str(in_amount),
            "slippageBps": "500",
            "swapMode": "ExactIn",
        }
        try:
            resp = await self.http.get(JUPITER_QUOTE, params=params)
        except Exception as e:  # noqa: BLE001
            logger.warning("jupiter_quote_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("jupiter_quote_bad_json", mint=mint, error=str(e))
            return None

        impact = data.get("priceImpactPct")
        if impact is None:
            return None
        try:
            return float(impact) * 100.0
        except (TypeError, ValueError):
            return None


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pair_age_seconds(created_ms: Any) -> int | None:
    if created_ms is None:
        return None
    try:
        created_s = float(created_ms) / 1000.0
    except (TypeError, ValueError):
        return None
    now_s = datetime.now(timezone.utc).timestamp()
    age = int(now_s - created_s)
    return max(age, 0)
