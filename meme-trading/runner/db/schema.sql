-- Runner intelligence DB schema (Phase 1-3 tables)

-- Raw buy events from wallet monitor.
CREATE TABLE IF NOT EXISTS buy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL UNIQUE,
    wallet_address TEXT NOT NULL,
    token_mint TEXT NOT NULL,
    sol_amount REAL NOT NULL,
    token_amount REAL NOT NULL,
    price_sol REAL NOT NULL,
    block_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_buy_events_mint_time ON buy_events(token_mint, block_time);
CREATE INDEX IF NOT EXISTS idx_buy_events_wallet_time ON buy_events(wallet_address, block_time);

-- Wallet tiers rebuilt nightly.
CREATE TABLE IF NOT EXISTS wallet_tiers (
    wallet_address TEXT PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('A', 'B', 'C', 'U')),
    win_rate REAL,
    trade_count INTEGER DEFAULT 0,
    pnl_sol REAL DEFAULT 0,
    source TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wallet_tiers_tier ON wallet_tiers(tier);

-- Flattened wallet trade history used by tier rebuilder.
CREATE TABLE IF NOT EXISTS wallet_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    token_mint TEXT NOT NULL,
    entry_price_sol REAL NOT NULL,
    exit_price_sol REAL,
    pnl_sol REAL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    is_win INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wallet_trades_wallet ON wallet_trades(wallet_address, entry_time);

-- Detected cluster signals (N A+B wallets within window).
CREATE TABLE IF NOT EXISTS cluster_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    wallet_count INTEGER NOT NULL,
    wallets_json TEXT NOT NULL,
    tier_counts_json TEXT NOT NULL,
    first_buy_time TIMESTAMP NOT NULL,
    last_buy_time TIMESTAMP NOT NULL,
    convergence_seconds INTEGER NOT NULL,
    mid_price_sol REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cluster_signals_mint ON cluster_signals(token_mint);
CREATE INDEX IF NOT EXISTS idx_cluster_signals_time ON cluster_signals(created_at);

-- Schema migration marker.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);
