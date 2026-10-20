from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradepnl.config import Settings
from tradepnl.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path}/test.db")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def auth_client(tmp_path):
    s = Settings(database_url=f"sqlite:///{tmp_path}/auth.db", api_key="secret-key")
    return TestClient(create_app(s))


@pytest.fixture
def short_client(tmp_path):
    s = Settings(database_url=f"sqlite:///{tmp_path}/short.db", allow_short=True)
    return TestClient(create_app(s))


def trade(
    trade_id: str,
    side: str = "buy",
    symbol: str = "BTC-USDT",
    price: str = "100",
    quantity: str = "1",
    fee: str = "0",
    fee_asset: str | None = None,
    account: str = "main",
) -> dict:
    t = {
        "venue_trade_id": trade_id,
        "account": account,
        "symbol": symbol,
        "side": side,
        "price": price,
        "quantity": quantity,
        "fee": fee,
    }
    if fee_asset:
        t["fee_asset"] = fee_asset
    return t


def ingest(client: TestClient, venue: str, trades: list[dict], **kwargs) -> dict:
    r = client.post("/v1/trades", json={"venue": venue, "trades": trades}, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()
