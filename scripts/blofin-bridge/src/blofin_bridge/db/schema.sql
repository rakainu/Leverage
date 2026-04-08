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
