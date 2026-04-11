"""Wallet monitor: WS logsSubscribe + signature dispatch to parser + event bus.

The WS connection + chunked subscribe loop is in `run()`. The core unit
of work — `handle_signature()` — is pure and testable without a real socket.
"""
import asyncio
import json
from collections import OrderedDict

import websockets

from runner.ingest.transaction_parser import TransactionParser
from runner.utils.logging import get_logger

logger = get_logger("runner.ingest.monitor")

WALLETS_PER_CONNECTION = 25


class WalletMonitor:
    """Monitor a set of wallets via Solana logsSubscribe, emit BuyEvents.

    `wallets` is a dict of wallet_address -> info dict (tier, source, etc).
    Signatures are deduplicated per process lifetime.
    """

    def __init__(
        self,
        wallets: dict[str, dict],
        event_bus: asyncio.Queue,
        parser: TransactionParser,
        ws_url: str = "",
        max_seen: int = 10000,
    ):
        self.wallets = wallets
        self.event_bus = event_bus
        self.parser = parser
        self.ws_url = ws_url
        self._seen_signatures: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max_seen
        self._running = True

    async def handle_signature(self, signature: str, wallet_address: str) -> None:
        """Core per-signature handler — test entry point."""
        if wallet_address not in self.wallets:
            return
        if signature in self._seen_signatures:
            return

        if len(self._seen_signatures) >= self._max_seen:
            # FIFO prune: drop the oldest half.
            for _ in range(self._max_seen // 2):
                if not self._seen_signatures:
                    break
                self._seen_signatures.popitem(last=False)
        self._seen_signatures[signature] = None

        event = await self.parser.parse_transaction(signature, wallet_address)
        if event is None:
            return
        await self.event_bus.put(event)
        logger.info(
            "buy_event",
            signature=event.signature,
            wallet=event.wallet_address,
            mint=event.token_mint,
            sol=event.sol_amount,
        )

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Top-level WS loop. Chunks wallets across connections.

        Each chunk runs `_run_connection` as its own task.
        """
        addresses = list(self.wallets.keys())
        chunks = [
            addresses[i : i + WALLETS_PER_CONNECTION]
            for i in range(0, len(addresses), WALLETS_PER_CONNECTION)
        ]
        logger.info(
            "monitor_start",
            wallets=len(addresses),
            chunks=len(chunks),
            per_conn=WALLETS_PER_CONNECTION,
        )
        await asyncio.gather(
            *(self._run_connection(chunk) for chunk in chunks),
            return_exceptions=True,
        )

    async def _run_connection(self, chunk: list[str]) -> None:
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=30) as ws:
                    for idx, wallet in enumerate(chunk):
                        await ws.send(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": idx,
                                    "method": "logsSubscribe",
                                    "params": [
                                        {"mentions": [wallet]},
                                        {"commitment": "confirmed"},
                                    ],
                                }
                            )
                        )
                    backoff = 1.0
                    sub_to_wallet: dict[int, str] = {}

                    async for raw in ws:
                        msg = json.loads(raw)
                        # Subscription confirmation:
                        if "result" in msg and isinstance(msg["result"], int) and "id" in msg:
                            idx = msg["id"]
                            if 0 <= idx < len(chunk):
                                sub_to_wallet[msg["result"]] = chunk[idx]
                            continue
                        # Log notification:
                        if msg.get("method") != "logsNotification":
                            continue
                        params = msg.get("params") or {}
                        sub_id = params.get("subscription")
                        value = (params.get("result") or {}).get("value") or {}
                        sig = value.get("signature")
                        err = value.get("err")
                        wallet = sub_to_wallet.get(sub_id)
                        if not sig or err is not None or wallet is None:
                            continue
                        await self.handle_signature(sig, wallet)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ws_disconnect",
                    error=str(e),
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
