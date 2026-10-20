from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from tradepnl.errors import OversellError
from tradepnl.models import Trade
from tradepnl.schemas import IngestResult, TradeResult
from tradepnl.venues.normalize import CanonicalTrade

from .ledger import apply_trade


def ingest(
    session: Session,
    venue: str,
    trades: Iterable[CanonicalTrade],
    *,
    allow_short: bool,
    ingest_key: str | None = None,
) -> IngestResult:
    """Apply a batch of canonical trades, one transaction per trade.

    Per-trade commits keep one bad fill from sinking the rest of the batch:
    each item comes back accepted, duplicate or rejected. A trade is recorded
    and its position update commits atomically, so the ledger can never show
    a position for a trade that is not persisted (or vice versa).
    """
    results: list[TradeResult] = []
    for ct in trades:
        exists = session.exec(
            select(Trade).where(
                Trade.venue == venue,
                Trade.venue_trade_id == ct.venue_trade_id,
            )
        ).first()
        if exists is not None:
            results.append(TradeResult(venue_trade_id=ct.venue_trade_id, status="duplicate"))
            continue

        row = Trade(
            venue=venue,
            venue_trade_id=ct.venue_trade_id,
            account=ct.account,
            symbol=ct.symbol,
            base=ct.base,
            quote=ct.quote,
            side=ct.side,
            price=ct.price,
            quantity=ct.quantity,
            fee=ct.fee,
            fee_asset=ct.fee_asset,
            executed_at=ct.executed_at,
            ingest_key=ingest_key,
            raw=ct.raw,
        )
        try:
            session.add(row)
            apply_trade(session, row, allow_short=allow_short)
            session.commit()
        except OversellError as exc:
            session.rollback()
            results.append(
                TradeResult(
                    venue_trade_id=ct.venue_trade_id,
                    status="rejected",
                    code="OVERSELL",
                    detail=str(exc),
                )
            )
        except IntegrityError:
            # Lost a race against a concurrent writer for the same
            # (venue, venue_trade_id). The unique constraint held; this is a
            # duplicate, not an error.
            session.rollback()
            results.append(TradeResult(venue_trade_id=ct.venue_trade_id, status="duplicate"))
        else:
            results.append(TradeResult(venue_trade_id=ct.venue_trade_id, status="accepted"))

    return IngestResult(
        venue=venue,
        accepted=sum(r.status == "accepted" for r in results),
        duplicates=sum(r.status == "duplicate" for r in results),
        rejected=sum(r.status == "rejected" for r in results),
        results=results,
    )
