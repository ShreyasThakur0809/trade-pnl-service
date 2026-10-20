from __future__ import annotations

from conftest import trade


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "binance" in r.json()["venues"]


def test_auth_required_when_key_configured(auth_client):
    body = {"venue": "binance", "trades": [trade("t1")]}
    assert auth_client.post("/v1/trades", json=body).status_code == 401
    assert auth_client.get("/v1/positions").status_code == 401
    assert (
        auth_client.post("/v1/trades", json=body, headers={"X-API-Key": "wrong"}).status_code
        == 401
    )
    r = auth_client.post("/v1/trades", json=body, headers={"X-API-Key": "secret-key"})
    assert r.status_code == 200


def test_health_open_even_with_key(auth_client):
    assert auth_client.get("/health").status_code == 200


def test_marks_upsert_and_list(client):
    client.post("/v1/marks", json={"marks": [{"symbol": "BTCUSDT", "price": "67000"}]})
    client.post("/v1/marks", json={"marks": [{"symbol": "BTC-USDT", "price": "68000"}]})

    marks = client.get("/v1/marks").json()
    assert len(marks) == 1                    # same canonical symbol -> upsert
    assert marks[0]["price"] == "68000"
    assert marks[0]["symbol"] == "BTC-USDT"
