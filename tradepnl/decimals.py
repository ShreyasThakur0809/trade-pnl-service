from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class DecimalText(TypeDecorator):
    """Persist Decimal as its exact string form.

    SQLite has no fixed-point numeric type: NUMERIC affinity silently stores
    values as REAL (IEEE 754), which corrupts quantities like 0.00000001 BTC
    or prices with many decimals. Storing the canonical string keeps every
    bit of precision; all arithmetic happens in Python with Decimal anyway,
    so nothing relies on SQL-side numeric comparison. On Postgres this column
    should become NUMERIC(38, 18) instead (see DESIGN.md).
    """

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect) -> str | None:
        return None if value is None else format(value, "f")

    def process_result_value(self, value: str | None, dialect) -> Decimal | None:
        return None if value is None else Decimal(value)


# API responses render Decimals as plain strings ("67250.55", never 6.7e4),
# matching how every major exchange API emits numbers and avoiding float noise.
DecStr = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str)]
