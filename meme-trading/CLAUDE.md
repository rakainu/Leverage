# SMC — Smart Money Convergence Trading System

Solana memecoin trading system that detects when multiple profitable wallets converge on the same token and trades accordingly.

## How It Works

1. **Scanner** monitors 50-100 curated wallet addresses via Solana WebSocket (Helius RPC)
2. **Convergence Engine** detects when 3+ tracked wallets buy the same token within 60 minutes
3. **Safety Checker** validates token: mint authority revoked, freeze authority revoked, honeypot simulation (Jupiter buy/sell), top holder concentration
4. **Executor** opens paper or live trades via Jupiter Swap API
5. **Position Manager** monitors open positions every 15s for TP/SL/timeout exits
6. **Dashboard** shows everything at http://localhost:8420 via FastAPI + WebSocket
7. **Telegram** pushes alerts to @radk9 via lpwade_bot

## Running

```bash
cd meme-trading
pip install -r requirements.txt
cp .env.example .env  # Edit with real keys
python main.py
```

Dashboard: http://localhost:8420

## Configuration (.env with SMC_ prefix)

Key settings:
- `SMC_MODE` — "paper" (default) or "live"
- `SMC_CONVERGENCE_THRESHOLD` — min wallets for signal (default: 3)
- `SMC_CONVERGENCE_WINDOW_MINUTES` — sliding window (default: 60)
- `SMC_TRADE_AMOUNT_SOL` — per-trade size (default: 0.1)
- `SMC_TAKE_PROFIT_PCT` / `SMC_STOP_LOSS_PCT` — exit thresholds
- `SMC_POSITION_TIMEOUT_MINUTES` — max hold time (default: 240)

## Architecture

```
WalletMonitor (Solana WSS) → event_bus → ConvergenceEngine → signal_bus → signal_router
  → SafetyChecker → PaperExecutor/LiveExecutor → PositionManager
  → alert_fanout → TelegramAlerter + WebSocketManager (dashboard)
```

All async via asyncio.Queue message buses. SQLite (WAL mode) as single source of truth.

## Project Structure

```
meme-trading/
├── main.py                    # Entry point — asyncio.gather all services
├── config/settings.py         # Pydantic Settings, .env with SMC_ prefix
├── config/wallets.json        # Tracked wallet addresses (manual + auto)
├── db/schema.sql              # SQLite tables: buy_events, signals, positions, wallets, stats
├── db/database.py             # aiosqlite singleton, WAL mode
├── scanner/wallet_monitor.py  # Solana WS logsSubscribe, chunked connections
├── scanner/transaction_parser.py  # Decode swap txns into BuyEvent
├── scanner/rpc_pool.py        # Round-robin RPC with health tracking
├── engine/convergence.py      # Sliding window detection, dedup
├── engine/safety.py           # Honeypot sim, mint/freeze auth, holder checks
├── engine/signal.py           # BuyEvent + ConvergenceSignal dataclasses
├─�� executor/jupiter.py        # Jupiter Swap API client
├── executor/paper.py          # Paper trading with 1h/4h/24h snapshots
├── executor/position_manager.py  # TP/SL/timeout monitoring
├── executor/live.py           # Real Jupiter swap execution (Phase 7)
├── curation/discovery.py      # GMGN wallet scraper (Phase 6)
├── curation/scorer.py         # Wallet scoring 0-100 (Phase 6)
├── curation/pipeline.py       # Auto wallet refresh (Phase 6)
├── dashboard/app.py           # FastAPI REST + WebSocket
├── dashboard/static/index.html  # Single-file dashboard (Tailwind + vanilla JS)
├── alerts/telegram.py         # Push notifications via lpwade_bot
└── utils/                     # Logging, constants, Solana helpers
```

## External Services

- **Helius** (helius.dev) — Solana RPC + WebSocket, Enhanced Transactions API
- **Jupiter** (jup.ag) — DEX aggregator for quotes, swaps, price checks
- **Telegram** — lpwade_bot for push alerts to @radk9

## Wallet Management

`config/wallets.json` is the wallet registry:
- `"source": "manual"` — never auto-removed, you control these
- `"source": "auto"` — added/deactivated by curation pipeline based on score
- Reloaded every 5 minutes by the scanner

## Build Status

- [x] Phase 1: Foundation (config, DB, logging)
- [x] Phase 2: Scanner (Helius WS, transaction parser)
- [x] Phase 3: Convergence Engine + Safety Checks
- [x] Phase 4: Paper Trading + Position Management
- [x] Phase 5: Dashboard + Telegram Alerts
- [x] Phase 6: Wallet Curation (Birdeye + Helius + Nansen discovery, scoring, auto-refresh)
- [x] Phase 7: Live Trading (Jupiter swap) + Docker + VPS Deploy

## Going Live

1. Export Phantom private key → set `SMC_SOLANA_PRIVATE_KEY` in `.env`
2. Fund wallet `GGR8UN1skyNV3ZfSZ96jgSu7tQGYSCzi6vhm3i1ZHJop` with 0.5-1 SOL
3. Set `SMC_MODE=live` in `.env`
4. Restart: `python main.py` (local) or `docker compose up -d` (VPS)

## VPS Deploy

```bash
ssh root@46.202.146.30
cd /opt/smc-trading
git clone https://github.com/rakainu/Leverage.git . || git pull
cd meme-trading
cp .env.example .env && nano .env  # Add real keys
docker compose up -d --build
# Dashboard: http://46.202.146.30:8420
```
