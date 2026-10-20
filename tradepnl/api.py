from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .errors import UnknownVenueError
from .models import IdempotencyRecord, MarkPrice, Position, ReconcileRun, Trade, utcnow
from .schemas import (
    MarkBatchIn,
    MarkOut,
    PnlReport,
    PnlRow,
    PnlTotals,
    PositionOut,
    ReconcileIn,
    ReconcileReport,
    ReconcileRow,
    TradeBatchIn,
    TradeIn,
    TradeOut,
)
from .services import ingest as ingest_service
from .services import reconcile as reconcile_service
from .venues import adapt, supported_venues
from .venues.normalize import CanonicalTrade, derive_trade_id, split_symbol

router = APIRouter()


# ---------- dependencies ----------

def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def require_api_key(request: Request) -> None:
    """Optional shared-secret guard: enforced only when API_KEY is configured.

    Positions and PnL are sensitive, so when API_KEY is set every /v1 route
    requires it; /health stays open for liveness probes.
    """
    expected = request.app.state.settings.api_key
    if expected is not None and request.headers.get("x-api-key") != expected:
        raise HTTPException(status_code=401, detail="missing or invalid API key")


AuthDeps = [Depends(require_api_key)]


def _norm_symbol(raw: str) -> str:
    try:
        return split_symbol(raw)[0]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------- helpers ----------

def _to_canonical(venue: str, t: TradeIn) -> CanonicalTrade:
    symbol, base, quote = split_symbol(t.symbol)
    return CanonicalTrade(
        venue=venue,
        venue_trade_id=t.venue_trade_id or derive_trade_id(venue, t.model_dump(mode="json")),
        account=t.account,
        symbol=symbol,
        base=base,
        quote=quote,
        side=t.side,
        price=t.price,
        quantity=t.quantity,
        fee=t.fee,
        fee_asset=t.fee_asset.upper() if t.fee_asset else None,
        executed_at=t.executed_at or utcnow(),
        raw=t.model_dump(mode="json"),
    )


async def _idempotent(
    request: Request,
    session: Session,
    endpoint: str,
    produce,
) -> JSONResponse:
    """Run `produce` under an Idempotency-Key if the caller sent one.

    Same key + same body -> stored response with `Idempotency-Replayed: true`.
    Same key + different body -> 409 (the key promises a specific request).
    """
    key = request.headers.get("idempotency-key")
    if not key:
        result = produce()
        return JSONResponse(result.model_dump(mode="json"))

    body_hash = hashlib.sha256(await request.body()).hexdigest()
    record = session.get(IdempotencyRecord, key)
    if record is not None:
        if record.endpoint != endpoint or record.request_hash != body_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used with a different request",
            )
        return JSONResponse(record.response, headers={"Idempotency-Replayed": "true"})

    result = produce()
    body = result.model_dump(mode="json")
    try:
        session.add(
            IdempotencyRecord(
                key=key, endpoint=endpoint, request_hash=body_hash,
                status_code=200, response=body,
            )
        )
        session.commit()
    except IntegrityError:
        # Another request stored the same key first. Its stored response is
        # authoritative if the payloads match.
        session.rollback()
        record = session.get(IdempotencyRecord, key)
        if record is not None and record.request_hash == body_hash:
            return JSONResponse(record.response, headers={"Idempotency-Replayed": "true"})
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request",
        ) from None
    return JSONResponse(body)


def _trade_out(t: Trade) -> TradeOut:
    return TradeOut(
        venue=t.venue,
        venue_trade_id=t.venue_trade_id,
        account=t.account,
        symbol=t.symbol,
        side=t.side,
        price=t.price,
        quantity=t.quantity,
        fee=t.fee,
        fee_asset=t.fee_asset,
        executed_at=t.executed_at,
        ingested_at=t.ingested_at,
    )


def _position_out(p: Position) -> PositionOut:
    return PositionOut(
        venue=p.venue,
        account=p.account,
        symbol=p.symbol,
        quantity=p.quantity,
        avg_cost=p.avg_cost,
        realized_pnl=p.realized_pnl,
        last_price=p.last_price,
        trade_count=p.trade_count,
        updated_at=p.updated_at,
    )


def _report(run: ReconcileRun) -> ReconcileReport:
    return ReconcileReport(
        run_id=run.id,
        venue=run.venue,
        account=run.account,
        created_at=run.created_at,
        tolerance=run.tolerance,
        compared=run.compared,
        matched=run.matched,
        breaks=run.breaks,
        results=[ReconcileRow(**r) for r in run.results],
    )


# ---------- health ----------

@router.get("/health")
def health(session: SessionDep) -> dict:
    session.exec(select(Trade).limit(1))
    return {"status": "ok", "venues": supported_venues()}


# ---------- ingestion ----------

@router.post("/v1/trades", dependencies=AuthDeps)
async def ingest_trades(body: TradeBatchIn, request: Request, session: SessionDep) -> JSONResponse:
    try:
        items = [_to_canonical(body.venue, t) for t in body.trades]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = request.app.state.settings
    return await _idempotent(
        request, session, "ingest",
        lambda: ingest_service.ingest(
            session, body.venue, items,
            allow_short=settings.allow_short,
            ingest_key=request.headers.get("idempotency-key"),
        ),
    )


@router.post("/v1/venues/{venue}/trades", dependencies=AuthDeps)
async def ingest_venue_trades(
    venue: str,
    request: Request,
    session: SessionDep,
    payload: Annotated[dict | list, Body()],
    account: Annotated[str, Query()] = "main",
) -> JSONResponse:
    """Ingest raw venue-native payloads through that venue's adapter."""
    try:
        items = adapt(venue.lower(), payload, account=account)
    except UnknownVenueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid {venue} payload: {exc}") from exc

    settings = request.app.state.settings
    return await _idempotent(
        request, session, f"ingest:{venue.lower()}",
        lambda: ingest_service.ingest(
            session, venue.lower(), items,
            allow_short=settings.allow_short,
            ingest_key=request.headers.get("idempotency-key"),
        ),
    )


@router.get("/v1/trades", dependencies=AuthDeps)
def list_trades(
    session: SessionDep,
    venue: str | None = None,
    account: str | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TradeOut]:
    stmt = select(Trade).order_by(Trade.executed_at, Trade.id).limit(limit).offset(offset)
    if venue:
        stmt = stmt.where(Trade.venue == venue)
    if account:
        stmt = stmt.where(Trade.account == account)
    if symbol:
        stmt = stmt.where(Trade.symbol == _norm_symbol(symbol))
    if since:
        stmt = stmt.where(Trade.executed_at >= since)
    return [_trade_out(t) for t in session.exec(stmt)]


# ---------- positions & pnl ----------

@router.get("/v1/positions", dependencies=AuthDeps)
def list_positions(
    session: SessionDep,
    venue: str | None = None,
    account: str | None = None,
    symbol: str | None = None,
) -> list[PositionOut]:
    stmt = select(Position).order_by(Position.venue, Position.account, Position.symbol)
    if venue:
        stmt = stmt.where(Position.venue == venue)
    if account:
        stmt = stmt.where(Position.account == account)
    if symbol:
        stmt = stmt.where(Position.symbol == _norm_symbol(symbol))
    return [_position_out(p) for p in session.exec(stmt)]


@router.get("/v1/pnl", dependencies=AuthDeps)
def pnl_report(
    session: SessionDep,
    venue: str | None = None,
    account: str | None = None,
    symbol: str | None = None,
) -> PnlReport:
    stmt = select(Position).order_by(Position.venue, Position.account, Position.symbol)
    if venue:
        stmt = stmt.where(Position.venue == venue)
    if account:
        stmt = stmt.where(Position.account == account)
    if symbol:
        stmt = stmt.where(Position.symbol == _norm_symbol(symbol))
    positions = list(session.exec(stmt))

    marks = {m.symbol: m.price for m in session.exec(select(MarkPrice))}

    rows: list[PnlRow] = []
    realized = Decimal(0)
    unrealized = Decimal(0)
    unpriced: list[str] = []
    for p in positions:
        mark = marks.get(p.symbol) or p.last_price
        unreal = (mark - p.avg_cost) * p.quantity if mark is not None else None
        if mark is None:
            unpriced.append(p.symbol)
        realized += p.realized_pnl
        unrealized += unreal or Decimal(0)
        rows.append(
            PnlRow(
                venue=p.venue,
                account=p.account,
                symbol=p.symbol,
                quantity=p.quantity,
                avg_cost=p.avg_cost,
                mark_price=mark,
                realized_pnl=p.realized_pnl,
                unrealized_pnl=unreal,
                total_pnl=p.realized_pnl + (unreal or Decimal(0)),
            )
        )

    return PnlReport(
        as_of=utcnow(),
        positions=rows,
        totals=PnlTotals(
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_pnl=realized + unrealized,
        ),
        unpriced=sorted(set(unpriced)),
    )


# ---------- marks ----------

@router.post("/v1/marks", dependencies=AuthDeps)
def upsert_marks(body: MarkBatchIn, session: SessionDep) -> list[MarkOut]:
    out = []
    for m in body.marks:
        symbol = _norm_symbol(m.symbol)
        row = session.get(MarkPrice, symbol)
        if row is None:
            row = MarkPrice(symbol=symbol, price=m.price, source=m.source)
        else:
            row.price = m.price
            row.source = m.source
            row.updated_at = utcnow()
        session.add(row)
        out.append(row)
    session.commit()
    return [MarkOut(symbol=r.symbol, price=r.price, source=r.source, updated_at=r.updated_at) for r in out]


@router.get("/v1/marks", dependencies=AuthDeps)
def list_marks(session: SessionDep) -> list[MarkOut]:
    return [
        MarkOut(symbol=m.symbol, price=m.price, source=m.source, updated_at=m.updated_at)
        for m in session.exec(select(MarkPrice).order_by(MarkPrice.symbol))
    ]


# ---------- reconciliation ----------

@router.post("/v1/reconcile", dependencies=AuthDeps)
def run_reconcile(body: ReconcileIn, request: Request, session: SessionDep) -> ReconcileReport:
    settings = request.app.state.settings
    tolerance = body.tolerance if body.tolerance is not None else settings.recon_tolerance
    run = reconcile_service.reconcile(
        session, body.venue.lower(), body.account, body.positions, tolerance=tolerance
    )
    return _report(run)


@router.get("/v1/reconcile/{run_id}", dependencies=AuthDeps)
def get_reconcile(run_id: int, session: SessionDep) -> ReconcileReport:
    run = session.get(ReconcileRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"reconcile run {run_id} not found")
    return _report(run)
