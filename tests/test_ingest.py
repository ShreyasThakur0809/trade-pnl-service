from __future__ import annotations

from decimal import Decimal

from conftest import ingest, trade


def test_ingest_single_trade_creates_position(client):
    res = ingest(client, "binance", [trade("t1", price="100", quantity="2")])
    assert res["accepted"] == 1
    assert res["results"][0]["status"] == "accepted"

    positions = client.get("/v1/positions").json()
    assert len(positions) == 1
    p = positions[0]
    assert p["venue"] == "binance"
    assert p["symbol"] == "BTC-USDT"
    assert p["quantity"] == "2"
    assert p["avg_cost"] == "100"


def test_ingest_batch_multiple_venues(client):
    ingest(client, "binance", [trade("b1", quantity="1")])
    ingest(client, "coinbase", [trade("c1", symbol="ETH-USD", quantity="5")])

    venues = {p["venue"] for p in client.get("/v1/positions").json()}
    assert venues == {"binance", "coinbase"}


def test_symbol_normalization_on_canonical_ingest(client):
    res = ingest(client, "binance", [trade("t1", symbol="BTCUSDT")])
    assert res["accepted"] == 1
    assert client.get("/v1/positions").json()[0]["symbol"] == "BTC-USDT"


def test_duplicate_trade_id_is_skipped(client):
    ingest(client, "binance", [trade("t1")])
    res = ingest(client, "binance", [trade("t1")])
    assert res["duplicates"] == 1
    # position still reflects exactly one fill
    assert client.get("/v1/positions").json()[0]["quantity"] == "1"


def test_duplicate_inside_same_batch(client):
    res = ingest(client, "binance", [trade("t1"), trade("t1")])
    assert res["accepted"] == 1
    assert res["duplicates"] == 1


def test_same_id_on_different_venue_is_not_a_duplicate(client):
    ingest(client, "binance", [trade("t1")])
    res = ingest(client, "coinbase", [trade("t1", symbol="ETH-USD")])
    assert res["accepted"] == 1


def test_missing_trade_id_derives_deterministic_id(client):
    t = trade("x")  # will be replaced
    del t["venue_trade_id"]
    res1 = ingest(client, "binance", [t])
    res2 = ingest(client, "binance", [t])
    assert res1["accepted"] == 1
    assert res2["duplicates"] == 1
    assert res1["results"][0]["venue_trade_id"].startswith("binance-derived-")


def test_invalid_quantity_rejected_by_validation(client):
    r = client.post(
        "/v1/trades",
        json={"venue": "binance", "trades": [trade("t1", quantity="-1")]},
    )
    assert r.status_code == 422


def test_trades_listing_filters(client):
    ingest(client, "binance", [trade("t1", symbol="BTC-USDT"), trade("t2", symbol="ETH-USDT")])
    ingest(client, "coinbase", [trade("t3", symbol="BTC-USD")])

    assert len(client.get("/v1/trades").json()) == 3
    assert len(client.get("/v1/trades", params={"venue": "binance"}).json()) == 2
    assert len(client.get("/v1/trades", params={"symbol": "BTC-USDT"}).json()) == 1
