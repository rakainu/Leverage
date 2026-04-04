"""Safety checks for tokens before trading — honeypot, mint auth, LP lock, etc."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from config.settings import Settings
from utils.constants import SOL_MINT

logger = logging.getLogger("smc.engine.safety")


@dataclass
class SafetyResult:
    """Aggregated result of all safety checks."""
    passed: bool = False
    mint_authority_revoked: bool = False
    freeze_authority_revoked: bool = False
    lp_locked: bool = False
    honeypot_risk: str = "unknown"  # "low" | "medium" | "high"
    top_holder_pct: float = 100.0
    reasons: list[str] = field(default_factory=list)


class SafetyChecker:
    """Runs parallel safety checks on a token mint before allowing a trade."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http
        self.rpc_url = settings.solana_rpc_urls[0]

    async def check(self, token_mint: str) -> SafetyResult:
        """Run all checks in parallel, return aggregated result."""
        results = await asyncio.gather(
            self._check_mint_authority(token_mint),
            self._check_freeze_authority(token_mint),
            self._check_honeypot(token_mint),
            self._check_top_holders(token_mint),
            return_exceptions=True,
        )

        result = SafetyResult()

        # Mint authority
        if isinstance(results[0], Exception):
            result.reasons.append(f"mint_auth check failed: {results[0]}")
        else:
            result.mint_authority_revoked = results[0]
            if self.settings.require_no_mint_authority and not results[0]:
                result.reasons.append("mint authority still active")

        # Freeze authority
        if isinstance(results[1], Exception):
            result.reasons.append(f"freeze_auth check failed: {results[1]}")
        else:
            result.freeze_authority_revoked = results[1]
            if not results[1]:
                result.reasons.append("freeze authority still active")

        # Honeypot
        if isinstance(results[2], Exception):
            result.reasons.append(f"honeypot check failed: {results[2]}")
            result.honeypot_risk = "unknown"
        else:
            result.honeypot_risk = results[2]
            if results[2] == "high":
                result.reasons.append("high honeypot risk (sell tax or unsellable)")

        # Top holders
        if isinstance(results[3], Exception):
            result.reasons.append(f"holder check failed: {results[3]}")
        else:
            result.top_holder_pct = results[3]
            if results[3] > self.settings.max_top_holder_pct:
                result.reasons.append(
                    f"top holder owns {results[3]:.1f}% (max {self.settings.max_top_holder_pct}%)"
                )

        result.passed = len(result.reasons) == 0

        if result.passed:
            logger.info(f"Safety PASSED for {token_mint[:12]}.. — all checks clear")
        else:
            logger.warning(
                f"Safety FAILED for {token_mint[:12]}.. — {', '.join(result.reasons)}"
            )

        return result

    async def _rpc_call(self, method: str, params: list) -> dict | None:
        """Make an RPC call and return the result."""
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=15,
            )
            data = resp.json()
            return data.get("result")
        except Exception as e:
            logger.error(f"RPC {method} failed: {e}")
            return None

    async def _check_mint_authority(self, mint: str) -> bool:
        """Check if mint authority has been revoked (safe = True)."""
        result = await self._rpc_call(
            "getAccountInfo",
            [mint, {"encoding": "jsonParsed"}],
        )
        if not result or not result.get("value"):
            raise ValueError("Could not fetch mint account")

        parsed = result["value"].get("data", {}).get("parsed", {})
        info = parsed.get("info", {})
        mint_authority = info.get("mintAuthority")
        return mint_authority is None

    async def _check_freeze_authority(self, mint: str) -> bool:
        """Check if freeze authority has been revoked (safe = True)."""
        result = await self._rpc_call(
            "getAccountInfo",
            [mint, {"encoding": "jsonParsed"}],
        )
        if not result or not result.get("value"):
            raise ValueError("Could not fetch mint account")

        parsed = result["value"].get("data", {}).get("parsed", {})
        info = parsed.get("info", {})
        freeze_authority = info.get("freezeAuthority")
        return freeze_authority is None

    async def _check_honeypot(self, mint: str) -> str:
        """Simulate buy+sell via Jupiter quote to detect sell tax.

        Returns: "low", "medium", or "high" risk.
        """
        test_amount = 100_000_000  # 0.1 SOL in lamports

        # Quote buy (SOL -> token)
        buy_quote = await self._jupiter_quote(SOL_MINT, mint, test_amount)
        if not buy_quote:
            return "high"  # Can't even get a buy quote

        out_amount = int(buy_quote.get("outAmount", 0))
        if out_amount == 0:
            return "high"

        # Quote sell (token -> SOL)
        sell_quote = await self._jupiter_quote(mint, SOL_MINT, out_amount)
        if not sell_quote:
            return "high"  # Can't sell = honeypot

        sell_return = int(sell_quote.get("outAmount", 0))

        # Calculate round-trip loss
        loss_pct = (1 - sell_return / test_amount) * 100

        if loss_pct > self.settings.honeypot_max_tax_pct:
            return "high"
        elif loss_pct > 5:
            return "medium"
        return "low"

    async def _check_top_holders(self, mint: str) -> float:
        """Get the largest holder's percentage of total supply."""
        result = await self._rpc_call(
            "getTokenLargestAccounts", [mint]
        )
        if not result or not result.get("value"):
            raise ValueError("Could not fetch token holders")

        accounts = result["value"]
        if not accounts:
            return 100.0

        # Get total supply
        supply_result = await self._rpc_call(
            "getTokenSupply", [mint]
        )
        if not supply_result or not supply_result.get("value"):
            raise ValueError("Could not fetch token supply")

        total_supply = float(supply_result["value"]["uiAmount"])
        if total_supply == 0:
            return 100.0

        largest_amount = float(accounts[0].get("uiAmount", 0))
        return (largest_amount / total_supply) * 100

    async def _jupiter_quote(self, input_mint: str, output_mint: str, amount: int) -> dict | None:
        """Get a Jupiter quote for price simulation."""
        try:
            resp = await self.http.get(
                "https://quote-api.jup.ag/v6/quote",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                    "slippageBps": "500",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Jupiter quote failed: {e}")
        return None
