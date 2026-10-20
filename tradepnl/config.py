from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, sourced from the environment by default."""

    database_url: str = "sqlite:///./tradepnl.db"
    api_key: str | None = None          # when set, X-API-Key is required on writes
    allow_short: bool = False           # permit positions to go negative
    recon_tolerance: Decimal = Decimal("0.00000001")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL", "sqlite:///./tradepnl.db"),
            api_key=os.environ.get("API_KEY") or None,
            allow_short=os.environ.get("ALLOW_SHORT", "").lower() in {"1", "true", "yes"},
            recon_tolerance=Decimal(os.environ.get("RECON_TOLERANCE", "0.00000001")),
        )
