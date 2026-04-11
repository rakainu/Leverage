"""Solana constants and helpers."""

SOL_MINT = "So11111111111111111111111111111111111111112"
WSOL_MINT = SOL_MINT

STABLECOIN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

LAMPORTS_PER_SOL = 1_000_000_000


def lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


def is_quote_mint(mint: str) -> bool:
    """True if mint is SOL or a known stablecoin — the 'source' side of a buy."""
    return mint == SOL_MINT or mint in STABLECOIN_MINTS
