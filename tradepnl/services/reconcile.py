from __future__ import annotations

from decimal import Decimal

from sqlmodel import Session, select

from tradepnl.models import Position, ReconcileRun
from tradepnl.schemas import ExternalPosition


def reconcile(
    session: Session,
    venue: str,
    account: str,
    external: list[ExternalPosition],
    *,
    tolerance: Decimal,
) -> ReconcileRun:
    """Diff an external position snapshot against the internal ledger.

    Every symbol held on either side is compared. |internal - external| above
    `tolerance` is a break; rows absent on one side count as zero on that side.
    The run is persisted so the report stays auditable.
    """
    internal = {
        p.symbol: p.quantity
        for p in session.exec(
            select(Position).where(Position.venue == venue, Position.account == account)
        )
    }
    ext_map = {e.symbol: e.quantity for e in external}

    rows: list[dict] = []
    for symbol in sorted(set(internal) | set(ext_map)):
        iq = internal.get(symbol)
        eq = ext_map.get(symbol)
        diff = (iq or Decimal(0)) - (eq or Decimal(0))
        rows.append(
            {
                "symbol": symbol,
                "internal": None if iq is None else format(iq, "f"),
                "external": None if eq is None else format(eq, "f"),
                "diff": format(diff, "f"),
                "status": "break" if abs(diff) > tolerance else "match",
            }
        )

    run = ReconcileRun(
        venue=venue,
        account=account,
        tolerance=tolerance,
        compared=len(rows),
        matched=sum(r["status"] == "match" for r in rows),
        breaks=sum(r["status"] == "break" for r in rows),
        results=rows,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
