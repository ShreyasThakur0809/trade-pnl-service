from __future__ import annotations

from decimal import Decimal

from conftest import ingest, trade


def _pos(client, venue="binance", symbol=None):
    params = {"venue": venue}
    if symbol:
        params["symbol"] = symbol
    return client.get("/v1/positions", params=params).json()[0]


def test_weighted_average_cost(client):
    ingest(client, "binance", [trade("b1", price="100", quantity="1")])
    ingest(client, "binance", [trade("b2", price="200", quantity="1")])
    p = _pos(client)
    assert p["quantity"] == "2"
    assert p["avg_cost"] == "150"


def test_realized_pnl_on_sell(client):
    # buy 1 @100 + 1 @200 -> avg 150; sell 1 @300 -> realized 150
    ingest(client, "binance", [trade("b1", price="100"), trade("b2", price="200")])
    ingest(client, "binance", [trade("s1", side="sell", price="300")])
    p = _pos(client)
    assert p["quantity"] == "1"
    assert p["realized_pnl"] == "150"

    # close the rest -> total realized 300, flat position resets basis
    ingest(client, "binance", [trade("s2", side="sell", price="300")])
    p = _pos(client)
    assert p["quantity"] == "0"
    assert p["avg_cost"] == "0"
    assert p["realized_pnl"] == "300"


def test_quote_fee_moves_cost_basis_and_proceeds(client):
    # buy 1 @100, fee 10 USDT -> basis 110
    ingest(client, "binance", [trade("b1", price="100", fee="10", fee_asset="USDT")])
    assert _pos(client)["avg_cost"] == "110"
    # sell 1 @300, fee 10 USDT -> realized (300-110) - 10 = 180
    ingest(client, "binance", [trade("s1", side="sell", price="300", fee="10", fee_asset="USDT")])
    assert _pos(client)["realized_pnl"] == "180"


def test_base_asset_fee_converted_at_trade_price(client):
    # fee 0.1 BTC on a 1 BTC buy @100 -> 10 USDT of extra cost -> basis 110
    ingest(client, "binance", [trade("b1", price="100", fee="0.1", fee_asset="BTC")])
    assert Decimal(_pos(client)["avg_cost"]) == Decimal("110")


def test_unrealized_pnl_with_mark(client):
    ingest(client, "binance", [trade("b1", price="100", quantity="2")])
    client.post("/v1/marks", json={"marks": [{"symbol": "BTC-USDT", "price": "120"}]})

    report = client.get("/v1/pnl").json()
    row = report["positions"][0]
    assert row["mark_price"] == "120"
    assert row["unrealized_pnl"] == "40"     # (120-100) * 2
    assert row["realized_pnl"] == "0"
    assert report["totals"]["unrealized_pnl"] == "40"


def test_pnl_falls_back_to_last_trade_price(client):
    ingest(client, "binance", [trade("b1", price="100"), trade("b2", price="120")])
    row = client.get("/v1/pnl").json()["positions"][0]
    assert row["mark_price"] == "120"        # no mark posted -> last fill price


def test_pnl_marks_unpriced_positions(client):
    # a position with no mark and no last price can't happen via trades,
    # so check the combined-totals path instead
    ingest(client, "binance", [trade("b1", price="100")])
    ingest(client, "coinbase", [trade("c1", symbol="ETH-USD", price="50")])
    report = client.get("/v1/pnl").json()
    assert len(report["positions"]) == 2
    assert report["unpriced"] == []


def test_short_positions_when_allowed(short_client):
    ingest(short_client, "binance", [trade("s1", side="sell", price="100", quantity="2")])
    p = _pos(short_client)
    assert p["quantity"] == "-2"
    assert p["avg_cost"] == "100"

    # cover 1 @90 -> realized (100-90)*1 = 10
    ingest(short_client, "binance", [trade("b1", price="90")])
    p = _pos(short_client)
    assert p["quantity"] == "-1"
    assert p["realized_pnl"] == "10"

    # buy 2 @80 -> covers remaining short (+20) and flips long 1 @80
    ingest(short_client, "binance", [trade("b2", price="80", quantity="2")])
    p = _pos(short_client)
    assert p["quantity"] == "1"
    assert p["avg_cost"] == "80"
    assert p["realized_pnl"] == "30"
