from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from .normalize import CanonicalTrade, derive_trade_id, split_symbol


def normalize(payload: dict, account: str = "main") -> CanonicalTrade:
    """Binance `GET /api/v3/myTrades` row.

    {"id": 28457, "symbol": "BTCUSDT", "price": "67250.55", "qty": "0.004",
     "commission": "0.000004", "commissionAsset": "BTC",
     "time": 1729353600000, "isBuyer": true}
    """
    symbol, base, quote = split_symbol(payload["symbol"])
    trade_id = payload.get("id")
    return CanonicalTrade(
        venue="binance",
        venue_trade_id=str(trade_id) if trade_id is not None else derive_trade_id("binance", payload),
        account=account,
        symbol=symbol,
        base=base,
        quote=quote,
        side="buy" if payload.get("isBuyer") else "sell",
        price=Decimal(str(payload["price"])),
        quantity=Decimal(str(payload["qty"])),
        fee=Decimal(str(payload.get("commission", 0))),
        fee_asset=payload.get("commissionAsset"),
        executed_at=datetime.fromtimestamp(int(payload["time"]) / 1000, UTC),
        raw=payload,
    )
