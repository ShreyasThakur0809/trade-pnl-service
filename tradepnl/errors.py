from __future__ import annotations

from decimal import Decimal


class OversellError(Exception):
    """A sell was attempted against insufficient inventory for the position."""

    def __init__(
        self,
        venue: str,
        account: str,
        symbol: str,
        available: Decimal,
        requested: Decimal,
    ) -> None:
        self.venue = venue
        self.account = account
        self.symbol = symbol
        self.available = available
        self.requested = requested
        super().__init__(
            f"oversell on {venue}/{account} {symbol}: "
            f"requested {requested} but only {available} available"
        )


class UnknownVenueError(Exception):
    """No adapter is registered for the requested venue."""
