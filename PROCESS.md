# Process

How this was built, in the order it was built, and what was weighed at each
step. DESIGN.md describes *what* the system is; this file describes *how it
came to be* and *why it looks this way*.

## Step 0 — Requirements, and the data problem

The task: a backend that ingests trades from multiple venues, tracks
positions, reports PnL — with idempotency, reconciliation and an oversell
check.

The first blocker was data. Company trade data wasn't shareable (correctly —
fill history is sensitive), and no public API gives you *account fills*:
exchanges expose market prices publicly but keep executions behind auth.
yfinance and friends hand you candles, not trades.

**Decision: generate the data.** `scripts/seed.py` fabricates venue-native
payloads (Binance `myTrades` shape, Coinbase fill shape, CoinDCX trade shape)
plus canonical trades, from a seeded RNG — fully reproducible. An optional
`--yfinance` mode snaps the fabricated fill prices to real historical closes
from Yahoo, so the dataset tracks actual market movement instead of noise.

*Trade-off considered:* hand-writing a static fixture was tempting but would
have hidden whether the generator understood each venue's field naming,
timestamp format (ms-epoch vs ISO-8601) and fee conventions. Generating
venue-native payloads forced the adapters to earn their keep — the same file
exercises dedup, oversell, derived ids and recon.

The dataset deliberately embeds the service's edge cases: a repeated fill
(dedup), a fee denominated in the base asset (fee conversion), a trade with
no id (derived id), an oversell, and a recon snapshot with a planted break.
`tests/test_sample_data.py` asserts all of it behaves as designed, so the
sample can't quietly rot.

## Step 1 — Domain model before framework

Before choosing a web framework I fixed the core objects:

- **Canonical trade** — one shape every venue maps into: venue, venue trade
  id, account, symbol (`BASE-QUOTE`), side, price, quantity, fee+fee asset,
  timestamp, raw payload.
- **Position scope** — per `(venue, account, symbol)`.

*Trade-off — position scope:* netting positions across venues is the
economically "true" book, but reconciliation happens *per venue* in
operational reality (you diff against one exchange's balances at a time).
Per-venue positions keep recon honest; cross-venue totals are an aggregation
a report can do. Chose per-venue.

*Trade-off — symbol canonicalization:* venue pairs arrive as `BTCUSDT`,
`BTC-USD`, `B-BTC_USDT`. Options: store verbatim (loses cross-venue
comparisons), maintain a per-venue market-metadata table (accurate, heavy),
or a quote-suffix + alias heuristic. Chose the heuristic — imperfect in
theory (a base asset ending in a quote suffix would break it), fine in
practice with a known-quotes list and aliases like `XBT→BTC`. Documented as
a limitation.

## Step 2 — Storage and precision

*Trade-off — number handling:*

| Option | Verdict |
|--------|---------|
| `float` | Rejected — binary floating point cannot represent `0.00000001`; PnL silently drifts |
| integer minor units | Rejected — every venue/asset has different precision; conversions everywhere |
| `Decimal` in Python, text on disk | **Chosen** — exact arithmetic; SQLite has no fixed-point type so the column stores the canonical string via a `DecimalText` type |

On Postgres the same columns become `NUMERIC(38,18)` with zero access-code
changes; the wire format is decimal strings (matching how exchange APIs
emit numbers), so no float ever crosses a boundary.

*Trade-off — SQLite default:* real deployments want Postgres (concurrency,
real NUMERIC). SQLite was chosen as the zero-setup default because a
reviewer should be able to run everything in 30 seconds; `DATABASE_URL`
swaps the engine.

## Step 3 — Ingestion design

Two ingestion paths on purpose:

- `POST /v1/venues/{venue}/trades` — venue-native payloads through an
  adapter registry (`adapt()` dispatches on venue name).
- `POST /v1/trades` — already-canonical batches for systems that normalize
  upstream, and for venues with no adapter yet.

*Trade-off — batch semantics:* all-or-nothing (one bad fill rejects the
batch) vs per-item results. Chose per-item with per-trade transactions:
`accepted | duplicate | rejected` per fill. A trade row and its position
update commit atomically, so the ledger can never show a position for a
fill that isn't persisted. A 5000-fill file with 3 bad lines shouldn't need
a full re-import.

## Step 4 — Idempotency (two layers, on purpose)

*Idempotent* = applying an operation twice gives the same result as once.

*Trade-off — where idempotency lives:*

1. **Request layer** — `Idempotency-Key` header. First response stored;
   replay returns it byte-for-byte with `Idempotency-Replayed: true`. The
   key binds to a SHA-256 of the request body, so reusing a key with a
   different payload is a 409, not a silent no-op.
2. **Entity layer** — `UNIQUE(venue, venue_trade_id)`. Retried file imports
   or keyless retries dedupe as `duplicate` rather than double-counting.

Only #1 protects a client retrying the *same HTTP request*; only #2 survives
a data pipeline re-delivering the same fill through a different request
entirely. Both exist in real trade capture systems, so both exist here.
The unique constraint is the authoritative backstop — an `IntegrityError`
on commit (lost race) maps to `duplicate`, never a 500.

## Step 5 — The ledger (PnL accounting)

*Trade-off — cost method:*

| Method | Notes |
|--------|-------|
| FIFO | Most precise per-lot; needs a `lots` table + partial consumption — more state, more edge cases |
| LIFO/HIFO | Tax strategies; same lot-tracking cost |
| **AVCO** (weighted average) | **Chosen** — O(1) state (one row per position), standard for crypto desks; the append-only trade table can recompute FIFO later if needed |

The ledger uses a **signed-quantity** model: positive = long with buy basis,
negative = short with sell basis. Realized PnL books on the portion of each
fill that *reduces* the open side:

- sell into a long: `(price − avg_cost) × closed − fee`
- buy into a short: `(avg_cost − price) × covered − fee`

Fees normalize to quote currency; base-asset fees convert at the fill's own
price; third-asset fees (e.g. BNB) are stored but excluded — a real system
would convert via a reference price feed.

*Trade-off — unrealized PnL needs a mark.* `POST /v1/marks` supplies them;
fallback is the position's last fill price; positions with neither surface
in `unpriced` rather than reporting a fake zero.

**A real bug found here:** the first draft routed a sell into an empty
position through the "open short" branch before the oversell check ran, so
`ALLOW_SHORT=false` was silently ignored for zero positions. `scripts/demo.py`
surfaced it on its first run — a regression test now pins the behavior:
selling with no position rejects `OVERSELL`.

## Step 6 — Oversell check

*Trade-off — reject vs record-and-flag:* recording an oversell and flagging
it preserves "what the venue says happened" (useful when your ledger is
wrong), but silently corrupts a system meant to be the source of truth.
**Chose reject-by-default** — fail closed, surface the data break
immediately, let recon explain the discrepancy. `ALLOW_SHORT=true` converts
to signed-quantity accounting for desks that genuinely run short.

The check lives in the *ledger*, not the API layer, so it holds for every
ingestion path — canonical, venue adapter, batch member.

## Step 7 — Reconciliation

*Trade-off — recon granularity:* quantity-level (diff balances), trade-level
(diff every fill by id), or cash-level. Chose **quantity-level first** —
highest signal per effort: missed fills, double-counts and fee drag all
surface as quantity breaks. Trade-level recon is a mechanical extension
(every raw payload is already stored) and is listed in future work.

Runs are persisted and retrievable (`run_id`) — recon output is audit data.
This also keeps the door open to the async pattern (`202` + background job +
poll) for heavy runs; the request shape doesn't change.

## Step 8 — API surface and auth

REST conventions used deliberately: `404` unknown venue/run, `401`
missing/invalid `X-API-Key` when `API_KEY` is configured (every `/v1` route,
since positions and PnL are sensitive; `/health` stays open for probes),
`409` idempotency-key conflict, `422` schema/payload errors. Item-level
failures ride inside the 200 batch response so clients can pinpoint fills.

*Trade-off — auth scope:* a shared-secret header is honest for a take-home;
OAuth/mTLS noted as production work rather than faked.

## Build order

1. Scaffold + deps → 2. models/storage (DecimalText) → 3. ledger math →
4. ingest service (dedup, per-item results) → 5. venue adapters →
6. API routes → 7. idempotency layer → 8. recon → 9. tests →
10. demo script → 11. seeder + dataset → 12. docs.

Ledger before API because the accounting is the product's core risk; tests
for the money math went in early rather than at the end.

## Testing strategy

Behavior over internals: tests exercise the HTTP API (via `TestClient`) and
assert observable outcomes — a replayed request returns the identical body
*and* the position moved exactly once; a rejected oversell leaves quantity
and trade count untouched; recon diff equals internal − external. PnL tests
use hand-checkable numbers (buy 1 @100 + 1 @200 → avg 150; sell 1 @300 →
realized 150) so a wrong formula can't hide behind plausible output.

47 tests, all on throwaway SQLite files, under a second to run.

## Known limitations (honest list)

- Symbol splitting is heuristic — production would use venue market metadata.
- Marks are symbol-global, not per-venue.
- Third-asset fees excluded from PnL (need a conversion feed).
- AVCO, not FIFO — recomputable from stored trades if required.
- Recon compares quantities only, not trade-by-trade.
- No auth beyond the optional API key; no rate limiting; no async job queue
  for heavy reports.
