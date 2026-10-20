from __future__ import annotations

from decimal import Decimal

from sqlmodel import Session, select

from tradepnl.errors import OversellError
from tradepnl.models import Position, Trade, utcnow

ZERO = Decimal(0)


def fee_in_quote(trade: Trade) -> Decimal:
    """Fee expressed in the position's quote currency.

    Fee in base asset is converted at the trade's own price (e.g. a BTC
    commission on a BTC-USDT buy). Fees denominated in a third asset (BNB and
    similar) cannot be converted without an extra price feed; they stay on the
    trade row but are excluded from PnL — see DESIGN.md.
    """
    if not trade.fee:
        return ZERO
    if trade.fee_asset == trade.quote:
        return trade.fee
    if trade.fee_asset == trade.base:
        return trade.fee * trade.price
    return ZERO


def get_position(session: Session, venue: str, account: str, symbol: str) -> Position | None:
    return session.exec(
        select(Position).where(
            Position.venue == venue,
            Position.account == account,
            Position.symbol == symbol,
        )
    ).first()


def apply_trade(session: Session, trade: Trade, *, allow_short: bool) -> Position:
    """Fold one fill into its position under signed-quantity accounting.

    quantity > 0 is a long with avg_cost = weighted buy basis; quantity < 0 is
    a short with avg_cost = weighted sell basis. Realized PnL is booked on the
    portion of each trade that reduces the open side.
    """
    pos = get_position(session, trade.venue, trade.account, trade.symbol)
    if pos is None:
        pos = Position(
            venue=trade.venue,
            account=trade.account,
            symbol=trade.symbol,
            quantity=ZERO,
            avg_cost=ZERO,
            realized_pnl=ZERO,
        )
        session.add(pos)

    fee_q = fee_in_quote(trade)
    qty, px = trade.quantity, trade.price

    if trade.side == "buy":
        if pos.quantity < ZERO:
            # Covering a short: realized = (entry - exit) * covered amount.
            cover = min(qty, -pos.quantity)
            pos.realized_pnl += (pos.avg_cost - px) * cover - fee_q
            remaining = qty + pos.quantity          # >0 means flipped long
            pos.quantity = remaining
            if remaining > ZERO:
                pos.avg_cost = px
            elif remaining == ZERO:
                pos.avg_cost = ZERO
        else:
            new_qty = pos.quantity + qty
            pos.avg_cost = (pos.avg_cost * pos.quantity + qty * px + fee_q) / new_qty
            pos.quantity = new_qty
    else:
        if qty > pos.quantity and not allow_short:
            # Covers both "sell more than held" and "sell with no position":
            # without shorts enabled, a sell may never take quantity negative.
            raise OversellError(trade.venue, trade.account, trade.symbol, pos.quantity, qty)
        if pos.quantity <= ZERO:
            # Opening or extending a short.
            new_abs = -pos.quantity + qty
            pos.avg_cost = (pos.avg_cost * -pos.quantity + qty * px) / new_abs
            pos.quantity -= qty
        else:
            closed = min(qty, pos.quantity)
            pos.realized_pnl += (px - pos.avg_cost) * closed - fee_q
            remaining = pos.quantity - qty          # <0 means flipped short
            pos.quantity = remaining
            if remaining < ZERO:
                pos.avg_cost = px
            elif remaining == ZERO:
                pos.avg_cost = ZERO

    pos.last_price = px
    pos.trade_count += 1
    pos.updated_at = utcnow()
    session.add(pos)
    return pos
