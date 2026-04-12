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

-- Filter pipeline results — one row per (candidate, filter) pair.
CREATE TABLE IF NOT EXISTS filter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    hard_fail_reason TEXT,
    sub_scores_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    cluster_signal_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_filter_results_mint ON filter_results(token_mint);
CREATE INDEX IF NOT EXISTS idx_filter_results_cluster ON filter_results(cluster_signal_id);

-- Final Runner Score + Verdict — one row per candidate (populated by scoring engine in Plan 2c).
CREATE TABLE IF NOT EXISTS runner_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    cluster_signal_id INTEGER,
    runner_score REAL NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('ignore', 'watch', 'strong_candidate', 'probable_runner')),
    short_circuited INTEGER DEFAULT 0,
    sub_scores_json TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runner_scores_mint ON runner_scores(token_mint);
CREATE INDEX IF NOT EXISTS idx_runner_scores_verdict ON runner_scores(verdict);
CREATE INDEX IF NOT EXISTS idx_runner_scores_time ON runner_scores(created_at);

-- Paper positions — one per scored candidate that reached execution threshold.
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    symbol TEXT,
    runner_score_id INTEGER NOT NULL REFERENCES runner_scores(id),
    verdict TEXT NOT NULL,
    runner_score REAL NOT NULL,
    entry_price_sol REAL NOT NULL,
    entry_price_usd REAL,
    amount_sol REAL NOT NULL,
    signal_time TIMESTAMP NOT NULL,
    entry_source TEXT NOT NULL DEFAULT 'paper_executor_v1',
    price_5m_sol REAL, pnl_5m_pct REAL,
    price_30m_sol REAL, pnl_30m_pct REAL,
    price_1h_sol REAL, pnl_1h_pct REAL,
    price_4h_sol REAL, pnl_4h_pct REAL,
    price_24h_sol REAL, pnl_24h_pct REAL,
    max_favorable_pct REAL DEFAULT 0.0,
    max_adverse_pct REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    close_reason TEXT CHECK (close_reason IN ('completed', 'error')),
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    notes_json TEXT,
    UNIQUE(runner_score_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_mint ON paper_positions(token_mint);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_verdict ON paper_positions(verdict);

-- Schema migration marker.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);
