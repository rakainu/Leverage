"""Constants for the SMC trading system."""

# Solana native SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"

# Known DEX program IDs
DEX_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcPX7rE": "jupiter",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "raydium_cpmm",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "orca",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump_fun",
}

# Known burn / dead addresses (LP tokens sent here = "locked")
BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}

# GMGN API base
GMGN_BASE_URL = "https://gmgn.ai"

# Jupiter API
JUPITER_API_URL = "https://api.jup.ag"

# Helius Enhanced API
HELIUS_API_URL = "https://api.helius.xyz"
