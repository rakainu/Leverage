"""ORM models. One module so Alembic autogenerate and tests can see the full schema."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

# 64-bit on Postgres, 32-bit on SQLite (so AUTOINCREMENT works for tests)
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Wallet(Base):
    """A Hyperliquid wallet we track. Discovered from leaderboard; scored on a rolling basis."""

    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), default="leaderboard", nullable=False)
    style: Mapped[str | None] = mapped_column(String(16))  # scalper | swing | positional
    current_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    trade_count: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)

    fills: Mapped[list["Fill"]] = relationship(back_populates="wallet", cascade="all, delete-orphan")
    scores: Mapped[list["ScoreHistory"]] = relationship(back_populates="wallet", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="wallet")


class Fill(Base):
    """A single Hyperliquid fill. Idempotent on (wallet, hash) per HL fill semantics."""

    __tablename__ = "fills"
    __table_args__ = (
        UniqueConstraint("wallet_address", "hash", name="uq_fills_wallet_hash"),
        Index("ix_fills_wallet_ts", "wallet_address", "ts"),
        Index("ix_fills_coin_ts", "coin", "ts"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # buy | sell
    direction: Mapped[str] = mapped_column(String(32), nullable=False)  # open_long | close_long | open_short | close_short | other HL dir strings
    px: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    sz: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    start_position: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    hash: Mapped[str] = mapped_column(String(80), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    closed_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))

    wallet: Mapped[Wallet] = relationship(back_populates="fills")


class HlPosition(Base):
    """Reconstructed position lifecycle for a wallet. One row per open/close cycle."""

    __tablename__ = "hl_positions"
    __table_args__ = (
        Index("ix_hl_positions_wallet_coin_opened", "wallet_address", "coin", "opened_at"),
        Index("ix_hl_positions_coin_status", "coin", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address", ondelete="CASCADE"), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_px: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exit_px: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    sz: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    realized_pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    hold_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(8), default="open", nullable=False)  # open | closed


class Event(Base):
    """Live position-change events streamed from HL WS. Triggers convergence detection."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_coin_ts", "coin", "ts"),
        Index("ix_events_kind_ts", "kind", "ts"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # open | close | resize | flip
    sz_after: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    px: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    raw_payload: Mapped[str | None] = mapped_column(Text)

    wallet: Mapped[Wallet] = relationship(back_populates="events")


class ScoreHistory(Base):
    """Daily snapshot of each wallet's composite score + components."""

    __tablename__ = "scores_history"
    __table_args__ = (
        UniqueConstraint("wallet_address", "snapshot_date", name="uq_scores_wallet_date"),
        Index("ix_scores_date_score", "snapshot_date", "composite"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(ForeignKey("wallets.address", ondelete="CASCADE"), nullable=False)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    composite: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    sharpe_proxy: Mapped[float | None] = mapped_column(Numeric(8, 4))
    max_dd_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    win_rate: Mapped[float | None] = mapped_column(Numeric(5, 4))
    sample_size: Mapped[int | None] = mapped_column(Integer)
    avg_hold_seconds: Mapped[int | None] = mapped_column(Integer)
    recency_weight: Mapped[float | None] = mapped_column(Numeric(5, 4))
    passes_anti_fluke: Mapped[bool] = mapped_column(default=True, nullable=False)
    fluke_reason: Mapped[str | None] = mapped_column(String(64))

    wallet: Mapped[Wallet] = relationship(back_populates="scores")


class Signal(Base):
    """A convergence event. May or may not result in a paper position (depends on safety state)."""

    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_ts", "fired_at"),
        Index("ix_signals_coin_side", "coin", "side"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    wallet_count: Mapped[int] = mapped_column(Integer, nullable=False)
    wallet_addresses: Mapped[str] = mapped_column(Text, nullable=False)  # comma-separated
    score_floor_used: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # pending | filled | skipped_paused | skipped_breaker | skipped_coin_paused
    # | skipped_drain | skipped_max_concurrent | skipped_universe | error
    reason: Mapped[str | None] = mapped_column(String(128))

    paper_position: Mapped["PaperPosition | None"] = relationship(back_populates="signal", uselist=False)


class PaperPosition(Base):
    """A paper trade we placed on BloFin demo (or another venue) in response to a Signal."""

    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint("close_reason IN (NULL, 'sl', 'tp', 'wallet_exit', 'breaker', 'drain', 'manual', 'error')",
                        name="ck_paper_positions_close_reason"),
        Index("ix_paper_positions_status", "status"),
        Index("ix_paper_positions_opened", "opened_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), unique=True, nullable=False)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)  # blofin | lighter | etc
    venue_order_id: Mapped[str | None] = mapped_column(String(64))
    venue_sl_order_id: Mapped[str | None] = mapped_column(String(64))
    venue_tp_order_id: Mapped[str | None] = mapped_column(String(64))
    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    margin_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    notional_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_px: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    sl_px: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    tp_px: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_px: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    realized_pnl_usdt: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    realized_pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(8), default="open", nullable=False)  # open | closed
    close_reason: Mapped[str | None] = mapped_column(String(16))

    signal: Mapped[Signal] = relationship(back_populates="paper_position")


class RuntimeState(Base):
    """Single-row key-value store for runtime flags (pause state, last heartbeat, etc.)."""

    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
