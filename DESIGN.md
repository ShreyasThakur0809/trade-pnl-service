# Design

## What this service does

Ingests trade fills from multiple venues, maintains a per-(venue, account,
symbol) position ledger, and answers "what do we hold and how is it doing"
(positions, realized + unrealized PnL). It defends the ledger's integrity
with three mechanisms the task called out: idempotency, an oversell check,
and reconciliation against external snapshots.

```
            raw venue payloads                     canonical trades
                  │                                      │
        POST /v1/venues/{venue}/trades           POST /v1/trades
                  │                                      │
            venue adapter ────────────────► CanonicalTrade
            (binance/coinbase/coindcx)                     │
                  └────────────────┬───────────────────────┘
                                   ▼
                     ingest service ── Idempotency-Key replay check
                     per-trade txn        │
                     ┌────────────────────┴───────────┐
                     ▼                                ▼
             trades table                     position ledger
             UNIQUE(venue,                    (quantity, avg_cost,
              venue_trade_id)                  realized_pnl, last_price)
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       GET /v1/positions    GET /v1/pnl          POST /v1/reconcile
       (ledger view)        (ledger + marks)     (external snapshot diff)
```

## Data model

| table               | key fields                                                        |
|---------------------|-------------------------------------------------------------------|
| `trade`             | `(venue, venue_trade_id)` UNIQUE; symbol, side, price, qty, fee, ts, raw payload |
| `position`          | `(venue, account, symbol)` UNIQUE; quantity, avg_cost, realized_pnl, last_price |
| `markprice`         | `symbol` PK; price, source                                        |
| `idempotencyrecord` | `key` PK; endpoint, request_hash, stored response                 |
| `reconcilerun`      | id PK; venue, account, results JSON, matched/breaks counts        |

## Idempotency

An operation is idempotent when applying it more than once produces the same
result as applying it once. Trade recording needs it at two levels:

1. **Request level — `Idempotency-Key` header.** A client retrying a POST
   (timeout, flaky network, batch re-import) reuses the key. The first
   request's response body is stored under the key; a replay returns it
   verbatim with `Idempotency-Replayed: true` and does not re-apply. The key
   is bound to a SHA-256 of the request body, so reusing a key with a
   *different* payload is a **409 Conflict**, not a silent no-op.

2. **Entity level — natural dedup key.** Every trade carries the venue's own
   trade id. `UNIQUE(venue, venue_trade_id)` means a re-imported file or a
   keyless retry can never double-count: the row comes back `duplicate` in
   the batch result. Payloads with no native id get a deterministic
   `venue-derived-<sha256>` id, which dedupes retries identically.

The unique constraint is the backstop; a SELECT check runs first purely to
return a friendly `duplicate` status, and an `IntegrityError` on commit (a
lost race) is still mapped to `duplicate`, never a 500.

Batch semantics: each trade commits in its own transaction — the trade row
and its position update are atomic, and one rejected item never sinks the
rest of the batch. Results come back per item as
`accepted | duplicate | rejected`.

## Positions & PnL

Weighted-average cost (AVCO), signed-quantity ledger:

- **Buy** while flat/long: `avg_cost = (avg_cost·qty + q·p + fee_quote) / (qty + q)`.
- **Sell** while long: `realized += (p − avg_cost)·closed − fee_quote`.
- **Sell** while short: extends the short with a weighted sell basis.
- **Buy** while short: `realized += (avg_cost − p)·covered − fee_quote`;
  buying through zero flips long with basis = fill price.
- Flat positions reset `avg_cost` to 0; `realized_pnl` accumulates forever.

**Fees**: quote-currency fees apply directly; base-asset fees convert at the
trade's own price (a BTC commission on a BTC-USDT buy is extra cost basis).
Fees in a third asset (e.g. BNB) can't be converted without an extra price
feed — they're stored on the trade row but excluded from PnL. A real desk
would route them through a conversion price; noted in *Future work*.

**Unrealized PnL** needs a mark: `POST /v1/marks` upserts symbol-level marks
(`(mark − avg_cost) · qty`, sign-aware for shorts). With no mark, the report
falls back to the position's last fill price; positions with neither are
listed under `unpriced` instead of silently reporting zero.

**AVCO vs FIFO**: AVCO keeps the ledger O(1) per trade — one row per position,
no lot tracking. FIFO would need a `lots` table and partial-lot consumption;
it's the right call if tax-lot accuracy matters, and the trade table already
has everything needed to recompute PnL under either method.

## Oversell check

A sell that would push `(venue, account, symbol)` quantity below zero is
rejected with `OVERSELL` and touches nothing — including "selling something
never bought" (zero position still rejects). `ALLOW_SHORT=true` relaxes this
into signed-quantity accounting for venues/desks that run short. The check
sits in the ledger, not the API layer, so it holds for every ingestion path.

## Reconciliation

`POST /v1/reconcile` takes a snapshot of what the *venue* says we hold and
diffs it against the internal ledger, per symbol, within a tolerance
(default 1e-8). Missing on either side counts as zero on that side. The run
is persisted (`reconcilerun`) and retrievable by id — recon results are
audit data, not throwaway output.

Deliberate scope: this compares *quantities*, which is the check that catches
missed fills, double-counted fills and fee drag. Extending it to trade-level
recon (diffing venue trade history id-by-id against `trade`) is mechanical —
the raw payload of every fill is already stored.

## Precision

All money and quantity fields are `Decimal` end to end. SQLite has no
fixed-point type — NUMERIC affinity silently becomes IEEE-754 REAL — so
columns persist the exact decimal string via a `DecimalText` type and all
arithmetic happens in Python. Wire format is also decimal strings
(`"67250.55"`), the same convention exchange APIs use, so nothing crosses a
float boundary anywhere in the system. On Postgres these become native
`NUMERIC(38,18)` columns — the access code is unchanged.

## Indexing

- `UNIQUE(venue, venue_trade_id)` — dedup lookups are index-backed.
- `UNIQUE(venue, account, symbol)` on positions — one row per position,
  point lookups.
- `ix_trade_list (venue, symbol, executed_at)` — covers the common
  "trades for venue X on symbol Y over time" listing.
- Single-column indexes on `trade.venue`, `trade.symbol` for filters.

## Status codes & errors

| Code | When                                                            |
|------|-----------------------------------------------------------------|
| 200  | success, including batch results containing rejected items      |
| 401  | `X-API-Key` missing/wrong when `API_KEY` is configured          |
| 404  | unknown venue adapter, unknown reconcile run                    |
| 409  | `Idempotency-Key` reused with a different payload               |
| 422  | schema validation, unparseable symbol, malformed venue payload  |

Item-level failures (oversell etc.) are reported per trade inside the 200
batch response as `{status: "rejected", code: "OVERSELL", detail: ...}` so a
client can tell exactly which fills need attention.

## Scaling notes

- **Long-running reports**: PnL/recon over millions of trades shouldn't
  block a request. The endpoint shape already returns a stored `run_id`, so
  heavy variants become `202 Accepted` + background job + poll
  `GET /v1/reconcile/{id}` — the same pattern real recon jobs use, and no
  request ever sits open holding a worker.
- **Concurrency**: per-trade transactions keep contention short; on
  Postgres, position updates would take `SELECT ... FOR UPDATE` row locks
  (SQLite serializes writes anyway).
- **Storage**: SQLite is the zero-dependency default; `DATABASE_URL` points
  the same code at Postgres. Trades are append-only, so the hot path
  (insert + position update) stays cheap as the table grows.

## Future work

- Pull-based recon: fetch each venue's balance endpoint on a schedule
  instead of taking POSTed snapshots.
- Trade-level recon against venue fill history (raw payloads already stored).
- FIFO lot accounting as an alternative cost method.
- Cash/quote balances tracked alongside asset positions.
- Fees in third assets converted via a reference price feed.
- Async PnL snapshot jobs for large books.
