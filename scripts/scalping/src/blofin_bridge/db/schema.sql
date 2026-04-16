CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('long','short')),
    entry_price     REAL NOT NULL,
    initial_size    REAL NOT NULL,
    current_size    REAL NOT NULL,
    tp_stage        INTEGER NOT NULL DEFAULT 0,
    tp1_fill_price  REAL,
    tp2_fill_price  REAL,
    sl_order_id     TEXT,
    tp1_order_id    TEXT,
    tp2_order_id    TEXT,
    tp3_order_id    TEXT,
    sl_distance     REAL,
    atr_value       REAL,
    trail_high_price REAL,
    trail_active    INTEGER NOT NULL DEFAULT 0,
    sl_policy       TEXT NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    realized_pnl    REAL,
    source          TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol_open
    ON positions (symbol) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER REFERENCES positions(id),
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    handled_at  TEXT,
    outcome     TEXT,
    error_msg   TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_received
    ON events (received_at);

CREATE TABLE IF NOT EXISTS trade_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER REFERENCES positions(id),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL,
    margin_usdt     REAL NOT NULL,
    leverage        REAL NOT NULL,
    initial_sl      REAL,
    tp_ceiling      REAL,
    trail_activated INTEGER NOT NULL DEFAULT 0,
    trail_high_price REAL,
    exit_reason     TEXT,       -- 'sl', 'trail_sl', 'tp_ceiling', 'manual', 'drift'
    pnl_usdt       REAL,
    pnl_pct        REAL,       -- percent of margin
    opened_at      TEXT NOT NULL,
    closed_at      TEXT NOT NULL,
    duration_secs  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_trade_log_symbol
    ON trade_log (symbol);
CREATE INDEX IF NOT EXISTS idx_trade_log_closed
    ON trade_log (closed_at);

CREATE TABLE IF NOT EXISTS pending_signals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol               TEXT NOT NULL,
    action               TEXT NOT NULL,
    signal_price         REAL NOT NULL,
    created_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
    filled_at            TEXT,
    fill_price           REAL,
    -- Snapshot captured at webhook receipt for later revalidation
    signal_timeframe     TEXT,
    signal_candle_high   REAL,
    signal_candle_low    REAL,
    signal_ema_value     REAL,
    signal_ema_slope     REAL,
    signal_atr           REAL,
    signal_bar_ts        INTEGER,
    -- Per-signal copies of invalidation limits (snapshot of config at receipt)
    max_age_seconds      INTEGER,
    max_bars             INTEGER,
    -- Reason code when status != 'pending'/'filled' (e.g. invalidated_structure_break)
    cancel_reason        TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_status
    ON pending_signals (status) WHERE status = 'pending';
