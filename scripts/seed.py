#!/usr/bin/env python3
"""Generate realistic synthetic trade data (no company/exchange data needed).

    python scripts/seed.py                  # writes data/sample_trades.json
    python scripts/seed.py --yfinance       # anchor prices to real Yahoo closes
    python scripts/seed.py --post URL       # also ingest into a running server

Why synthetic: public APIs expose market prices, not account fills, so real
trade records have to be fabricated. Default mode walks prices with a seeded
RNG (fully reproducible). --yfinance keeps the same fabricated fills but snaps
prices to actual historical closes, so the dataset tracks real market moves.

The dataset deliberately embeds the edge cases the service is built for:
a duplicated fill, a base-asset fee, a missing trade id, an oversell attempt,
and a reconciliation snapshot with a planted break.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

BASE_TS = datetime(2026, 10, 12, tzinfo=UTC)
RNG = random.Random(20261020)

# venue -> (native symbol, Yahoo ticker, start price, fill count)
BOOKS = [
    ("binance", "BTCUSDT", "BTC-USD", 67000.0, 8),
    ("coinbase", "ETH-USD", "ETH-USD", 3400.0, 8),
    ("coindcx", "B-SOL_USDT", "SOL-USD", 150.0, 6),
]


def num(x: float) -> str:
    return f"{x:.8f}".rstrip("0").rstrip(".")


def price_walk(start: float, n: int) -> list[float]:
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + RNG.gauss(0, 0.012)))
    return prices


def yf_closes(ticker: str, n: int) -> list[float]:
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance not installed; run: pip install -e '.[data]' (or drop --yfinance)")
    hist = yf.Ticker(ticker).history(period="1mo", interval="1h")
    closes = [float(c) for c in hist["Close"].dropna().tolist()]
    if not closes:
        sys.exit(f"no yfinance data for {ticker}")
    step = max(1, len(closes) // n)
    return closes[::step][:n]


def binance_fill(i: int, ts_ms: int, side: str, price: float, qty: float) -> dict:
    # alternate fee asset so the dataset exercises base-asset fee conversion
    fee_asset = "BTC" if i % 2 == 0 else "USDT"
    fee = qty * 0.001 if fee_asset == "BTC" else qty * price * 0.001
    return {
        "id": 900000 + i, "orderId": 100000 + i, "symbol": "BTCUSDT",
        "price": num(price), "qty": num(qty), "quoteQty": num(price * qty),
        "commission": num(fee), "commissionAsset": fee_asset,
        "time": ts_ms, "isBuyer": side == "buy", "isMaker": RNG.random() < 0.5,
    }


def coinbase_fill(i: int, ts: datetime, side: str, price: float, qty: float) -> dict:
    return {
        "trade_id": f"cb-{50000 + i}", "product_id": "ETH-USD",
        "side": side.upper(), "price": num(price), "size": num(qty),
        "fee": num(qty * price * 0.0015),
        "created_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def coindcx_fill(i: int, ts_ms: int, side: str, price: float, qty: float) -> dict:
    return {
        "id": f"dcx-{70000 + i}", "symbol": "B-SOL_USDT", "side": side,
        "price": round(price, 2), "quantity": round(qty, 4),
        "fee": round(qty * price * 0.001, 4), "timestamp": ts_ms,
    }


def gen_fills(venue: str, prices: list[float]) -> tuple[list[dict], float]:
    """Buy-heavy first half, sell second half -> realized PnL + ending inventory."""
    inventory = 0.0
    fills = []
    for i, price in enumerate(prices):
        ts = BASE_TS + timedelta(hours=4 * i)
        ts_ms = int(ts.timestamp() * 1000)
        buy_leg = i < len(prices) * 0.6 or inventory <= 0
        if buy_leg:
            side, qty = "buy", round(RNG.uniform(0.1, 0.9), 4)
        else:
            side = "sell"
            qty = round(min(RNG.uniform(0.1, 0.5), inventory * 0.6), 4)
            if qty <= 0:
                continue
        inventory += qty if side == "buy" else -qty
        if venue == "binance":
            fills.append(binance_fill(i, ts_ms, side, price, qty))
        elif venue == "coinbase":
            fills.append(coinbase_fill(i, ts, side, price, qty))
        else:
            fills.append(coindcx_fill(i, ts_ms, side, price, qty))
    return fills, round(inventory, 8)


def build(mode: str) -> dict:
    batches = []
    internal = {}

    for venue, native_symbol, ticker, start, n in BOOKS:
        prices = yf_closes(ticker, n) if mode == "yfinance" else price_walk(start, n)
        fills, inventory = gen_fills(venue, prices)
        internal[venue] = inventory
        batches.append({"endpoint": f"/v1/venues/{venue}/trades", "payload": fills})

    # Canonical path: a venue with no adapter (kraken) still ingests via /v1/trades.
    canonical = [
        {"venue_trade_id": "kr-1", "symbol": "DOGE-USDT", "side": "buy",
         "price": "0.21", "quantity": "500", "executed_at": (BASE_TS + timedelta(hours=2)).isoformat()},
        {"symbol": "DOGE-USDT", "side": "buy", "price": "0.22", "quantity": "300",
         "executed_at": (BASE_TS + timedelta(hours=6)).isoformat()},  # no id -> derived
    ]
    batches.append({"endpoint": "/v1/trades",
                    "payload": {"venue": "kraken", "trades": canonical}})

    # Deliberate edge cases:
    # - replay of the first binance fill -> duplicate on ingest
    first_binance = dict(batches[0]["payload"][0])
    # - oversell: selling far more than the generated binance inventory
    oversell = {"venue": "binance", "trades": [
        {"venue_trade_id": "ovr-1", "symbol": "BTCUSDT", "side": "sell",
         "price": num(internal["binance"] and 70000.0),
         "quantity": num(internal["binance"] + 50)}]}
    batches.append({"endpoint": "/v1/venues/binance/trades", "payload": [first_binance]})
    batches.append({"endpoint": "/v1/trades", "payload": oversell})

    marks = [
        {"symbol": "BTC-USDT", "price": "69500", "source": "seed"},
        {"symbol": "ETH-USD", "price": "3350", "source": "seed"},
        {"symbol": "SOL-USDT", "price": "142", "source": "seed"},
        {"symbol": "DOGE-USDT", "price": "0.25", "source": "seed"},
    ]
    # Planted recon break: venue reports slightly less BTC than the ledger.
    reconcile = {"venue": "binance", "positions": [
        {"symbol": "BTC-USDT", "quantity": num(internal["binance"] - 0.02)}]}

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "note": "synthetic fills; see scripts/seed.py and PROCESS.md",
        "batches": batches,
        "marks": marks,
        "reconcile": reconcile,
    }


def post(client_base: str, doc: dict) -> None:
    def call(path: str, body, key: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        req = Request(client_base + path, data=json.dumps(body).encode(),
                      headers=headers, method="POST")
        with urlopen(req) as resp:
            return json.loads(resp.read())

    for i, batch in enumerate(doc["batches"]):
        res = call(batch["endpoint"], batch["payload"], key=f"seed-{i}")
        print(f"{batch['endpoint']:32s} accepted={res.get('accepted')} "
              f"duplicates={res.get('duplicates')} rejected={res.get('rejected')}")
    print("marks:", len(call("/v1/marks", {"marks": doc["marks"]})))
    rep = call("/v1/reconcile", doc["reconcile"])
    print(f"reconcile: compared={rep['compared']} matched={rep['matched']} "
          f"breaks={rep['breaks']} (run_id={rep['run_id']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yfinance", action="store_true",
                    help="anchor prices to real Yahoo Finance closes")
    ap.add_argument("--post", metavar="URL", help="ingest into a running server")
    ap.add_argument("--out", default="data/sample_trades.json")
    args = ap.parse_args()

    doc = build("yfinance" if args.yfinance else "synthetic")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(f"wrote {out} ({sum(len(b['payload'] if isinstance(b['payload'], list) else b['payload']['trades']) for b in doc['batches'])} fills)")

    if args.post:
        post(args.post.rstrip("/"), doc)


if __name__ == "__main__":
    main()
