from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .normalize import CanonicalTrade, derive_trade_id, split_symbol


def normalize(payload: dict, account: str = "main") -> CanonicalTrade:
    """CoinDCX trade-history row.

    {"id": "a1b2c3", "symbol": "B-BTC_USDT", "side": "buy",
     "price": 67250.55, "quantity": 0.004, "fee": 0.05,
     "timestamp": 1729353600000}
    """
    raw_symbol = payload.get("symbol") or payload.get("pair") or ""
    symbol, base, quote = split_symbol(raw_symbol)
    trade_id = payload.get("id") or payload.get("trade_id")
    ts_ms = int(payload.get("timestamp") or payload.get("time") or 0)
    return CanonicalTrade(
        venue="coindcx",
        venue_trade_id=str(trade_id) if trade_id is not None else derive_trade_id("coindcx", payload),
        account=account,
        symbol=symbol,
        base=base,
        quote=quote,
        side=str(payload["side"]).lower(),
        price=Decimal(str(payload["price"])),
        quantity=Decimal(str(payload.get("quantity", payload.get("qty")))),
        fee=Decimal(str(payload.get("fee", 0))),
        fee_asset=quote,                      # CoinDCX charges fees in quote currency
        executed_at=datetime.fromtimestamp(ts_ms / 1000, UTC),
        raw=payload,
    )
