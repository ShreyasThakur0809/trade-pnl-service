from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from .api import router
from .config import Settings
from .db import init_db, make_engine
from .models import Trade


def load_sample_data(app: FastAPI) -> None:
    """Load sample data on startup if database is empty."""
    engine = app.state.engine
    with Session(engine) as session:
        # Check if we already have data
        existing = session.exec(select(Trade)).first()
        if existing is not None:
            return  # Data already loaded, skip

    # Load and ingest sample data
    sample_file = Path(__file__).parent.parent / "data" / "sample_trades.json"
    if not sample_file.exists():
        return

    doc = json.loads(sample_file.read_text())
    client = TestClient(app)

    for i, batch in enumerate(doc["batches"]):
        endpoint = batch["endpoint"]
        payload = batch["payload"]
        headers = {"Content-Type": "application/json", "Idempotency-Key": f"seed-{i}"}
        client.post(endpoint, json=payload, headers=headers)

    # Load marks
    client.post("/v1/marks", json={"marks": doc["marks"]})

    # Load reconciliation
    client.post("/v1/reconcile", json=doc["reconcile"])


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

    @app.on_event("startup")
    def startup_load_sample_data():
        load_sample_data(app)

    return app


app = create_app()
