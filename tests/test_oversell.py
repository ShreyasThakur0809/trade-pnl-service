from __future__ import annotations

from conftest import ingest, trade


def test_oversell_rejected_and_position_untouched(client):
    ingest(client, "binance", [trade("b1", quantity="1")])
    res = ingest(client, "binance", [trade("s1", side="sell", quantity="2")])

    item = res["results"][0]
    assert res["rejected"] == 1
    assert item["status"] == "rejected"
    assert item["code"] == "OVERSELL"
    assert "available" in item["detail"]

    p = client.get("/v1/positions").json()[0]
    assert p["quantity"] == "1"          # unchanged
    assert p["trade_count"] == 1         # rejected fill not folded in


def test_sell_exact_position_is_allowed(client):
    ingest(client, "binance", [trade("b1", quantity="1")])
    res = ingest(client, "binance", [trade("s1", side="sell", quantity="1")])
    assert res["accepted"] == 1


def test_oversell_in_batch_does_not_sink_other_items(client):
    ingest(client, "binance", [trade("b1", quantity="1")])
    res = ingest(
        client,
        "binance",
        [trade("s1", side="sell", quantity="5"), trade("b2", quantity="1")],
    )
    assert res["rejected"] == 1
    assert res["accepted"] == 1
    assert client.get("/v1/positions").json()[0]["quantity"] == "2"


def test_short_selling_when_enabled(short_client):
    res = ingest(short_client, "binance", [trade("s1", side="sell", quantity="3")])
    assert res["accepted"] == 1
    assert short_client.get("/v1/positions").json()[0]["quantity"] == "-3"


def test_sell_with_no_position_is_oversell(client):
    res = ingest(client, "binance", [trade("s1", side="sell", quantity="1")])
    assert res["rejected"] == 1
    assert res["results"][0]["code"] == "OVERSELL"
    assert client.get("/v1/positions").json() == []


def test_oversell_then_legit_sell_still_works(client):
    ingest(client, "binance", [trade("b1", quantity="1")])
    ingest(client, "binance", [trade("s1", side="sell", quantity="5")])
    res = ingest(client, "binance", [trade("s2", side="sell", quantity="1")])
    assert res["accepted"] == 1
    assert client.get("/v1/positions").json()[0]["quantity"] == "0"
