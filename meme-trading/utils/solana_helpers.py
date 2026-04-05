"""Solana wallet and transaction helpers."""

import logging

import base58
from solders.keypair import Keypair

logger = logging.getLogger("smc.utils.solana")


def load_keypair(private_key_b58: str) -> Keypair:
    """Load a Solana keypair from a base58-encoded private key string."""
    if not private_key_b58:
        raise ValueError("No private key provided. Set SMC_SOLANA_PRIVATE_KEY in .env")
    try:
        key_bytes = base58.b58decode(private_key_b58)
        return Keypair.from_bytes(key_bytes)
    except Exception as e:
        raise ValueError(f"Invalid private key: {e}") from e


def lamports_to_sol(lamports: int) -> float:
    """Convert lamports to SOL."""
    return lamports / 1_000_000_000


def sol_to_lamports(sol: float) -> int:
    """Convert SOL to lamports."""
    return int(sol * 1_000_000_000)


def short_address(address: str, chars: int = 4) -> str:
    """Shorten a Solana address for display."""
    if len(address) <= chars * 2 + 2:
        return address
    return f"{address[:chars]}..{address[-chars:]}"
