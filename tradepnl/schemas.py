from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from .decimals import DecStr

Side = Literal["buy", "sell"]


# ---------- ingestion ----------

class TradeIn(BaseModel):
    """Canonical trade accepted by POST /v1/trades.

    symbol accepts "BTC-USDT", "BTC_USDT" or "BTCUSDT"; it is normalized to
    "BASE-QUOTE". venue_trade_id may be omitted, in which case a deterministic
    id is derived from the payload (so a retried send still deduplicates).
    """

    venue_trade_id: str | None = None
    account: str = "main"
    symbol: str
    side: Side
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    fee: Decimal = Field(default=Decimal(0), ge=0)
    fee_asset: str | None = None
    executed_at: datetime | None = None


class TradeBatchIn(BaseModel):
    venue: str
    trades: list[TradeIn] = Field(min_length=1, max_length=5000)


class TradeResult(BaseModel):
    venue_trade_id: str
    status: Literal["accepted", "duplicate", "rejected"]
    code: str | None = None
    detail: str | None = None


class IngestResult(BaseModel):
    venue: str
    accepted: int
    duplicates: int
    rejected: int
    results: list[TradeResult]


# ---------- reads ----------

class TradeOut(BaseModel):
    venue: str
    venue_trade_id: str
    account: str
    symbol: str
    side: str
    price: DecStr
    quantity: DecStr
    fee: DecStr
    fee_asset: str | None
    executed_at: datetime
    ingested_at: datetime


class PositionOut(BaseModel):
    venue: str
    account: str
    symbol: str
    quantity: DecStr
    avg_cost: DecStr
    realized_pnl: DecStr
    last_price: DecStr | None
    trade_count: int
    updated_at: datetime


# ---------- marks ----------

class MarkIn(BaseModel):
    symbol: str
    price: Decimal = Field(gt=0)
    source: str = "manual"


class MarkBatchIn(BaseModel):
    marks: list[MarkIn] = Field(min_length=1)


class MarkOut(BaseModel):
    symbol: str
    price: DecStr
    source: str
    updated_at: datetime


# ---------- pnl ----------

class PnlRow(BaseModel):
    venue: str
    account: str
    symbol: str
    quantity: DecStr
    avg_cost: DecStr
    mark_price: DecStr | None
    realized_pnl: DecStr
    unrealized_pnl: DecStr | None
    total_pnl: DecStr


class PnlTotals(BaseModel):
    realized_pnl: DecStr
    unrealized_pnl: DecStr
    total_pnl: DecStr


class PnlReport(BaseModel):
    as_of: datetime
    positions: list[PnlRow]
    totals: PnlTotals
    unpriced: list[str]  # positions with no mark and no last trade price


# ---------- reconciliation ----------

class ExternalPosition(BaseModel):
    symbol: str
    quantity: Decimal


class ReconcileIn(BaseModel):
    """Snapshot of what the venue reports we hold."""

    venue: str
    account: str = "main"
    positions: list[ExternalPosition]
    tolerance: Decimal | None = None     # defaults to server setting


class ReconcileRow(BaseModel):
    symbol: str
    internal: DecStr | None              # None = not held internally
    external: DecStr | None              # None = absent from the snapshot
    diff: DecStr                         # internal - external
    status: Literal["match", "break"]


class ReconcileReport(BaseModel):
    run_id: int
    venue: str
    account: str
    created_at: datetime
    tolerance: DecStr
    compared: int
    matched: int
    breaks: int
    results: list[ReconcileRow]
