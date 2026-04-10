CREATE TABLE IF NOT EXISTS buy_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    token_mint TEXT NOT NULL,
    token_symbol TEXT,
    amount_sol REAL NOT NULL,
    amount_tokens REAL,
    signature TEXT UNIQUE NOT NULL,
    dex TEXT,
    timestamp DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_buy_events_token_time ON buy_events(token_mint, timestamp);
CREATE INDEX IF NOT EXISTS idx_buy_events_wallet_time ON buy_events(wallet_address, timestamp);

CREATE TABLE IF NOT EXISTS convergence_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    token_symbol TEXT,
    wallet_count INTEGER NOT NULL,
    wallets_json TEXT NOT NULL,
    first_buy_at DATETIME NOT NULL,
    signal_at DATETIME NOT NULL,
    avg_amount_sol REAL,
    total_amount_sol REAL,
    safety_passed INTEGER,
    safety_details_json TEXT,
    action_taken TEXT,
    position_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signals_time ON convergence_signals(signal_at DESC);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES convergence_signals(id),
    token_mint TEXT NOT NULL,
    token_symbol TEXT,
    mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
    entry_price REAL NOT NULL,
    current_price REAL,
    exit_price REAL,
    amount_sol REAL NOT NULL,
    amount_tokens REAL,
    pnl_pct REAL,
    pnl_sol REAL,
    close_reason TEXT,
    price_1h REAL,
    price_4h REAL,
    price_24h REAL,
    pnl_1h_pct REAL,
    pnl_4h_pct REAL,
    pnl_24h_pct REAL,
    high_watermark_pct REAL DEFAULT 0.0,
    buy_signature TEXT,
    sell_signature TEXT,
    opened_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, mode);

CREATE TABLE IF NOT EXISTS tracked_wallets (
    address TEXT PRIMARY KEY,
    label TEXT,
    source TEXT DEFAULT 'manual',
    score REAL DEFAULT 0.0,
    total_trades INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0.0,
    total_pnl_sol REAL DEFAULT 0.0,
    avg_hold_minutes REAL,
    active INTEGER DEFAULT 1,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    signals_count INTEGER DEFAULT 0,
    trades_count INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    total_pnl_sol REAL DEFAULT 0.0,
    avg_pnl_pct REAL DEFAULT 0.0,
    best_trade_pnl_pct REAL,
    worst_trade_pnl_pct REAL
);
