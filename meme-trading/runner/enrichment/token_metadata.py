"""Fetch token metadata via Helius DAS getAsset."""
from typing import Any

from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.token_metadata")


class TokenMetadataFetcher:
    """Wraps Helius DAS `getAsset` for fungible tokens.

    Returns a dict of normalized fields, or None if the RPC call fails or
    the response does not contain a usable result. Raising exceptions is
    reserved for bugs; network/remote failures are converted to `None`.
    """

    def __init__(self, http: RateLimitedClient, rpc_url: str):
        self.http = http
        self.rpc_url = rpc_url

    async def fetch(self, mint: str) -> dict[str, Any] | None:
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAsset",
                    "params": {"id": mint},
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("das_getAsset_error", mint=mint, error=str(e))
            return None

        if resp.status_code != 200:
            logger.warning(
                "das_getAsset_non_200", mint=mint, status=resp.status_code
            )
            return None

        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("das_getAsset_bad_json", mint=mint, error=str(e))
            return None

        result = data.get("result")
        if not result or not isinstance(result, dict):
            return None

        content = result.get("content") or {}
        metadata = content.get("metadata") or {}
        token_info = result.get("token_info") or {}

        return {
            "symbol": metadata.get("symbol"),
            "name": metadata.get("name"),
            "description": metadata.get("description"),
            "decimals": token_info.get("decimals"),
            "supply": token_info.get("supply"),
            "mint_authority": token_info.get("mint_authority"),
            "freeze_authority": token_info.get("freeze_authority"),
        }
