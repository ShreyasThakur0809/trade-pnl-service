"""End-to-end walkthrough against an in-memory app (no server needed).

    python scripts/demo.py

Ingests venue-native and canonical trades, replays a request to show
idempotency, attempts an oversell, posts marks, prints the PnL report and
finishes with a reconciliation run that contains a planted break.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tradepnl.config import Settings
from tradepnl.main import create_app


def show(title: str, payload) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2))


def main() -> None:
    client = TestClient(create_app(Settings(database_url="sqlite://")))

    show("1. Ingest a raw Binance fill (buy 0.5 BTC)",
         client.post("/v1/venues/binance/trades", json=[{
             "id": 9001, "symbol": "BTCUSDT", "price": "67000.5", "qty": "0.5",
             "commission": "0.000005", "commissionAsset": "BTC",
             "time": 1729353600000, "isBuyer": True,
         }]).json())

    show("2. Ingest a raw Coinbase fill (buy 4 ETH)",
         client.post("/v1/venues/coinbase/trades", json=[{
             "trade_id": "cb-42", "product_id": "ETH-USD", "side": "BUY",
             "price": "3400", "size": "4", "fee": "2.5",
             "created_at": "2026-10-19T12:00:00Z",
         }]).json())

    show("3. Ingest a raw CoinDCX fill (buy 3 SOL)",
         client.post("/v1/venues/coindcx/trades", json=[{
             "id": "dcx-7", "symbol": "B-SOL_USDT", "side": "buy",
             "price": 150, "quantity": 3, "fee": 0.05,
             "timestamp": 1729353600000,
         }]).json())

    show("4. Canonical batch: sell 0.2 BTC (realizes PnL) + buy 5 ETH on binance",
         client.post("/v1/trades", json={"venue": "binance", "trades": [
             {"venue_trade_id": "b-sell1", "symbol": "BTC-USDT", "side": "sell",
              "price": "69000", "quantity": "0.2", "fee": "1", "fee_asset": "USDT"},
             {"venue_trade_id": "b-eth1", "symbol": "ETHUSDT", "side": "buy",
              "price": "3100", "quantity": "5"},
         ]}).json())

    key_body = {"venue": "binance", "trades": [
        {"venue_trade_id": "b-77", "symbol": "BTC-USDT",
         "side": "buy", "price": "68000", "quantity": "0.1"}]}
    client.post("/v1/trades", json=key_body, headers={"Idempotency-Key": "demo-key-1"})
    r = client.post("/v1/trades", json=key_body, headers={"Idempotency-Key": "demo-key-1"})
    print("\n=== 5. Same request replayed with Idempotency-Key ===")
    print(f"   status={r.status_code} idempotency-replayed="
          f"{r.headers.get('idempotency-replayed', 'false')} "
          f"(position gained 0.1 BTC exactly once)")

    show("6. Oversell attempt (sell 99 BTC) is rejected", client.post(
        "/v1/trades", json={"venue": "binance", "trades": [
            {"venue_trade_id": "b-bad", "symbol": "BTC-USDT", "side": "sell",
             "price": "70000", "quantity": "99"}]},
    ).json())

    show("7. Positions", client.get("/v1/positions").json())

    client.post("/v1/marks", json={"marks": [
        {"symbol": "BTC-USDT", "price": "69500"},
        {"symbol": "ETH-USDT", "price": "3050"},
        {"symbol": "ETH-USD", "price": "3050"},
        {"symbol": "SOL-USDT", "price": "142"},
    ]})
    show("8. PnL report", client.get("/v1/pnl").json())

    show("9. Reconcile binance snapshot (BTC 0.05 break, ETH matches)",
         client.post("/v1/reconcile", json={"venue": "binance", "positions": [
             {"symbol": "BTC-USDT", "quantity": "0.35"},
             {"symbol": "ETH-USDT", "quantity": "5"},
         ]}).json())


if __name__ == "__main__":
    main()
