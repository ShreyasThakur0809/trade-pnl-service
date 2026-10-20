from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from .decimals import DecimalText


def utcnow() -> datetime:
    return datetime.now(UTC)


class Trade(SQLModel, table=True):
    """One normalized fill.

    (venue, venue_trade_id) is the natural dedup key: venues assign every fill
    a stable id, so a retried import can never double-count. The unique
    constraint is the backstop; ingestion also checks first for a friendlier
    "duplicate" result than a raw IntegrityError.
    """

    __table_args__ = (
        UniqueConstraint("venue", "venue_trade_id", name="uq_trade_venue_external"),
        Index("ix_trade_list", "venue", "symbol", "executed_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    venue: str = Field(index=True)
    venue_trade_id: str
    account: str = "main"
    symbol: str = Field(index=True)          # canonical "BASE-QUOTE"
    base: str
    quote: str
    side: str                                # "buy" | "sell"
    price: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    quantity: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    fee: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    fee_asset: str | None = None
    executed_at: datetime
    ingested_at: datetime = Field(default_factory=utcnow)
    ingest_key: str | None = None            # Idempotency-Key that carried this trade
    raw: dict | None = Field(default=None, sa_column=Column(JSON))


class Position(SQLModel, table=True):
    """Running position per (venue, account, symbol), weighted-average cost."""

    __table_args__ = (
        UniqueConstraint("venue", "account", "symbol", name="uq_position_scope"),
    )

    id: int | None = Field(default=None, primary_key=True)
    venue: str
    account: str
    symbol: str
    quantity: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    avg_cost: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    realized_pnl: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    last_price: Decimal | None = Field(default=None, sa_column=Column(DecimalText()))
    trade_count: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class MarkPrice(SQLModel, table=True):
    """Latest mark for unrealized PnL. Symbol-level, not venue-level."""

    symbol: str = Field(primary_key=True)
    price: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    source: str = "manual"
    updated_at: datetime = Field(default_factory=utcnow)


class IdempotencyRecord(SQLModel, table=True):
    """Stored response for an Idempotency-Key.

    A replayed request (same key + same payload) returns the stored body
    byte-for-byte; the same key with a different payload is a 409.
    """

    key: str = Field(primary_key=True)
    endpoint: str
    request_hash: str
    status_code: int
    response: dict = Field(sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class ReconcileRun(SQLModel, table=True):
    """Persisted result of one reconciliation run."""

    id: int | None = Field(default=None, primary_key=True)
    venue: str
    account: str
    created_at: datetime = Field(default_factory=utcnow)
    tolerance: Decimal = Field(sa_column=Column(DecimalText(), nullable=False))
    compared: int = 0
    matched: int = 0
    breaks: int = 0
    results: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
