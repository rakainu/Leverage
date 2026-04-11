"""Helius-based deployer history fetcher.

v1: find deployer by looking at the earliest signature against the mint
address, then parse that transaction's first signer as the deployer.
Age is derived from the blockTime of that init tx.

Deployer token count is deliberately omitted in v1 — getting it reliably
requires scanning the deployer's full signature history, which is slow
and API-intensive. Filter/scoring code treats `deployer_token_count=None`
as unknown rather than zero.
"""
from datetime import datetime, timezone
from typing import Any

from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.deployer")


class DeployerFetcher:
    def __init__(self, http: RateLimitedClient, rpc_url: str):
        self.http = http
        self.rpc_url = rpc_url

    async def fetch(self, mint: str) -> dict[str, Any] | None:
        earliest_sig, block_time = await self._earliest_signature(mint)
        if earliest_sig is None:
            return None

        signer = await self._signer_of(earliest_sig)
        if signer is None:
            return None

        age_seconds: int | None = None
        first_tx_time: datetime | None = None
        if block_time is not None:
            first_tx_time = datetime.fromtimestamp(block_time, tz=timezone.utc)
            age_seconds = int(
                (datetime.now(timezone.utc) - first_tx_time).total_seconds()
            )

        return {
            "deployer_address": signer,
            "deployer_first_tx_time": first_tx_time,
            "deployer_age_seconds": age_seconds,
            "deployer_token_count": None,  # not computed in v1
        }

    async def _earliest_signature(self, mint: str) -> tuple[str | None, int | None]:
        """Return (signature, blockTime) of the earliest known tx for this mint."""
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [mint, {"limit": 1000}],
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("signatures_error", mint=mint, error=str(e))
            return None, None
        if resp.status_code != 200:
            return None, None

        try:
            body = resp.json()
        except Exception:
            return None, None
        result = body.get("result") or []
        if not result:
            return None, None

        # Result is returned newest-first; find the earliest by blockTime.
        with_time = [e for e in result if e.get("blockTime") is not None]
        if not with_time:
            # Fall back to last element (oldest in result order).
            tail = result[-1]
            return tail.get("signature"), tail.get("blockTime")

        earliest = min(with_time, key=lambda e: e["blockTime"])
        return earliest.get("signature"), earliest.get("blockTime")

    async def _signer_of(self, signature: str) -> str | None:
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "confirmed",
                        },
                    ],
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("getTransaction_error", signature=signature, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception:
            return None

        result = body.get("result")
        if not result:
            return None
        message = (result.get("transaction") or {}).get("message") or {}
        keys = message.get("accountKeys") or []

        for key in keys:
            if isinstance(key, dict):
                if key.get("signer"):
                    return key.get("pubkey")
            elif isinstance(key, str):
                # Bare-string format — first entry is conventionally the fee payer.
                return key
        return None
