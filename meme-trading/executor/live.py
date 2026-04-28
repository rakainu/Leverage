"""Live trading executor — real Jupiter swaps on Solana."""

import base64
import json
import logging
from datetime import datetime, timezone

import base58
import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Processed

from config.settings import Settings
from db.database import get_db
from engine.signal import ConvergenceSignal
from engine.safety import SafetyResult
from executor.jupiter import JupiterClient
from utils.constants import SOL_MINT
from utils.solana_helpers import load_keypair, sol_to_lamports

logger = logging.getLogger("smc.executor.live")


class LiveExecutor:
    """Executes real trades via Jupiter Swap on Solana."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jupiter = JupiterClient(settings.jupiter_api_key)
        self.keypair = load_keypair(settings.solana_private_key)
        self.pubkey = str(self.keypair.pubkey())
        self.rpc_url = settings.solana_rpc_urls[0]
        self.http = httpx.AsyncClient(timeout=30)
        logger.info(f"Live executor initialized — wallet: {self.pubkey[:8]}..{self.pubkey[-4:]}")

    async def execute(self, signal: ConvergenceSignal, safety: SafetyResult) -> int | None:
        """Execute a real buy: SOL -> token via Jupiter."""
        amount_lamports = sol_to_lamports(self.settings.trade_amount_sol)

        try:
            # 1. Get quote
            quote = await self.jupiter.get_quote(
                SOL_MINT,
                signal.token_mint,
                amount_lamports,
                self.settings.slippage_bps,
            )
            if not quote:
                logger.error(f"No quote for {signal.token_mint[:12]}.. — skipping")
                return None

            out_amount = int(quote.get("outAmount", 0))
            if out_amount == 0:
                logger.error("Quote returned 0 output — skipping")
                return None

            # 2. Build swap transaction
            swap_tx_b64 = await self.jupiter.get_swap_transaction(quote, self.pubkey)
            if not swap_tx_b64:
                logger.error("Failed to build swap transaction — skipping")
                return None

            # 3. Sign and send
            signature = await self._sign_and_send(swap_tx_b64)
            if not signature:
                logger.error("Transaction failed — skipping")
                return None

            # 4. Calculate entry price
            entry_price = self.settings.trade_amount_sol / out_amount

            # 5. Persist position
            db = await get_db()
            cursor = await db.execute(
                """INSERT INTO positions
                   (signal_id, token_mint, token_symbol, mode, status, entry_price,
                    amount_sol, amount_tokens, buy_signature, opened_at)
                   VALUES (?, ?, ?, 'live', 'open', ?, ?, ?, ?, ?)""",
                (
                    signal.db_id,
                    signal.token_mint,
                    signal.token_symbol,
                    entry_price,
                    self.settings.trade_amount_sol,
                    out_amount,
                    signature,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
            position_id = cursor.lastrowid

            # Inverse link cs.position_id -> p.id, keyed by signal row id
            if signal.db_id is not None:
                await db.execute(
                    "UPDATE convergence_signals SET position_id=?, action_taken='live_trade' WHERE id=?",
                    (position_id, signal.db_id),
                )
                await db.commit()

            logger.info(
                f"LIVE TRADE opened: {signal.token_mint[:12]}.. | "
                f"Spent: {self.settings.trade_amount_sol} SOL | "
                f"Got: {out_amount} tokens | "
                f"Tx: {signature[:16]}.. | ID: {position_id}"
            )
            return position_id

        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            return None

    async def close_position(self, position_id: int, reason: str):
        """Sell token back to SOL via Jupiter."""
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        )
        if not rows:
            logger.error(f"Position {position_id} not found")
            return

        pos = dict(rows[0])
        token_mint = pos["token_mint"]
        amount_tokens = int(pos.get("amount_tokens", 0))

        if amount_tokens <= 0:
            logger.error(f"Position {position_id} has no tokens to sell")
            return

        try:
            # 1. Quote sell (token -> SOL)
            quote = await self.jupiter.get_quote(
                token_mint, SOL_MINT, amount_tokens, self.settings.slippage_bps
            )
            if not quote:
                logger.error(f"No sell quote for position {position_id}")
                return

            # 2. Build + sign + send
            swap_tx_b64 = await self.jupiter.get_swap_transaction(quote, self.pubkey)
            if not swap_tx_b64:
                logger.error(f"Failed to build sell tx for position {position_id}")
                return

            signature = await self._sign_and_send(swap_tx_b64)
            if not signature:
                logger.error(f"Sell transaction failed for position {position_id}")
                return

            sol_received = int(quote.get("outAmount", 0)) / 1e9
            exit_price = sol_received / amount_tokens if amount_tokens > 0 else 0
            pnl_sol = sol_received - pos["amount_sol"]
            pnl_pct = (pnl_sol / pos["amount_sol"]) * 100 if pos["amount_sol"] > 0 else 0

            await db.execute(
                """UPDATE positions SET
                   status='closed', close_reason=?, exit_price=?,
                   current_price=?, pnl_pct=?, pnl_sol=?,
                   sell_signature=?, closed_at=?, updated_at=?
                   WHERE id=?""",
                (reason, exit_price, exit_price, pnl_pct, pnl_sol,
                 signature, datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(), position_id),
            )
            await db.commit()

            logger.info(
                f"LIVE SELL: position #{position_id} closed ({reason}) | "
                f"Received: {sol_received:.4f} SOL | "
                f"P&L: {pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL) | "
                f"Tx: {signature[:16]}.."
            )

        except Exception as e:
            logger.error(f"Live sell failed for position {position_id}: {e}")

    async def _sign_and_send(self, swap_tx_b64: str) -> str | None:
        """Deserialize, sign, and send a Jupiter swap transaction."""
        try:
            raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
            signature = self.keypair.sign_message(to_bytes_versioned(raw_tx.message))
            signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])

            client = Client(self.rpc_url)
            opts = TxOpts(skip_preflight=False, preflight_commitment=Processed)
            resp = client.send_raw_transaction(bytes(signed_tx), opts=opts)

            result = json.loads(resp.to_json())
            tx_sig = result.get("result")
            if tx_sig:
                logger.info(f"Transaction sent: {tx_sig[:16]}..")
                return tx_sig
            else:
                logger.error(f"Send failed: {result}")
                return None

        except Exception as e:
            logger.error(f"Sign/send failed: {e}")
            return None
