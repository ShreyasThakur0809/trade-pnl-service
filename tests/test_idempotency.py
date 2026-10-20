from __future__ import annotations

from conftest import ingest, trade

BODY = {"venue": "binance", "trades": [trade("t1", price="100", quantity="1")]}


def test_idempotency_key_replays_same_response(client):
    headers = {"Idempotency-Key": "key-1"}
    r1 = client.post("/v1/trades", json=BODY, headers=headers)
    r2 = client.post("/v1/trades", json=BODY, headers=headers)

    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r2.headers["idempotency-replayed"] == "true"
    # effect applied exactly once
    assert client.get("/v1/positions").json()[0]["quantity"] == "1"


def test_idempotency_key_reuse_with_different_body_conflicts(client):
    headers = {"Idempotency-Key": "key-1"}
    client.post("/v1/trades", json=BODY, headers=headers)

    other = {"venue": "binance", "trades": [trade("t2", price="200")]}
    r = client.post("/v1/trades", json=other, headers=headers)
    assert r.status_code == 409


def test_different_keys_apply_normally(client):
    client.post("/v1/trades", json=BODY, headers={"Idempotency-Key": "k1"})
    r = client.post(
        "/v1/trades",
        json={"venue": "binance", "trades": [trade("t2")]},
        headers={"Idempotency-Key": "k2"},
    )
    assert r.status_code == 200
    assert client.get("/v1/positions").json()[0]["quantity"] == "2"


def test_retry_without_key_dedupes_by_trade_id(client):
    # a client that forgets the key is still safe: the natural key catches it
    ingest(client, "binance", [trade("t1")])
    res = ingest(client, "binance", [trade("t1")])
    assert res["duplicates"] == 1
