# trade-pnl-service

A **trade recorder and PnL backend** for crypto desks. It ingests executed
fills from multiple venues, maintains a position ledger per
(venue, account, symbol) under weighted-average-cost accounting, and reports
realized + unrealized PnL — guarded by idempotent ingestion, position
reconciliation and an oversell check.

```
raw venue payloads ──► venue adapters ──► canonical trades ──► atomic ingest
                                                                │  ┌─► trades (append-only)
            Idempotency-Key + UNIQUE(venue, venue_trade_id) ────┤
            oversell check + ledger math ───────────────────────┘  └─► positions
                                                                       │  ├─► GET /v1/positions
                          marks ───────────────────────────────────────┤  ├─► GET /v1/pnl
                          external snapshot ───────────────────────────┴─► POST /v1/reconcile
```

## Features

| Requirement | What ships |
|---|---|
| Multi-venue ingestion | Venue adapters for **Binance, Coinbase, CoinDCX** (`POST /v1/venues/{venue}/trades`) plus a venue-agnostic canonical endpoint (`POST /v1/trades`). Adding a venue = one adapter file + one registry entry. |
| Trade recording | Append-only `trade` table; every row keeps the **raw venue payload** for audit and future trade-level recon. |
| Position tracking | Signed-quantity ledger per (venue, account, symbol): quantity, weighted-average cost, realized PnL, last price, fill count. |
| PnL reporting | Realized (booked on every closing fill) + unrealized (from posted marks, falls back to last fill price) via `GET /v1/pnl`. |
| Idempotency | **Two layers**: `Idempotency-Key` request dedup (stored response replayed, key reuse with a different body → `409`) and `UNIQUE(venue, venue_trade_id)` entity dedup (re-delivered fills report `duplicate`). |
| Reconciliation | `POST /v1/reconcile` diffs an external position snapshot against the ledger per symbol, flags `break`s beyond a tolerance, and persists each run for audit. |
| Oversell check | Sells beyond available inventory are rejected per-item (`OVERSELL`) with zero state change; `ALLOW_SHORT` opt-in enables short positions. |
| Exact money math | `Decimal` end to end — stored as exact strings (SQLite-safe), emitted as strings on the wire like real exchange APIs. No floats anywhere. |

## Documentation map

| Doc | What's inside |
|-----|---------------|
| [SETUP.md](SETUP.md) | Full setup guide: prerequisites, install, verify, run, troubleshoot |
| [CODEBASE.md](CODEBASE.md) | File-by-file walkthrough, interview diagram, request trace, trading glossary |
| [DESIGN.md](DESIGN.md) | Architecture, data model, design decisions and scaling notes |
| [PROCESS.md](PROCESS.md) | Step-by-step build story, every trade-off weighed |
| [ELI5.md](ELI5.md) | The whole project explained with a fruit-stall analogy |
| [AI_USAGE.md](AI_USAGE.md) | How AI tools were used and how the output was verified |

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                    # 47 tests, ~1s
python scripts/demo.py    # end-to-end walkthrough, no server needed
uvicorn tradepnl.main:app --reload   # serves on :8000, docs at /docs
```

See [SETUP.md](SETUP.md) for the detailed guide and troubleshooting.

## Configuration

All configuration is via environment variables (`Settings.from_env()`):

| Env var | Default | Effect |
|---------|---------|--------|
| `DATABASE_URL` | `sqlite:///./tradepnl.db` | SQLAlchemy URL. Point at Postgres in production (`postgresql+psycopg://...`). |
| `API_KEY` | unset | When set, **every `/v1/*` route requires `X-API-Key`** (401 otherwise). `/health` stays open. |
| `ALLOW_SHORT` | `false` | `true` allows positions to go negative (short accounting). |
| `RECON_TOLERANCE` | `0.00000001` | Default diff tolerance for reconciliation; overridable per request. |

## API reference

Conventions:

- **All numbers are decimal strings** (`"67250.55"`), never JSON floats.
- **Writes are idempotent**: send `Idempotency-Key: <key>` on ingestion
  requests; a replay returns the stored response with
  `Idempotency-Replayed: true`.
- **Batch results are per item**: `{status: accepted | duplicate | rejected}`
  for every fill; one bad fill never sinks the batch.
- Errors return `{"detail": "..."}`. Status codes: `200` ok · `401` bad/absent
  API key · `404` unknown venue or run · `409` idempotency-key conflict ·
  `422` validation/payload error.

### `GET /health`
Liveness probe (open even when `API_KEY` is set).
```json
{"status": "ok", "venues": ["binance", "coindcx", "coinbase"]}
```

### `POST /v1/trades` — canonical ingestion
Accepts a batch of normalized trades. `venue` is free-form (canonical path
works for venues without an adapter). `venue_trade_id` may be omitted — a
deterministic `venue-derived-<sha256>` id is generated so retries still
dedupe.

```bash
curl -X POST localhost:8000/v1/trades \
  -H 'Idempotency-Key: import-1' -H 'Content-Type: application/json' \
  -d '{"venue": "binance", "trades": [
        {"venue_trade_id": "t-1", "symbol": "BTCUSDT", "side": "buy",
         "price": "67000.5", "quantity": "0.5",
         "fee": "0.000005", "fee_asset": "BTC"},
        {"venue_trade_id": "t-2", "symbol": "BTC-USDT", "side": "sell",
         "price": "69000", "quantity": "0.2"}
      ]}'
```
```json
{"venue": "binance", "accepted": 2, "duplicates": 0, "rejected": 0,
 "results": [
   {"venue_trade_id": "t-1", "status": "accepted", "code": null, "detail": null},
   {"venue_trade_id": "t-2", "status": "accepted", "code": null, "detail": null}]}
```

Symbols accept `BTC-USDT`, `BTC_USDT`, `B-BTC_USDT` or `BTCUSDT` — all
normalize to `BTC-USDT`.

### `POST /v1/venues/{venue}/trades` — raw venue ingestion
Body is the venue's own payload shape (a single fill or a list); the venue's
adapter maps it to canonical form. `?account=` sets the target account
(default `main`).

```bash
curl -X POST localhost:8000/v1/venues/binance/trades -d '[
  {"id": 9001, "symbol": "BTCUSDT", "price": "67000.5", "qty": "0.5",
   "commission": "0.000005", "commissionAsset": "BTC",
   "time": 1729353600000, "isBuyer": true}
]'
```

Supported venues: `binance`, `coinbase`, `coindcx`. Unknown venue → `404`,
malformed payload → `422` with the offending field named.

### `GET /v1/trades`
Query params: `venue`, `account`, `symbol`, `since` (ISO-8601), `limit`
(1–1000, default 100), `offset`. Ordered by `executed_at`.

```json
[{"venue": "binance", "venue_trade_id": "9001", "account": "main",
  "symbol": "BTC-USDT", "side": "buy", "price": "67000.5",
  "quantity": "0.5", "fee": "0.000005", "fee_asset": "BTC",
  "executed_at": "2024-10-19T16:00:00Z", "ingested_at": "2026-10-19T22:30:20Z"}]
```

### `GET /v1/positions`
Query params: `venue`, `account`, `symbol`. Returns the ledger state.

```json
[{"venue": "binance", "account": "main", "symbol": "BTC-USDT",
  "quantity": "0.4", "avg_cost": "67250.87750375",
  "realized_pnl": "398.7659990", "last_price": "68000",
  "trade_count": 3, "updated_at": "2026-10-19T22:30:20Z"}]
```

`quantity` is signed (negative = short). `avg_cost` is the weighted-average
basis of the open side; `realized_pnl` accumulates and never resets.

### `GET /v1/pnl`
Same filters as positions. Joins positions × marks (falls back to each
position's last fill price; positions with neither appear in `unpriced`).

```json
{"as_of": "2026-10-19T22:31:01Z",
 "positions": [
   {"venue": "binance", "account": "main", "symbol": "BTC-USDT",
    "quantity": "0.4", "avg_cost": "67250.87750375", "mark_price": "69500",
    "realized_pnl": "398.7659990", "unrealized_pnl": "899.648998500",
    "total_pnl": "1298.414997500"}],
 "totals": {"realized_pnl": "398.7659990",
            "unrealized_pnl": "899.648998500",
            "total_pnl": "1298.414997500"},
 "unpriced": []}
```

### `POST /v1/marks` / `GET /v1/marks`
Upsert / list mark prices (symbol-level, canonicalized).

```bash
curl -X POST localhost:8000/v1/marks \
  -d '{"marks": [{"symbol": "BTC-USDT", "price": "69500", "source": "manual"}]}'
```

### `POST /v1/reconcile` / `GET /v1/reconcile/{run_id}`
POST takes a venue-reported position snapshot and diffs it against the
ledger; every compared row is `match` or `break` and the run is persisted.

```bash
curl -X POST localhost:8000/v1/reconcile -d '{
  "venue": "binance",
  "positions": [{"symbol": "BTC-USDT", "quantity": "0.35"},
                {"symbol": "ETH-USDT", "quantity": "5"}]}'
```
```json
{"run_id": 1, "venue": "binance", "account": "main",
 "tolerance": "0.00000001", "compared": 2, "matched": 1, "breaks": 1,
 "results": [
   {"symbol": "BTC-USDT", "internal": "0.4", "external": "0.35",
    "diff": "0.05", "status": "break"},
   {"symbol": "ETH-USDT", "internal": "5", "external": "5",
    "diff": "0", "status": "match"}]}
```

## Sample data

No company/exchange data is used — `scripts/seed.py` generates a realistic
synthetic dataset (committed at `data/sample_trades.json`) with venue-native
payloads for all three adapters plus canonical trades. It deliberately
contains one duplicate fill, a base-asset fee, a missing trade id, an
oversell and a recon snapshot with a planted break.

```bash
python scripts/seed.py                              # regenerate the dataset
python scripts/seed.py --post http://localhost:8000 # ingest into a running server
pip install -e ".[data]" && python scripts/seed.py --yfinance
# ^ optional: anchors fill prices to real Yahoo Finance hourly closes
```

## Project layout

```
tradepnl/
  main.py              # create_app() factory
  api.py               # routes + idempotency-key handling + auth dep
  config.py            # env-driven Settings
  db.py                # engine creation, DDL
  models.py            # SQLModel tables: trade, position, markprice,
                       #   idempotencyrecord, reconcilerun
  schemas.py           # request/response models (decimals as strings)
  decimals.py          # DecimalText column type + DecStr serializer
  errors.py            # OversellError, UnknownVenueError
  services/
    ingest.py          # batch loop: dedup → apply → commit, per-item results
    ledger.py          # signed-quantity AVCO accounting + oversell check
    reconcile.py       # external snapshot diffing + persisted runs
  venues/
    normalize.py       # CanonicalTrade, split_symbol, derive_trade_id
    binance.py         # Binance myTrades -> canonical
    coinbase.py        # Coinbase fill -> canonical
    coindcx.py         # CoinDCX trade -> canonical
tests/                 # 47 tests across 8 files
scripts/demo.py        # end-to-end walkthrough (TestClient, no server)
scripts/seed.py        # synthetic dataset generator (+ --yfinance mode)
data/sample_trades.json
```

## Testing & verification

```bash
pytest                          # 47 tests, ~1s on throwaway SQLite DBs
pytest tests/test_pnl.py -v     # just the money math
python scripts/demo.py          # full narrated walkthrough of every feature
```

The suite covers: all three adapters, dedup (unique trade id), request-level
idempotency (replay + key-conflict), oversell (rejected, batch-isolated,
zero-position), AVCO math (buys, sells, fees in quote and base assets),
short accounting, marks and unrealized PnL, reconciliation (match, break,
missing-on-either-side, persisted runs, tolerance), auth, validation and
every documented status code.
