from __future__ import annotations

BINANCE_FILL = {
    "id": 28457,
    "orderId": 100234,
    "symbol": "BTCUSDT",
    "price": "67250.55",
    "qty": "0.004",
    "quoteQty": "268.99",
    "commission": "0.000004",
    "commissionAsset": "BTC",
    "time": 1729353600000,
    "isBuyer": True,
    "isMaker": False,
}

COINBASE_FILL = {
    "trade_id": "cb-1234567",
    "product_id": "ETH-USD",
    "side": "BUY",
    "price": "3420.10",
    "size": "0.5",
    "fee": "0.86",
    "created_at": "2026-10-19T12:00:00Z",
}

COINDCX_FILL = {
    "id": "dcx-abc123",
    "symbol": "B-SOL_USDT",
    "side": "sell",
    "price": 148.25,
    "quantity": 2,
    "fee": 0.06,
    "timestamp": 1729353600000,
}


def test_binance_adapter_maps_fill(client):
    r = client.post("/v1/venues/binance/trades", json=[BINANCE_FILL])
    assert r.status_code == 200
    assert r.json()["accepted"] == 1

    t = client.get("/v1/trades").json()[0]
    assert t["venue_trade_id"] == "28457"
    assert t["symbol"] == "BTC-USDT"
    assert t["side"] == "buy"
    assert t["price"] == "67250.55"
    assert t["quantity"] == "0.004"


def test_coinbase_adapter_maps_fill(client):
    r = client.post("/v1/venues/coinbase/trades", json=[COINBASE_FILL])
    assert r.json()["accepted"] == 1

    t = client.get("/v1/trades").json()[0]
    assert t["venue"] == "coinbase"
    assert t["symbol"] == "ETH-USD"
    assert t["quantity"] == "0.5"


def test_coindcx_adapter_maps_fill_and_sell_side(client):
    # sell into an existing short-allowed scenario needs inventory first;
    # use canonical buy to seed, then adapter sell against it
    r = client.post(
        "/v1/venues/coindcx/trades",
        json=[{**COINDCX_FILL, "id": "dcx-buy1", "side": "buy"}],
    )
    assert r.json()["accepted"] == 1
    assert client.get("/v1/positions").json()[0]["symbol"] == "SOL-USDT"

    r = client.post("/v1/venues/coindcx/trades", json=[COINDCX_FILL])
    assert r.json()["accepted"] == 1
    assert client.get("/v1/positions").json()[0]["quantity"] == "0"


def test_venue_ingest_dedupes_on_replay(client):
    client.post("/v1/venues/binance/trades", json=[BINANCE_FILL])
    r = client.post("/v1/venues/binance/trades", json=[BINANCE_FILL])
    assert r.json()["duplicates"] == 1


def test_unknown_venue_404(client):
    r = client.post("/v1/venues/kraken/trades", json=[{"a": 1}])
    assert r.status_code == 404
    assert "supported" in r.json()["detail"]


def test_malformed_venue_payload_422(client):
    r = client.post("/v1/venues/binance/trades", json=[{"unexpected": "shape"}])
    assert r.status_code == 422
