from __future__ import annotations

from collections.abc import Callable

from tradepnl.errors import UnknownVenueError

from . import binance, coindcx, coinbase
from .normalize import CanonicalTrade

ADAPTERS: dict[str, Callable[[dict, str], CanonicalTrade]] = {
    "binance": binance.normalize,
    "coinbase": coinbase.normalize,
    "coindcx": coindcx.normalize,
}


def supported_venues() -> list[str]:
    return sorted(ADAPTERS)


def adapt(venue: str, payload, account: str = "main") -> list[CanonicalTrade]:
    """Normalize a raw venue payload (single dict or list) into canonical trades."""
    try:
        fn = ADAPTERS[venue]
    except KeyError:
        raise UnknownVenueError(
            f"unsupported venue {venue!r}; supported: {', '.join(supported_venues())}"
        ) from None
    rows = payload if isinstance(payload, list) else payload.get("trades", [payload])
    return [fn(row, account) for row in rows]
