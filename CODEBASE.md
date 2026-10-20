# Codebase walkthrough

A file-by-file explanation of the system: what each component does, which
problem it solves, and how a request flows through the code. Pair this with
DESIGN.md (decisions) and PROCESS.md (how it was built).

## The 30-second version

> "It records trade fills from multiple exchanges, keeps a running position
> per venue/account/symbol, and reports realized and unrealized PnL. Raw
> exchange payloads go through venue adapters into one canonical trade
> shape. Every write is idempotent — retries can't double-count. Sells that
> exceed inventory are rejected. And you can POST an exchange's balance
> snapshot to reconcile it against the internal ledger."

## Architecture (the diagram to draw in an interview)

```
 VENUE-NATIVE PAYLOADS            NORMALIZATION              WRITE PATH (atomic per trade)
                                                                                    REPORTS
 binance myTrades ─┐                                                                                 
                  ├──► venues/binance.py ─┐                    ┌─ 1. dedup check                     GET /v1/trades
 coinbase fills ──┼─► venues/coinbase.py ─┤  CanonicalTrade    │    SELECT (venue, venue_trade_id)   GET /v1/positions
                  ├──► venues/coindcx.py ─┤                    │    UNIQUE constraint = backstop    GET /v1/pnl
 coindcx fills ───┘                       ├───────────────────►│          │                          GET /v1/marks
                                          │   venues/__init__  │          ▼                          POST /v1/marks
 canonical JSON ──────────────────────────┘   .adapt()         │   2. ledger apply (ledger.py)      POST /v1/reconcile
         POST /v1/trades                                         │      position + realized PnL     GET /v1/reconcile/{id}
                                                                 │      + OVERSELL check
 Idempotency-Key ─► _idempotent() wraps the whole write          │          │
 (replay = stored response; same key + new body = 409)           │          ▼
                                                                 │   3. commit: trade row + position
                                                                 │      accepted / duplicate / rejected
                                                                 ▼
                                                         ┌───────┴───────────────────────────┐
                                                         │              SQLite/PG            │
                                                         │  trade · position · markprice ·   │
                                                         │  idempotencyrecord · reconcilerun │
                                                         └───────────────────────────────────┘
```

One line per layer when explaining: **"adapters normalize, the ingest
service deduplicates and applies fills atomically, the ledger owns
accounting, and reports are just reads over the same tables."**

## One request, traced through the code

`POST /v1/venues/binance/trades` with a Binance `myTrades` payload:

1. **Route** — `ingest_venue_trades()` in `tradepnl/api.py`. FastAPI parses
   the JSON body; `require_api_key` dependency runs first (401 if `API_KEY`
   is configured and the header is wrong/missing).
2. **Adapt** — `venues.adapt("binance", payload)` looks up
   `ADAPTERS["binance"]` → `binance.normalize()` maps the raw fill to a
   `CanonicalTrade`: `id`→`venue_trade_id`, `BTCUSDT`→`BTC-USDT` via
   `split_symbol()`, `isBuyer`→`side`, `qty`→`quantity`, `commission`/`commissionAsset`
   →`fee`/`fee_asset`, ms-epoch `time`→`executed_at`. Unknown venue → 404;
   malformed payload → 422.
3. **Idempotency wrapper** — `_idempotent()` hashes the raw request body
   (SHA-256) and looks up the `Idempotency-Key` header in
   `idempotencyrecord`. Replay with same body → stored response +
   `Idempotency-Replayed: true`. Same key, different body → 409.
4. **Ingest loop** — `services/ingest.ingest()` iterates the canonical
   trades, each in its own transaction:
   - `SELECT` for an existing `(venue, venue_trade_id)` → `duplicate`.
   - Builds the `Trade` row and calls `ledger.apply_trade()`.
   - `OversellError` → rollback, item marked `rejected` with `OVERSELL`.
   - `IntegrityError` (lost a race on the unique constraint) → `duplicate`.
   - Success → one commit for trade + position together → `accepted`.
5. **Ledger** — `apply_trade()` loads the `Position` row for
   `(venue, account, symbol)` (creates it if absent), normalizes the fee to
   quote currency, and updates `quantity`, `avg_cost`, `realized_pnl`,
   `last_price`, `trade_count` per the accounting rules below.
6. **Response** — `IngestResult` with per-item `TradeResult`s; if a key was
   supplied, the response body is stored under it before returning.

## Component → problem map

| Requirement / concern      | Component                          | Code |
|----------------------------|------------------------------------|------|
| Ingest many venues         | adapter registry + normalizers     | `venues/__init__.py`, `venues/{binance,coinbase,coindcx}.py` |
| One internal trade shape   | `CanonicalTrade`, `split_symbol`   | `venues/normalize.py` |
| Record trades durably      | `Trade` table, `ingest()`          | `models.py`, `services/ingest.py` |
| Idempotency (request)      | `_idempotent()`, `IdempotencyRecord` | `api.py`, `models.py` |
| Dedup (entity)             | `UNIQUE(venue, venue_trade_id)` + pre-check | `models.py`, `services/ingest.py` |
| Track positions            | `Position`, `apply_trade()`        | `models.py`, `services/ledger.py` |
| Report PnL                 | `pnl_report()`, `MarkPrice`        | `api.py`, `models.py` |
| Oversell check             | `OversellError`, sell branch       | `errors.py`, `services/ledger.py` |
| Reconciliation             | `reconcile()`, `ReconcileRun`      | `services/reconcile.py`, `models.py` |
| Exact money math           | `DecimalText`, `DecStr`            | `decimals.py` |
| Config                     | `Settings` (env-driven)            | `config.py` |
| Optional auth              | `require_api_key` dependency       | `api.py` |
| App wiring                 | `create_app()` factory             | `main.py`, `db.py` |

## File by file

### `tradepnl/main.py`
`create_app(settings)` — app factory. Builds the engine, creates tables,
stores `settings`/`engine` on `app.state`, mounts the router. A factory (not
a module-level app) so tests can spin up isolated apps with their own DB and
flags. `app = create_app()` at the bottom is what uvicorn serves.

### `tradepnl/config.py`
`Settings` — frozen dataclass with `database_url`, `api_key`, `allow_short`,
`recon_tolerance`, read from env by `Settings.from_env()`. Frozen so config
can't drift mid-request.

### `tradepnl/db.py`
`make_engine()` — SQLite gets `check_same_thread=False` (TestClient/uvicorn
touch the DB across threads) and `StaticPool` for in-memory URLs (every
connection must share the same in-memory DB). `init_db()` runs
`create_all` — import of `models` registers table metadata first.

### `tradepnl/decimals.py` — the precision layer
- `DecimalText` — SQLAlchemy `TypeDecorator` storing `Decimal` as its exact
  string. Exists because SQLite's NUMERIC affinity silently degrades to
  IEEE-754 REAL, which cannot represent `0.00000001` or most decimal prices.
  All arithmetic stays in Python `Decimal`.
- `DecStr` — Pydantic serializer that emits decimals as plain strings on the
  wire (`"67250.55"`), the same convention exchange APIs use.

### `tradepnl/models.py` — the schema
- `Trade` — one normalized fill. `UNIQUE(venue, venue_trade_id)` is the
  dedup backstop; `raw` keeps the original venue payload for audit and
  future trade-level recon; `ingest_key` records which idempotency key
  carried it. Indexes back the trade-listing filters.
- `Position` — running state per `(venue, account, symbol)`: `quantity`
  (signed: long + / short −), `avg_cost` (basis for the open side),
  `realized_pnl` (accumulates forever), `last_price` (mark fallback),
  `trade_count` (how many fills folded in).
- `MarkPrice` — latest mark per symbol for unrealized PnL.
- `IdempotencyRecord` — key → `{endpoint, request_hash, stored response}`.
  What makes request-level replay possible.
- `ReconcileRun` — persisted result of one recon run (audit trail).
- `utcnow()` — single source for timezone-aware timestamps.

### `tradepnl/schemas.py` — the wire contract
Pydantic request/response models. `TradeIn` validates canonical input
(`price`, `quantity` > 0, fee ≥ 0); `TradeResult`/`IngestResult` are the
per-item batch response; `PnlRow`/`PnlReport` shape the PnL report;
`ReconcileIn`/`ReconcileReport` shape recon I/O. Every money field
serializes via `DecStr`.

### `tradepnl/errors.py`
`OversellError` — carries venue/account/symbol/available/requested so the
rejection message is self-explanatory. `UnknownVenueError` for unregistered
adapter names (surfaced as 404).

### `tradepnl/api.py` — the HTTP surface
- **Dependencies** — `get_session` (request-scoped `Session`),
  `require_api_key` (401s when `API_KEY` is set; `/health` deliberately
  open), `AuthDeps` list applied to every `/v1` route.
- **`_idempotent()`** — the request-level idempotency described above.
  Handles the concurrent same-key race by falling back to the stored
  record on `IntegrityError`.
- **`_to_canonical()`** — canonical `TradeIn` → `CanonicalTrade`; derives a
  deterministic `venue-derived-<sha256>` id when the caller sends none, so
  id-less retries still dedupe.
- **Endpoints** — `GET /health`; `POST /v1/trades` and
  `POST /v1/venues/{venue}/trades` (the two ingestion doors);
  `GET /v1/trades` (filters + paging); `GET /v1/positions`;
  `GET /v1/pnl` (joins positions × marks, falls back to `last_price`,
  lists `unpriced`); `POST`/`GET /v1/marks` (upsert + list);
  `POST /v1/reconcile` + `GET /v1/reconcile/{run_id}`.
- Small mappers `_trade_out`/`_position_out`/`_report` keep response
  shaping out of the service layer.

### `tradepnl/services/ingest.py` — the write pipeline
`ingest()` runs the batch loop described in the trace: dedup SELECT → build
row → `apply_trade` → commit, all inside one try per item. Per-item
transactions mean a bad fill never sinks the batch, and trade+position
commit atomically. Returns `IngestResult` with per-fill outcomes.

### `tradepnl/services/ledger.py` — the accounting core
- `fee_in_quote()` — normalizes fees: quote-asset fee as-is, base-asset fee
  × fill price, third-asset fee excluded (documented).
- `apply_trade()` — signed-quantity AVCO accounting, four cases:
  - **buy while flat/long** → weighted-average basis, fee folded in.
  - **buy while short** → covers: `(avg_cost − price) × covered` realized;
    overshoot flips long at the fill price.
  - **sell while long** → oversell check first (`qty > position` and
    shorts disabled → `OversellError`), then `(price − avg_cost) × closed`
    realized; overshoot can flip short when allowed.
  - **sell while flat/short** → opens/extends the short with weighted
    sell basis (requires `ALLOW_SHORT`; a sell on a zero position with
    shorts off is an oversell too).
  Flat positions reset `avg_cost` to zero; `realized_pnl` never resets.

### `tradepnl/services/reconcile.py` — the auditor
`reconcile()` loads all internal positions for the venue/account, indexes
the external snapshot by symbol, diffs every symbol present on either side
(missing side = 0), and marks `|internal − external| > tolerance` as a
`break`. The run — including every compared row — is committed to
`reconcilerun` so the report is auditable.

### `tradepnl/venues/normalize.py` — shared adapter plumbing
- `CanonicalTrade` — the one shape all adapters emit.
- `split_symbol()` — `"BTC-USDT"`/`"BTC_USDT"`/`"B-BTC_USDT"`/`"BTCUSDT"`
  → `("BTC-USDT", "BTC", "USDT")` via separator scan, then a known-quote
  suffix list (`USDT`, `USD`, `INR`…) with aliases (`XBT→BTC`).
- `derive_trade_id()` — deterministic `venue-derived-<sha256>` for payloads
  with no native id; same payload → same id → retries dedupe.

### `tradepnl/venues/{binance,coinbase,coindcx}.py` — field mapping
Each `normalize(payload)` translates one venue's fill shape:

| Canonical field   | Binance `myTrades`      | Coinbase fill        | CoinDCX trade            |
|-------------------|-------------------------|----------------------|--------------------------|
| `venue_trade_id`  | `id`                    | `trade_id`           | `id` / `trade_id`        |
| `symbol`          | `symbol` (`BTCUSDT`)    | `product_id`         | `symbol` (`B-SOL_USDT`)  |
| `side`            | `isBuyer` bool          | `side` (`BUY`)       | `side` (`buy`)           |
| `quantity`        | `qty`                   | `size`               | `quantity`/`qty`         |
| `fee`             | `commission` (+asset)   | `fee` (quote)        | `fee` (quote)            |
| `executed_at`     | `time` (ms epoch)       | `created_at` (ISO)   | `timestamp` (ms epoch)   |

`venues/__init__.py` keeps the `ADAPTERS` registry — adding Kraken is one
file + one dict entry.

### `scripts/demo.py`
In-process end-to-end narrative (TestClient, no server): three venue fills →
canonical batch → idempotent replay → oversell rejection → positions →
marks → PnL → recon with a planted break. What a reviewer runs first.

### `scripts/seed.py`
Deterministic dataset generator — venue-native payloads from a seeded RNG;
optional `--yfinance` mode anchors fill prices to real Yahoo closes.
Bakes in the edge cases (duplicate, base-asset fee, missing id, oversell,
planted recon break) and writes `data/sample_trades.json`.
`--post URL` ingests it into a running server.

### `tests/` — 47 tests, ~1s
`conftest.py` gives fresh-app fixtures (`client`, `auth_client`,
`short_client`) plus `trade()`/`ingest()` helpers. Files map 1:1 to
concerns: `test_ingest`, `test_idempotency`, `test_oversell`, `test_pnl`,
`test_reconcile`, `test_venues`, `test_api`, `test_sample_data` (the
committed dataset exercised through the real API).

## Trading terminology (the words Ravi may use)

| Term | Meaning, and where it lives in this system |
|------|--------------------------------------------|
| **Fill / trade / execution** | One completed buy or sell on an exchange. Stored as a `Trade` row. |
| **Venue** | An exchange you trade on (Binance, Coinbase, CoinDCX). |
| **Symbol / pair** | What was traded, e.g. `BTC-USDT`. |
| **Base / quote** | In `BTC-USDT`: base = BTC (the asset), quote = USDT (the money). Prices and PnL are in quote currency. |
| **Side** | `buy` or `sell` — direction of the fill. |
| **Position** | Net holdings of a symbol from all fills so far: `quantity` on the `Position` row. |
| **Long / short** | Position > 0 (own it) / position < 0 (sold borrowed, owe it). Shorts need `ALLOW_SHORT`. |
| **Inventory** | Desk slang for position — "we're holding 0.4 BTC". |
| **Cost basis / avg cost** | What you effectively paid per unit for the open position: `avg_cost`. Buys raise or lower it via weighted average. |
| **Realized PnL** | Profit locked in when a position is reduced/closed: `realized_pnl`. Sell 1 BTC @300 with basis 150 → +150 realized. |
| **Unrealized PnL** | Paper profit on what's still open: `(mark − avg_cost) × quantity`. Needs a `mark_price`. |
| **Mark price / mark-to-market** | Current fair price used to value open positions (`MarkPrice`); PnL falls back to last fill price. |
| **Fee / commission** | Venue's cut of a fill (`fee` + `fee_asset`). Base-asset fees (e.g. 0.0001 BTC) convert to quote at the fill price. |
| **Venue trade id** | The exchange's own fill identifier — our dedup key. |
| **Ledger** | The append-only record of fills plus derived position state. |
| **Reconciliation / recon** | Comparing *our* ledger to *the venue's* reported balances to catch missed or double-counted fills. |
| **Break** | A recon difference beyond `tolerance` — something needs investigating. |
| **Oversell** | Selling more of a symbol than the position holds. Rejected with `OVERSELL`. |
| **Idempotency** | Applying an operation twice gives the same result as once. Implemented at request level (`Idempotency-Key` → stored response) and entity level (`UNIQUE(venue, venue_trade_id)`). |
| **Dedup** | Skipping a fill the ledger already recorded — the practical half of idempotency. |
| **AVCO** | Average-cost accounting — `avg_cost` = weighted mean entry price. Alternative: **FIFO** (oldest lots close first — needs per-lot tracking). |
| **Tolerance** | Recon threshold (default 1e-8) below which rounding dust isn't a break. |
| **Snapshot** | A point-in-time dump of what the venue says we hold — the input to `/v1/reconcile`. |
| **Maker / taker** | Which side of the book your order hit; Binance payloads carry `isMaker` (stored in `raw`, unused for PnL). |

## Likely follow-up questions → where the answer lives

| Question | Point at |
|----------|----------|
| "Walk a trade through the system" | the trace section above — route → adapter → idempotency → ingest → ledger → response |
| "How is it idempotent?" | two layers: `_idempotent()` in `api.py` + unique constraint on `trade` |
| "Why not floats?" | `decimals.py` docstring; SQLite REAL corruption |
| "How do you handle a 30-second request?" | recon runs already persist as `ReconcileRun` → `202` + poll pattern in DESIGN.md |
| "What breaks it?" | PROCESS.md "Known limitations" — symbol heuristic, symbol-global marks, quantity-level recon |
| "How do you add Kraken?" | one adapter file + `ADAPTERS` entry — no other changes |
