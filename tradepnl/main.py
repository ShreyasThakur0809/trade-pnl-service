from __future__ import annotations

from fastapi import FastAPI

from .api import router
from .config import Settings
from .db import init_db, make_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    init_db(engine)

    app = FastAPI(
        title="Trade PnL Service",
        version="0.1.0",
        description=(
            "Multi-venue trade recorder: idempotent ingestion, position "
            "tracking, realized/unrealized PnL, reconciliation and oversell checks."
        ),
    )
    app.state.settings = settings
    app.state.engine = engine
    app.include_router(router)
    return app


app = create_app()
