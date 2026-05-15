"""init schema

Revision ID: 0001
Revises:
Create Date: 2026-05-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("address", sa.String(64), primary_key=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(32), nullable=False, server_default="leaderboard"),
        sa.Column("style", sa.String(16)),
        sa.Column("current_score", sa.Numeric(5, 2)),
        sa.Column("trade_count", sa.Integer),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "fills",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("wallet_address", sa.String(64), sa.ForeignKey("wallets.address", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("px", sa.Numeric(20, 8), nullable=False),
        sa.Column("sz", sa.Numeric(20, 8), nullable=False),
        sa.Column("start_position", sa.Numeric(20, 8)),
        sa.Column("hash", sa.String(80), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("closed_pnl", sa.Numeric(20, 8)),
        sa.UniqueConstraint("wallet_address", "hash", name="uq_fills_wallet_hash"),
    )
    op.create_index("ix_fills_wallet_ts", "fills", ["wallet_address", "ts"])
    op.create_index("ix_fills_coin_ts", "fills", ["coin", "ts"])

    op.create_table(
        "hl_positions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("wallet_address", sa.String(64), sa.ForeignKey("wallets.address", ondelete="CASCADE"), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("entry_px", sa.Numeric(20, 8), nullable=False),
        sa.Column("exit_px", sa.Numeric(20, 8)),
        sa.Column("sz", sa.Numeric(20, 8), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 8)),
        sa.Column("realized_pnl_pct", sa.Numeric(10, 4)),
        sa.Column("hold_seconds", sa.Integer),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
    )
    op.create_index("ix_hl_positions_wallet_coin_opened", "hl_positions", ["wallet_address", "coin", "opened_at"])
    op.create_index("ix_hl_positions_coin_status", "hl_positions", ["coin", "status"])

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("wallet_address", sa.String(64), sa.ForeignKey("wallets.address"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("sz_after", sa.Numeric(20, 8), server_default="0"),
        sa.Column("px", sa.Numeric(20, 8)),
        sa.Column("raw_payload", sa.Text),
    )
    op.create_index("ix_events_coin_ts", "events", ["coin", "ts"])
    op.create_index("ix_events_kind_ts", "events", ["kind", "ts"])

    op.create_table(
        "scores_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("wallet_address", sa.String(64), sa.ForeignKey("wallets.address", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("composite", sa.Numeric(5, 2), nullable=False),
        sa.Column("sharpe_proxy", sa.Numeric(8, 4)),
        sa.Column("max_dd_pct", sa.Numeric(8, 4)),
        sa.Column("win_rate", sa.Numeric(5, 4)),
        sa.Column("sample_size", sa.Integer),
        sa.Column("avg_hold_seconds", sa.Integer),
        sa.Column("recency_weight", sa.Numeric(5, 4)),
        sa.Column("passes_anti_fluke", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("fluke_reason", sa.String(64)),
        sa.UniqueConstraint("wallet_address", "snapshot_date", name="uq_scores_wallet_date"),
    )
    op.create_index("ix_scores_date_score", "scores_history", ["snapshot_date", "composite"])

    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("wallet_count", sa.Integer, nullable=False),
        sa.Column("wallet_addresses", sa.Text, nullable=False),
        sa.Column("score_floor_used", sa.Numeric(5, 2), nullable=False),
        sa.Column("window_seconds", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(128)),
    )
    op.create_index("ix_signals_ts", "signals", ["fired_at"])
    op.create_index("ix_signals_coin_side", "signals", ["coin", "side"])

    op.create_table(
        "paper_positions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.BigInteger, sa.ForeignKey("signals.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("venue_order_id", sa.String(64)),
        sa.Column("venue_sl_order_id", sa.String(64)),
        sa.Column("venue_tp_order_id", sa.String(64)),
        sa.Column("coin", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("margin_usdt", sa.Numeric(20, 8), nullable=False),
        sa.Column("leverage", sa.Integer, nullable=False),
        sa.Column("notional_usdt", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_px", sa.Numeric(20, 8), nullable=False),
        sa.Column("sl_px", sa.Numeric(20, 8), nullable=False),
        sa.Column("tp_px", sa.Numeric(20, 8), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("exit_px", sa.Numeric(20, 8)),
        sa.Column("realized_pnl_usdt", sa.Numeric(20, 8)),
        sa.Column("realized_pnl_pct", sa.Numeric(10, 4)),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("close_reason", sa.String(16)),
        sa.CheckConstraint(
            "close_reason IN (NULL, 'sl', 'tp', 'wallet_exit', 'breaker', 'drain', 'manual', 'error')",
            name="ck_paper_positions_close_reason",
        ),
    )
    op.create_index("ix_paper_positions_status", "paper_positions", ["status"])
    op.create_index("ix_paper_positions_opened", "paper_positions", ["opened_at"])

    op.create_table(
        "runtime_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("runtime_state")
    op.drop_index("ix_paper_positions_opened", table_name="paper_positions")
    op.drop_index("ix_paper_positions_status", table_name="paper_positions")
    op.drop_table("paper_positions")
    op.drop_index("ix_signals_coin_side", table_name="signals")
    op.drop_index("ix_signals_ts", table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_scores_date_score", table_name="scores_history")
    op.drop_table("scores_history")
    op.drop_index("ix_events_kind_ts", table_name="events")
    op.drop_index("ix_events_coin_ts", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_hl_positions_coin_status", table_name="hl_positions")
    op.drop_index("ix_hl_positions_wallet_coin_opened", table_name="hl_positions")
    op.drop_table("hl_positions")
    op.drop_index("ix_fills_coin_ts", table_name="fills")
    op.drop_index("ix_fills_wallet_ts", table_name="fills")
    op.drop_table("fills")
    op.drop_table("wallets")
