"""Safety checks for tokens before trading — honeypot, mint auth, LP lock, etc."""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from config.settings import Settings

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
        """Run RPC checks + RugCheck in parallel, return aggregated result."""
        results = await asyncio.gather(
            self._check_mint_authority(token_mint),
            self._check_freeze_authority(token_mint),
            self._check_rugcheck(token_mint),
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

        # RugCheck (covers honeypot, top holders, LP lock)
        if isinstance(results[2], Exception):
            result.reasons.append(f"rugcheck failed: {results[2]}")
            result.honeypot_risk = "unknown"
        else:
            rc = results[2]
            result.honeypot_risk = rc["risk_level"]
            result.top_holder_pct = rc.get("top_holder_pct", 0)
            for reason in rc.get("reasons", []):
                result.reasons.append(reason)

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

    async def _check_rugcheck(self, mint: str) -> dict:
        """Check token via RugCheck API — covers honeypot, holders, LP lock."""
        try:
            resp = await self.http.get(
                f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary",
                timeout=10,
            )
            if resp.status_code != 200:
                raise ValueError(f"RugCheck returned {resp.status_code}")

            data = resp.json()
            score = data.get("score_normalised", 100)
            risks = data.get("risks") or []
            lp_locked = data.get("lpLockedPct", 0)

            reasons = []
            danger_risks = [r for r in risks if r.get("level") == "danger"]

            for r in danger_risks:
                reasons.append(f"[rugcheck] {r['name']}: {r.get('description', '')}")

            # Determine risk level from normalized score
            if score > 50 or len(danger_risks) >= 2:
                risk_level = "high"
            elif score > 30 or len(danger_risks) == 1:
                risk_level = "medium"
            else:
                risk_level = "low"

            # Only fail on high risk
            if risk_level != "high":
                reasons = []

            logger.info(
                f"RugCheck {mint[:12]}.. score={score}/100 "
                f"dangers={len(danger_risks)} lp_locked={lp_locked:.0%} → {risk_level}"
            )

            return {
                "risk_level": risk_level,
                "score": score,
                "reasons": reasons,
                "top_holder_pct": 0,  # RugCheck doesn't return this in summary
                "lp_locked_pct": lp_locked,
            }

        except Exception as e:
            logger.warning(f"RugCheck failed for {mint[:12]}..: {e}")
            # Don't block trades when RugCheck is unreachable
            return {
                "risk_level": "unknown",
                "score": -1,
                "reasons": [],
                "top_holder_pct": 0,
                "lp_locked_pct": 0,
            }
