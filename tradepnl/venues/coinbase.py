from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .normalize import CanonicalTrade, derive_trade_id, split_symbol


def normalize(payload: dict, account: str = "main") -> CanonicalTrade:
    """Coinbase Advanced Trade fill row.

    {"trade_id": "1234567", "product_id": "BTC-USD", "side": "BUY",
     "price": "67250.55", "size": "0.004", "fee": "1.35",
     "created_at": "2026-10-19T12:00:00Z"}
    """
    symbol, base, quote = split_symbol(payload["product_id"])
    trade_id = payload.get("trade_id")
    return CanonicalTrade(
        venue="coinbase",
        venue_trade_id=str(trade_id) if trade_id is not None else derive_trade_id("coinbase", payload),
        account=account,
        symbol=symbol,
        base=base,
        quote=quote,
        side=str(payload["side"]).lower(),
        price=Decimal(str(payload["price"])),
        quantity=Decimal(str(payload["size"])),
        fee=Decimal(str(payload.get("fee", 0))),
        fee_asset=quote,                      # Coinbase reports fee in quote currency
        executed_at=datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00")),
        raw=payload,
    )
