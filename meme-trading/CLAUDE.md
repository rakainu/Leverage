# Meme Trading

Solana memecoin spot trading system — fast in-and-out trades on trending tokens.

## Strategy

- **Approach:** Spot trading (no/minimal leverage), quick entries and exits on trending memecoins
- **Signal Source:** GMGN.ai trending token detection + smart money tracking
- **Execution:** Jupiter Swap API (Solana DEX aggregator) for on-chain trades, MEXC for CEX option
- **Position Management:** Profit targets, stop-losses, time-based exits

## Data Sources

### GMGN.ai
- Multi-chain memecoin trading terminal (Solana, ETH, Base, BSC, Tron, Blast, Monad)
- Smart money wallet tracking, copy trading, token sniping
- 400+ parameter analysis for token scoring
- Anti-MEV protection, honeypot detection, liquidity checks
- 1% per-trade fee, no subscription
- API available for price data and trending tokens
- Platforms: web, Chrome extension, Telegram bot, Android app

### MEXC
- 2,939+ trading pairs including memecoins (PUMP/USDT etc.)
- Spot fees: 0% maker / 0.05% taker (industry lowest)
- Futures fees: 0.01% maker / 0.05% taker
- API: REST + WebSocket, SDKs in Python, JS, Java, Go, .NET
- KYC required for API futures trading
- Good for CEX-based memecoin trading if tokens are listed

## Architecture Reference (from Swiper bot analysis)

Swiper (github.com/shakeebshams/swiper) is the closest open-source reference:
- Python, Jupiter Swap, Supabase for position tracking
- Buy module: GMGN trending scan -> age filter (<60s) -> Jupiter swap
- Sell module: poll positions, exit on profit (2.5%), stop-loss (50%), or timeout (5 min)
- Limitations: scrapes GMGN via curl (fragile), no real sell execution, hardcoded values

## Tech Stack

- **Language:** Python
- **DEX:** Jupiter Swap API (Solana)
- **CEX (optional):** MEXC via REST API or ccxt
- **Data:** GMGN.ai API for trending/price data
- **Storage:** Supabase or local SQLite for position tracking
- **Wallet:** Solana keypair (base58), dedicated trading wallet with limited funds

## Key Dependencies

```
solana, solders, base58          # Solana blockchain interaction
requests, httpx                   # API calls
python-dotenv                     # Config management
supabase / sqlite3                # Position storage
ccxt                              # Exchange connectivity (MEXC, others)
```

## Risk Controls

- Dedicated wallet with capped funds (never main wallet)
- Max concurrent positions limit
- Per-trade SOL amount cap
- Mandatory stop-loss on every position
- Time-based exit to avoid bag-holding
- Honeypot/rug detection before buying
- No private keys in version control

## Project Structure (planned)

```
meme-trading/
  scanner/          # Token discovery & trending detection
  trader/           # Buy/sell execution via Jupiter or MEXC
  positions/        # Position tracking & management
  safety/           # Honeypot detection, rug checks, risk limits
  config/           # Environment config, pair lists
```
