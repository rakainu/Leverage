# SMC Trading System — Required & Recommended Tools

## TIER 1: Required Now (Free)

| Tool | What It Does | Status | Action |
|------|-------------|--------|--------|
| **Helius Free** (helius.dev) | Solana RPC + WebSocket, 30 req/s | ACTIVE | Already configured with API key |
| **Jupiter API** (jup.ag) | DEX aggregator for quotes, price checks, swap execution | ACTIVE (no key) | Get free API key at portal.jup.ag when site is back |
| **Telegram Bot** (lpwade_bot) | Push alerts to your phone | ACTIVE | Already sending messages |
| **Python 3.11+** | Runtime | ACTIVE | Installed locally |
| **SQLite** | Position & signal storage | ACTIVE | Built into Python |
| **Git/GitHub** | Version control | ACTIVE | rakainu/Leverage |

## TIER 2: Required Soon (Free or Cheap)

| Tool | What It Does | Why You Need It | Cost | Priority |
|------|-------------|-----------------|------|----------|
| **Helius Developer Plan** | 100 req/s RPC + Enhanced Transactions API + Webhooks | Free tier (30 req/s) will rate-limit when tracking 50+ wallets. Enhanced API simplifies transaction parsing. Webhooks eliminate fragile WebSocket connections entirely. | **$49/mo** | HIGH — get this when we go to 20+ wallets |
| **Jupiter API Key** | Higher rate limits on price quotes | Position manager checks prices every 15s. Free anonymous quotes will rate-limit with multiple positions. | **Free** | HIGH — register at portal.jup.ag |
| **Solana Wallet** (Phantom/CLI) | Dedicated trading wallet for live execution | Separate from your main wallet. Fund with test amount only. Never reuse for anything else. | **Free** | MEDIUM — need before Phase 7 (live trading) |
| **VPS already owned** | Hostinger VPS for 24/7 operation | Already running OpenClaw. SMC runs alongside on port 8420. | **Already paid** | MEDIUM — deploy after paper trading validates |

## TIER 3: Recommended (Makes This a Killer System)

### Data & Analytics

| Tool | What It Does | Why It's Worth It | Cost |
|------|-------------|-------------------|------|
| **Birdeye API** (birdeye.so) | Token metadata, price history, holder data, new pair alerts | More reliable than GMGN scraping. Official API with docs. Adds token age, creation time, holder count to safety checks. | Free tier / $49/mo |
| **DexScreener API** (dexscreener.com) | Trending pairs, new pools, price charts | Secondary signal source. Can cross-reference GMGN trending with DexScreener trending for higher conviction signals. | **Free** |
| **Defined.fi API** | Real-time token data, liquidity depth, holder distribution | Best-in-class data quality. Liquidity depth analysis for our Volume/Liquidity Regime Detection (future strategy). | Free tier available |
| **CoinGecko API** | Price data for tokens that get listed on CG | Backup price source. Useful for established memecoins that survived initial phase. | **Free** |

### Wallet Intelligence

| Tool | What It Does | Why It's Worth It | Cost |
|------|-------------|-------------------|------|
| **Cielo Finance** (cielo.finance) | Multi-wallet tracking dashboard | Manually validate wallets before adding to our tracked list. See full wallet history, PnL, patterns. | $30/mo |
| **Nansen** (nansen.ai) | Smart money labels, wallet profiling, PnL leaderboards | Best wallet intelligence platform. Can export profitable Solana wallets directly. Replaces GMGN scraping for wallet discovery. | $100+/mo |
| **Arkham Intelligence** (arkhamintelligence.com) | Wallet identity + entity mapping | Identify which wallets belong to same entity (dedup our watchlist). Detect coordinated wallets. | Free tier / $50/mo |

### Execution & Safety

| Tool | What It Does | Why It's Worth It | Cost |
|------|-------------|-------------------|------|
| **Jito Bundles** (jito.wtf) | MEV protection for live trades | Bundle your swap tx to avoid sandwich attacks. Critical once trading with >0.5 SOL per trade. Without this, bots will front-run your buys. | ~0.001 SOL tip/bundle |
| **RugCheck API** (rugcheck.xyz) | Automated rug pull detection | Dedicated safety API. Returns risk score, mint/freeze authority status, LP lock status, holder distribution. Replaces some of our manual safety checks with a single API call. | **Free** |
| **QuickNode** (quicknode.com) | Backup RPC endpoint | If Helius goes down, the scanner falls back automatically. RPC redundancy is critical for 24/7 operation. | $9/mo starter |

### Social & Sentiment (Future Edge)

| Tool | What It Does | Why It's Worth It | Cost |
|------|-------------|-------------------|------|
| **LunarCrush API** | Social sentiment scoring across Twitter/Reddit/Telegram | Detect Social-Price Gap: token mentions surging but price flat = early signal. Adds sentiment layer to convergence signals. | Free tier / $30/mo |
| **Twitter/X API** | Direct tweet/mention monitoring | Real-time social buzz detection. Track when KOLs mention specific tokens. | $100/mo basic |
| **TradingView** (already have) | Charts, Pine Script alerts, Fibonacci analysis | Write Pine Script to auto-detect Fib bounce setups on memecoins that survived initial pump. Adds technical entry timing to our smart money signals. | Already have |

## TIER 4: Nice-to-Have (Future Upgrades)

| Tool | What It Does | Cost |
|------|-------------|------|
| **GMGN Pro/API** | Official API access eliminates fragile scraping | Contact them |
| **Kolscan Pro** | Better leaderboard data, wallet export | TBD |
| **Dune Analytics** | Custom SQL queries on Solana data | Free / $349/mo for API |
| **MEXC API** | CEX trading for listed memecoins (0% maker fees) | **Free** |
| **ccxt** | Unified exchange API library (100+ exchanges) | **Free** (open source) |
| **Supabase** | Cloud DB for multi-device dashboard | Free tier |

---

## Priority Action Items

### Do Now (Free)
1. Register at **portal.jup.ag** for Jupiter API key (when site is back up)
2. Register at **rugcheck.xyz** — check if they have an API
3. Register at **dexscreener.com** — free API, no key needed
4. Create a **dedicated Solana wallet** for trading (Phantom or CLI)

### Do This Week ($49/mo)
5. Upgrade to **Helius Developer Plan** — the single biggest upgrade

### Do When Profitable ($30-100/mo)
6. **Birdeye API** for better token data
7. **Cielo Finance** for wallet validation
8. **Jito bundles** for MEV protection on live trades
9. **LunarCrush** for sentiment signals

### Dream Stack (when this prints money)
10. **Nansen** for the ultimate wallet intelligence
11. **Twitter/X API** for real-time social signals
12. **Arkham** for wallet entity mapping
