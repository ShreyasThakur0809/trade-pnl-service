from __future__ import annotations

from conftest import ingest, trade


def test_reconcile_all_match(client):
    ingest(client, "binance", [trade("b1", quantity="3")])
    r = client.post(
        "/v1/reconcile",
        json={"venue": "binance", "positions": [{"symbol": "BTC-USDT", "quantity": "3"}]},
    )
    assert r.status_code == 200
    report = r.json()
    assert report["breaks"] == 0
    assert report["results"][0]["status"] == "match"


def test_reconcile_detects_quantity_break(client):
    # internal says 3 BTC, venue reports 2.5 -> 0.5 break
    ingest(client, "binance", [trade("b1", quantity="3")])
    r = client.post(
        "/v1/reconcile",
        json={"venue": "binance", "positions": [{"symbol": "BTC-USDT", "quantity": "2.5"}]},
    )
    report = r.json()
    assert report["breaks"] == 1
    row = report["results"][0]
    assert row["internal"] == "3"
    assert row["external"] == "2.5"
    assert row["diff"] == "0.5"
    assert row["status"] == "break"


def test_reconcile_missing_on_either_side(client):
    ingest(client, "binance", [trade("b1", symbol="BTC-USDT", quantity="1")])
    r = client.post(
        "/v1/reconcile",
        json={
            "venue": "binance",
            "positions": [{"symbol": "ETH-USDT", "quantity": "5"}],   # BTC absent
        },
    )
    rows = {row["symbol"]: row for row in r.json()["results"]}
    assert rows["BTC-USDT"]["external"] is None
    assert rows["BTC-USDT"]["status"] == "break"
    assert rows["ETH-USDT"]["internal"] is None
    assert rows["ETH-USDT"]["status"] == "break"


def test_reconcile_run_is_persisted(client):
    ingest(client, "binance", [trade("b1")])
    r = client.post(
        "/v1/reconcile",
        json={"venue": "binance", "positions": [{"symbol": "BTC-USDT", "quantity": "1"}]},
    )
    run_id = r.json()["run_id"]

    fetched = client.get(f"/v1/reconcile/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id


def test_reconcile_unknown_run_404(client):
    assert client.get("/v1/reconcile/999").status_code == 404


def test_reconcile_tolerance_configurable(client):
    ingest(client, "binance", [trade("b1", quantity="1.000000001")])
    r = client.post(
        "/v1/reconcile",
        json={
            "venue": "binance",
            "tolerance": "0.00000001",
            "positions": [{"symbol": "BTC-USDT", "quantity": "1"}],
        },
    )
    # diff of 1e-9 is inside the 1e-8 tolerance
    assert r.json()["results"][0]["status"] == "match"
