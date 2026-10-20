from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

# Quote currencies we can split off the tail of a concatenated pair like
# "BTCUSDT" or "ETHBTC". Ordered longest-first so USDT wins over USD.
KNOWN_QUOTES = (
    "FDUSD", "USDT", "USDC", "TUSD", "DAI",
    "USD", "BTC", "ETH", "BNB", "INR", "EUR", "JPY",
)

# Kraken-style legacy asset names.
ASSET_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


@dataclass(frozen=True)
class CanonicalTrade:
    """The internal representation every venue adapter produces."""

    venue: str
    venue_trade_id: str
    account: str
    symbol: str            # "BTC-USDT"
    base: str              # "BTC"
    quote: str             # "USDT"
    side: str              # "buy" | "sell"
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_asset: str | None
    executed_at: datetime
    raw: dict


def split_symbol(raw: str) -> tuple[str, str, str]:
    """Normalize a venue pair to (symbol, base, quote).

    Handles "BTC-USDT", "BTC_USDT", "BTC/USDT", CoinDCX "B-BTC_USDT",
    and bare concatenated "BTCUSDT". Returns ("BTC-USDT", "BTC", "USDT").
    """
    s = raw.strip().upper()
    if s.startswith("B-"):                    # CoinDCX market prefix
        s = s[2:]
    base = quote = ""
    for sep in ("-", "_", "/", ":"):
        if sep in s:
            base, _, quote = s.partition(sep)
            break
    if not quote:
        for q in KNOWN_QUOTES:
            if s.endswith(q) and len(s) > len(q):
                base, quote = s[: -len(q)], q
                break
    if not base or not quote:
        raise ValueError(f"cannot parse symbol {raw!r}")
    base = ASSET_ALIASES.get(base, base)
    quote = ASSET_ALIASES.get(quote, quote)
    return f"{base}-{quote}", base, quote


def derive_trade_id(venue: str, payload: dict) -> str:
    """Deterministic id for payloads that carry no native trade id.

    Same payload -> same id, so retrying an import deduplicates instead of
    double-counting.
    """
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"{venue}-derived-{digest[:24]}"
