"""Monitor tracked wallets via Solana WebSocket logsSubscribe."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
import websockets

from config.settings import Settings
from db.database import get_db
from engine.signal import BuyEvent
from scanner.rpc_pool import RpcPool
from scanner.transaction_parser import TransactionParser

logger = logging.getLogger("smc.scanner.monitor")


class WalletMonitor:
    """Subscribes to Solana WS logsSubscribe for tracked wallets.

    Splits wallets across multiple WS connections (25 per connection)
    and emits BuyEvents to the event_bus queue.
    """

    WALLETS_PER_CONNECTION = 25

    def __init__(self, settings: Settings, event_bus: asyncio.Queue):
        self.settings = settings
        self.event_bus = event_bus
        self.ws_pool = RpcPool(settings.solana_ws_urls)
        self.parser = TransactionParser(
            RpcPool(settings.solana_rpc_urls),
            httpx.AsyncClient(timeout=30),
        )
        self.wallets: dict[str, dict] = {}  # address -> wallet info
        self._running = True
        self._seen_signatures: set[str] = set()  # Dedup within session
        self._max_seen = 10000  # Prune after this many

    async def load_wallets(self):
        """Load active wallets from wallets.json."""
        path = Path(self.settings.wallets_json_path)
        if not path.exists():
            logger.warning(f"Wallets file not found: {path}")
            return
        data = json.loads(path.read_text())
        self.wallets = {
            w["address"]: w
            for w in data.get("wallets", [])
            if w.get("active", True)
        }
        logger.info(f"Loaded {len(self.wallets)} active wallets")

    async def run(self):
        """Main loop: split wallets into chunks, run each in its own WS."""
        await self.load_wallets()

        if not self.wallets:
            logger.warning("No wallets to monitor. Add wallets to config/wallets.json")
            # Keep running and check for wallets periodically
            while self._running:
                await asyncio.sleep(30)
                await self.load_wallets()
                if self.wallets:
                    break

        addresses = list(self.wallets.keys())
        chunks = [
            addresses[i : i + self.WALLETS_PER_CONNECTION]
            for i in range(0, len(addresses), self.WALLETS_PER_CONNECTION)
        ]

        logger.info(f"Starting {len(chunks)} WS connection(s) for {len(addresses)} wallets")

        tasks = [self._run_chunk(chunk) for chunk in chunks]
        tasks.append(self._reload_loop())
        await asyncio.gather(*tasks)

    async def _run_chunk(self, addresses: list[str]):
        """Persistent WS connection for a chunk of wallets. Reconnects on failure."""
        backoff = 5
        while self._running:
            ws_url = self.ws_pool.next()
            try:
                async with websockets.connect(
                    ws_url, ping_interval=20, ping_timeout=30
                ) as ws:
                    sub_ids = {}
                    for addr in addresses:
                        sub_id = await self._subscribe(ws, addr)
                        if sub_id is not None:
                            sub_ids[sub_id] = addr

                    self.ws_pool.mark_healthy(ws_url)
                    backoff = 5  # Reset on success
                    logger.info(
                        f"WS connected, subscribed to {len(sub_ids)} wallets"
                    )

                    async for raw_msg in ws:
                        msg = json.loads(raw_msg)
                        await self._handle_notification(msg, sub_ids)

            except Exception as e:
                self.ws_pool.mark_failed(ws_url)
                logger.error(f"WS connection lost: {e}, reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _subscribe(self, ws, address: str) -> int | None:
        """Send logsSubscribe with mentions filter for a wallet address."""
        request = {
            "jsonrpc": "2.0",
            "id": hash(address) % 100000,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [address]},
                {"commitment": "confirmed"},
            ],
        }
        await ws.send(json.dumps(request))
        try:
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            sub_id = resp.get("result")
            if sub_id is not None:
                return sub_id
            logger.warning(f"Subscribe failed for {address[:8]}...: {resp}")
        except asyncio.TimeoutError:
            logger.warning(f"Subscribe timeout for {address[:8]}...")
        return None

    async def _handle_notification(self, msg: dict, sub_ids: dict):
        """Extract signature from log notification, parse transaction."""
        if msg.get("method") != "logsNotification":
            return

        result = msg["params"]["result"]
        signature = result["value"]["signature"]
        err = result["value"]["err"]
        if err is not None:
            return  # Failed transaction

        # Dedup
        if signature in self._seen_signatures:
            return
        self._seen_signatures.add(signature)
        if len(self._seen_signatures) > self._max_seen:
            # Prune oldest half
            to_keep = list(self._seen_signatures)[self._max_seen // 2 :]
            self._seen_signatures = set(to_keep)

        sub_id = msg["params"]["subscription"]
        wallet_addr = sub_ids.get(sub_id)
        if not wallet_addr:
            logger.debug(f"Unknown sub_id {sub_id} for sig {signature[:12]}..")
            return

        logger.info(f"Processing txn {signature[:12]}.. from wallet {wallet_addr[:8]}..")
        # Parse in background to not block WS message processing
        asyncio.create_task(self._parse_and_emit(signature, wallet_addr))

    async def _parse_and_emit(self, signature: str, wallet_address: str):
        """Fetch txn, parse, emit BuyEvent if it's a buy."""
        try:
            event = await self.parser.parse_transaction(signature, wallet_address)
            if event and event.amount_sol > 0:
                logger.info(
                    f"BUY detected: {wallet_address[:8]}.. bought "
                    f"{event.token_mint[:8]}.. for {event.amount_sol:.4f} SOL "
                    f"via {event.dex}"
                )
                await self.event_bus.put(event)

                # Persist to DB
                db = await get_db()
                await db.execute(
                    """INSERT OR IGNORE INTO buy_events
                       (wallet_address, token_mint, token_symbol, amount_sol,
                        amount_tokens, signature, dex, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.wallet_address,
                        event.token_mint,
                        event.token_symbol,
                        event.amount_sol,
                        event.amount_tokens,
                        event.signature,
                        event.dex,
                        event.timestamp.isoformat(),
                    ),
                )
                await db.commit()

        except Exception as e:
            logger.warning(f"Failed to parse txn {signature[:16]}...: {e}")

    async def _reload_loop(self):
        """Reload wallets.json every 5 minutes to pick up changes."""
        while self._running:
            await asyncio.sleep(300)
            old_count = len(self.wallets)
            await self.load_wallets()
            new_count = len(self.wallets)
            if new_count != old_count:
                logger.info(f"Wallet list updated: {old_count} -> {new_count}")
