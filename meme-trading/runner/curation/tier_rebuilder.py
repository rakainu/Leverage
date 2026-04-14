"""TierRebuilder — fetch wallet trade history, compute win rate, retier wallets.

Background loop that runs once on startup (backfill) then once per day at
`weights.wallet_tier.rebuild_hour_utc` UTC. Uses Helius `getSignaturesForAddress`
to pull each wallet's recent transactions, parses them into entry/exit pairs
via FIFO matching per (wallet, mint), persists to `wallet_trades`, then
recomputes `wallet_tiers` from those stats.

v1 scope:
  - Treats each (wallet, mint) as one position from first buy to first sell.
  - Skips partial sells, multi-token swaps, and non-quote-paired trades.
  - Limits to last MAX_SIGS_PER_WALLET signatures (~30-60 days at active
    pace) and the rolling window from `weights.wallet_tier.rolling_window_days`.
  - Wallets with fewer than `a_tier_min_trades` closed pairs stay tier U
    (insufficient data, neutral 40 pts) instead of being miscategorized.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runner.cluster.wallet_registry import WalletRegistry
from runner.cluster.wallet_tier import WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger
from runner.utils.solana import is_quote_mint

logger = get_logger("runner.curation.tier_rebuilder")

MAX_SIGS_PER_WALLET = 200
SIG_BATCH_LIMIT = 100  # Helius getSignaturesForAddress max per call


@dataclass
class _TradeLeg:
    """One side of a swap (buy or sell) for a wallet in a single tx."""
    signature: str
    mint: str
    sol_delta: float        # +sol_delta = SOL received (sell). -sol_delta = SOL spent (buy).
    token_delta: float      # +token_delta = received token (buy). -token_delta = sent token (sell).
    block_time: datetime

    @property
    def is_buy(self) -> bool:
        return self.token_delta > 0 and self.sol_delta < 0

    @property
    def is_sell(self) -> bool:
        return self.token_delta < 0 and self.sol_delta > 0


@dataclass
class _Pair:
    """A buy paired with its first subsequent sell — one closed trade."""
    mint: str
    entry_price_sol: float
    exit_price_sol: float
    entry_sol: float
    exit_sol: float
    entry_time: datetime
    exit_time: datetime

    @property
    def pnl_sol(self) -> float:
        return self.exit_sol - self.entry_sol

    @property
    def is_win(self) -> bool:
        return self.pnl_sol > 0


class TierRebuilder:
    def __init__(
        self,
        db: Database,
        http: RateLimitedClient,
        registry: WalletRegistry,
        weights: WeightsLoader,
        helius_rpc_url: str,
        tier_cache: WalletTierCache,
        run_on_startup: bool = True,
    ):
        self.db = db
        self.http = http
        self.registry = registry
        self.weights = weights
        self.rpc_url = helius_rpc_url
        self.tier_cache = tier_cache
        self.run_on_startup = run_on_startup

    # ── public ─────────────────────────────────────────────────────

    async def run(self) -> None:
        logger.info("tier_rebuilder_start", run_on_startup=self.run_on_startup)
        if self.run_on_startup:
            try:
                stats = await self.rebuild_now()
                logger.info("tier_rebuilder_startup_done", **stats)
            except Exception as e:  # noqa: BLE001
                logger.error("tier_rebuilder_startup_failed", error=str(e))
        while True:
            sleep_sec = self._seconds_until_next_run()
            logger.info(
                "tier_rebuilder_scheduled",
                next_run_in_sec=int(sleep_sec),
                rebuild_hour_utc=self._rebuild_hour(),
            )
            await asyncio.sleep(sleep_sec)
            try:
                stats = await self.rebuild_now()
                logger.info("tier_rebuilder_done", **stats)
            except Exception as e:  # noqa: BLE001
                logger.error("tier_rebuilder_failed", error=str(e))

    async def verify_single_wallet(self, wallet: str) -> dict[str, Any]:
        """Run the same trade-pairing + tier math on one wallet WITHOUT writing
        it to wallet_tiers. Used by the GMGN vetting funnel (Stage 3) to check
        a candidate against our own on-chain ground truth before admitting it
        to the active pool.

        Returns {closed_trades, win_rate, pnl_sol, tier, pairs}.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=int(self.weights.get("wallet_tier.rolling_window_days", 30))
        )
        try:
            pairs = await self._gather_pairs_for_wallet(wallet, cutoff)
        except Exception as e:  # noqa: BLE001
            logger.warning("verify_single_wallet_failed", wallet=wallet, error=str(e))
            return {
                "closed_trades": 0, "win_rate": 0.0,
                "pnl_sol": 0.0, "tier": "U", "pairs": [],
            }
        n = len(pairs)
        wins = sum(1 for p in pairs if p.is_win)
        wr = wins / n if n else 0.0
        pnl_sum = sum(p.pnl_sol for p in pairs)
        a_wr = float(self.weights.get("wallet_tier.a_tier_win_rate", 0.60))
        b_wr = float(self.weights.get("wallet_tier.b_tier_win_rate", 0.35))
        min_trades = int(self.weights.get("wallet_tier.a_tier_min_trades", 5))
        if n < min_trades:
            tier = "U"
        elif wr >= a_wr:
            tier = "A"
        elif wr >= b_wr:
            tier = "B"
        else:
            tier = "C"
        return {
            "closed_trades": n, "win_rate": wr,
            "pnl_sol": pnl_sum, "tier": tier, "pairs": pairs,
        }

    async def rebuild_now(self) -> dict[str, int]:
        wallets = sorted(self.registry.active_addresses())
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=int(self.weights.get("wallet_tier.rolling_window_days", 30))
        )
        per_wallet_pairs: dict[str, list[_Pair]] = {}
        new_trade_rows = 0

        for wallet in wallets:
            try:
                pairs = await self._gather_pairs_for_wallet(wallet, cutoff)
            except Exception as e:  # noqa: BLE001
                logger.warning("wallet_pair_gather_failed", wallet=wallet, error=str(e))
                continue
            per_wallet_pairs[wallet] = pairs
            new_trade_rows += await self._persist_pairs(wallet, pairs)

        promotions, demotions = await self._retier(per_wallet_pairs)

        # Refresh the in-memory tier cache so the convergence detector uses the
        # new tiers without a process restart.
        await self.tier_cache.load()

        return {
            "wallets_processed": len(wallets),
            "wallets_with_trades": sum(1 for p in per_wallet_pairs.values() if p),
            "new_trade_rows": new_trade_rows,
            "promotions": promotions,
            "demotions": demotions,
        }

    # ── pair gathering ─────────────────────────────────────────────

    async def _gather_pairs_for_wallet(
        self, wallet: str, cutoff: datetime
    ) -> list[_Pair]:
        sigs = await self._fetch_signatures(wallet, cutoff)
        legs: list[_TradeLeg] = []
        for sig_meta in sigs:
            sig = sig_meta.get("signature")
            block_time_unix = sig_meta.get("blockTime")
            if not sig or not block_time_unix:
                continue
            block_time = datetime.fromtimestamp(block_time_unix, tz=timezone.utc)
            if block_time < cutoff:
                continue
            leg = await self._extract_leg(sig, wallet, block_time)
            if leg is not None:
                legs.append(leg)

        # FIFO match: per-mint queue of unmatched buys; pop on each sell.
        legs.sort(key=lambda x: x.block_time)
        open_buys: dict[str, list[_TradeLeg]] = defaultdict(list)
        pairs: list[_Pair] = []
        for leg in legs:
            if leg.is_buy:
                open_buys[leg.mint].append(leg)
            elif leg.is_sell and open_buys[leg.mint]:
                buy = open_buys[leg.mint].pop(0)
                # Treat as full close even if partial — v1 simplification.
                entry_sol = -buy.sol_delta
                exit_sol = leg.sol_delta
                if buy.token_delta <= 0 or leg.token_delta >= 0:
                    continue
                pairs.append(_Pair(
                    mint=leg.mint,
                    entry_price_sol=entry_sol / buy.token_delta,
                    exit_price_sol=exit_sol / (-leg.token_delta),
                    entry_sol=entry_sol,
                    exit_sol=exit_sol,
                    entry_time=buy.block_time,
                    exit_time=leg.block_time,
                ))
        return pairs

    async def _fetch_signatures(
        self, wallet: str, cutoff: datetime
    ) -> list[dict[str, Any]]:
        all_sigs: list[dict[str, Any]] = []
        before: str | None = None
        while len(all_sigs) < MAX_SIGS_PER_WALLET:
            params: list[Any] = [wallet, {"limit": SIG_BATCH_LIMIT}]
            if before:
                params[1]["before"] = before
            try:
                resp = await self.http.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getSignaturesForAddress", "params": params,
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("getSignaturesForAddress_error", wallet=wallet, error=str(e))
                break
            if resp.status_code != 200:
                break
            try:
                body = resp.json()
            except Exception:
                break
            sigs = body.get("result") or []
            if not sigs:
                break
            all_sigs.extend(sigs)
            last = sigs[-1]
            last_bt = last.get("blockTime")
            before = last.get("signature")
            if last_bt and datetime.fromtimestamp(last_bt, tz=timezone.utc) < cutoff:
                break
            if len(sigs) < SIG_BATCH_LIMIT:
                break
        return all_sigs[:MAX_SIGS_PER_WALLET]

    async def _extract_leg(
        self, signature: str, wallet: str, block_time: datetime
    ) -> _TradeLeg | None:
        """Pull one tx, return a TradeLeg if it's an unambiguous buy or sell."""
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "confirmed",
                    }],
                },
            )
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        result = data.get("result")
        if not result:
            return None
        meta = result.get("meta") or {}
        if meta.get("err") is not None:
            return None

        # Build mint→net_token_change map for the wallet.
        token_deltas: dict[str, float] = defaultdict(float)
        for entry in (meta.get("preTokenBalances") or []):
            if entry.get("owner") == wallet and entry.get("mint"):
                amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                token_deltas[entry["mint"]] -= amount
        for entry in (meta.get("postTokenBalances") or []):
            if entry.get("owner") == wallet and entry.get("mint"):
                amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                token_deltas[entry["mint"]] += amount

        # SOL leg from quote mints + native lamports.
        sol_delta = 0.0
        non_quote: list[tuple[str, float]] = []
        for mint, change in token_deltas.items():
            if abs(change) < 1e-12:
                continue
            if is_quote_mint(mint):
                sol_delta += change
            else:
                non_quote.append((mint, change))

        # Native SOL fallback via lamport balance change.
        idx = self._wallet_account_index(result, wallet)
        if idx is not None:
            pre_b = (meta.get("preBalances") or [])
            post_b = (meta.get("postBalances") or [])
            if idx < len(pre_b) and idx < len(post_b):
                fee = int(meta.get("fee") or 0)
                lamport_change = post_b[idx] - pre_b[idx]
                # On a buy, lamport_change is strongly negative (after fee).
                # On a sell, lamport_change is strongly positive.
                native_sol_delta = (lamport_change + fee) / 1_000_000_000
                if abs(native_sol_delta) > 0.001:
                    sol_delta += native_sol_delta

        if len(non_quote) != 1 or abs(sol_delta) < 1e-9:
            return None  # ambiguous or non-trade tx

        mint, token_delta = non_quote[0]
        # Only count as buy/sell if SOL and token move in opposite directions.
        if (token_delta > 0 and sol_delta < 0) or (token_delta < 0 and sol_delta > 0):
            return _TradeLeg(
                signature=signature, mint=mint, sol_delta=sol_delta,
                token_delta=token_delta, block_time=block_time,
            )
        return None

    @staticmethod
    def _wallet_account_index(result: dict, wallet: str) -> int | None:
        keys = (result.get("transaction") or {}).get("message", {}).get("accountKeys", [])
        for i, k in enumerate(keys):
            if isinstance(k, dict) and k.get("pubkey") == wallet:
                return i
            if isinstance(k, str) and k == wallet:
                return i
        return None

    # ── persistence + retiering ────────────────────────────────────

    async def _persist_pairs(self, wallet: str, pairs: list[_Pair]) -> int:
        assert self.db.conn is not None
        inserted = 0
        for p in pairs:
            try:
                # Dedupe by (wallet, mint, entry_time) — re-running the rebuild
                # shouldn't double-count.
                async with self.db.conn.execute(
                    """SELECT 1 FROM wallet_trades
                       WHERE wallet_address=? AND token_mint=? AND entry_time=?""",
                    (wallet, p.mint, p.entry_time.isoformat()),
                ) as cur:
                    if await cur.fetchone():
                        continue
                await self.db.conn.execute(
                    """INSERT INTO wallet_trades
                       (wallet_address, token_mint, entry_price_sol, exit_price_sol,
                        pnl_sol, entry_time, exit_time, is_win)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (wallet, p.mint, p.entry_price_sol, p.exit_price_sol,
                     p.pnl_sol, p.entry_time.isoformat(), p.exit_time.isoformat(),
                     1 if p.is_win else 0),
                )
                inserted += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("wallet_trade_persist_failed", wallet=wallet, error=str(e))
        await self.db.conn.commit()
        return inserted

    async def _retier(self, per_wallet_pairs: dict[str, list[_Pair]]) -> tuple[int, int]:
        assert self.db.conn is not None
        a_wr = float(self.weights.get("wallet_tier.a_tier_win_rate", 0.60))
        b_wr = float(self.weights.get("wallet_tier.b_tier_win_rate", 0.35))
        min_trades = int(self.weights.get("wallet_tier.a_tier_min_trades", 5))
        promos = 0
        demos = 0

        # Read existing tiers so we can detect promotion/demotion deltas.
        async with self.db.conn.execute(
            "SELECT wallet_address, tier FROM wallet_tiers"
        ) as cur:
            existing = {row[0]: row[1] async for row in cur}

        for wallet, pairs in per_wallet_pairs.items():
            n = len(pairs)
            wins = sum(1 for p in pairs if p.is_win)
            wr = wins / n if n else 0.0
            pnl_sum = sum(p.pnl_sol for p in pairs)

            if n < min_trades:
                new_tier = "U"
            elif wr >= a_wr:
                new_tier = "A"
            elif wr >= b_wr:
                new_tier = "B"
            else:
                new_tier = "C"

            old = existing.get(wallet)
            if old != new_tier:
                if _tier_rank(new_tier) > _tier_rank(old or "U"):
                    promos += 1
                else:
                    demos += 1

            await self.db.conn.execute(
                """INSERT INTO wallet_tiers
                   (wallet_address, tier, win_rate, trade_count, pnl_sol,
                    source, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'tier_rebuilder', CURRENT_TIMESTAMP)
                   ON CONFLICT(wallet_address) DO UPDATE SET
                   tier=excluded.tier, win_rate=excluded.win_rate,
                   trade_count=excluded.trade_count, pnl_sol=excluded.pnl_sol,
                   source='tier_rebuilder', updated_at=CURRENT_TIMESTAMP""",
                (wallet, new_tier, wr, n, pnl_sum),
            )

        await self.db.conn.commit()
        return promos, demos

    # ── scheduling ─────────────────────────────────────────────────

    def _rebuild_hour(self) -> int:
        return int(self.weights.get("wallet_tier.rebuild_hour_utc", 4))

    def _seconds_until_next_run(self) -> float:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=self._rebuild_hour(), minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max((target - now).total_seconds(), 60.0)


def _tier_rank(label: str) -> int:
    return {"C": 0, "U": 1, "B": 2, "A": 3}.get(label, 1)
