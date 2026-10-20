# Explain it like I'm 5

## The story

Imagine you sell fruit. Not at one shop — at **three different markets** in
three different towns: Binance Bazaar, Coinbase Chowk, and CoinDCX Market.

At each market you buy and sell mangoes, oranges and bananas all day.
Every time you buy or sell, the market gives you a **receipt**: "you bought
0.5 mangoes for ₹67,000" (mangoes are very expensive in this story, just go
with it).

The problem: **each market writes receipts differently.**

- Binance Bazaar writes `BTCUSDT` and the time as `1729353600000` (a giant
  number only computers like).
- Coinbase Chowk writes `BTC-USD` and the time as words.
- CoinDCX Market writes `B-BTC_USDT` and puts the fee somewhere else.

So you hire a **super-organized friend** — that's this program — whose whole
job is to run your notebook.

## What the friend does

### 1. The translator (venue adapters)

Your friend teaches themselves all three receipt styles. Whatever a receipt
looks like, they copy the important bits into the notebook in **one fixed
format**: which market, what fruit, buy or sell, how many, at what price,
what fee, when.

*In the code: `tradepnl/venues/` — one little translator file per market,
all producing the same `CanonicalTrade` shape.*

### 2. The big notebook (trades table)

Every receipt gets its own line in a notebook that is **never erased**.
If the market gives the receipt a number (like receipt #9001), your friend
writes that number down too.

*In the code: the `trade` table in `models.py`.*

### 3. The "wait, did I already write this?" check (dedup + idempotency)

Sometimes your phone lags and you send the same receipt twice. Sometimes
you're not sure it went through, so you send it again. A careless helper
would write it down twice — and then your notebook says you own **two**
mangoes when you only bought one.

Your friend does two smart things:

- Before writing, they check: *"did I already record receipt #9001 from
  Binance Bazaar?"* If yes, they don't write it again — they just say
  "already got it." (**dedup**)
- When you send a batch, you can stick a **sticky note** on it saying
  "this is batch K-17." If you send the same batch again with the same
  sticky note, your friend doesn't redo the work — they hand you the
  exact same answer they gave last time. And if you try to reuse sticky
  note "K-17" on a *different* batch, they call you out on it.
  (**Idempotency-Key** — the fancy word for "doing it twice = doing it
  once.")

*In the code: `UNIQUE(venue, venue_trade_id)` on the `trade` table, and
`_idempotent()` + `IdempotencyRecord` in `api.py`.*

### 4. The running count (positions)

Next to the big notebook, your friend keeps a small card for each fruit at
each market: **"how many do we have right now, and what did they cost us
on average?"**

Bought 1 mango at ₹100 and another at ₹200? The card says: *2 mangoes,
average ₹150.*

*In the code: the `position` table + `apply_trade()` in `ledger.py`.*

### 5. The "you can't sell what you don't have" rule (oversell check)

You tell your friend: "write down that I sold 5 mangoes." They look at the
card: it says 3. So they refuse — **"you only have 3, you can't sell 5"** —
and write nothing. This catches typos, missing buys, and real mistakes
before they poison the notebook.

(Some shops DO let you sell mangoes you don't have, promising to get them
later — that's called *shorting*. The friend can allow it if you flip the
`ALLOW_SHORT` switch, and then the card just shows a minus number.)

*In the code: `OversellError` in `errors.py`, checked inside
`apply_trade()` — so EVERY way of writing a receipt goes through the rule.*

### 6. "How much money did we make?" (PnL)

Your friend tracks two kinds of profit:

- **Realized** — money you *actually* made. Bought a mango at ₹150, sold it
  at ₹300? That's ₹150 in your pocket, for real. Written in ink.
- **Unrealized** — money you *would* make if you sold right now. You still
  hold a mango that cost ₹150, and today mangoes cost ₹180? You have ₹30
  of "paper profit" — pencil, not ink, because tomorrow's price might
  change.

For the pencil number your friend needs today's price — you can pin it on
the board (`POST /v1/marks`). If nobody pins today's price, the friend just
uses the last price someone paid.

*In the code: `realized_pnl` on the `position` row (ink), `pnl_report()`
in `api.py` combining `avg_cost` × `mark_price` (pencil).*

### 7. Checking the shop's homework (reconciliation)

At the end of the day, Binance Bazaar tells you: *"we think you have
0.35 mangoes."* Your notebook says 0.40. Somebody is wrong — maybe a
receipt got lost, maybe the market missed a fee.

So your friend lines up both lists, fruit by fruit, and says: *"oranges:
same. mangoes: off by 0.05 — that's a **break**, go figure out why."*
And they file the comparison away so you can look at it later.

*In the code: `POST /v1/reconcile` → `reconcile()` → `ReconcileRun`,
saved in the `reconcilerun` table.*

### 8. Counting exactly, never approximately (Decimal)

Computers are secretly bad at decimals. Ask a normal computer to remember
`0.00000001` and it might quietly store `0.000000009999999…`. For fruit
money that's unacceptable, so the friend writes every number down as an
**exact string of digits** — `"67250.55"` — and only ever does math with a
precise decimal calculator, never the sloppy floating-point one.

*In the code: `DecimalText` / `DecStr` in `decimals.py`.*

### 9. The counter window (the API)

You never rummage through the notebooks yourself. You go to a little window
and ask: "record these receipts" (`POST /v1/trades`), "what do we hold?"
(`GET /v1/positions`), "how much did we make?" (`GET /v1/pnl`), "does our
notebook match the market's?" (`POST /v1/reconcile`).

If you set a secret password (`API_KEY`), the friend won't answer anyone at
the window who doesn't know it (`401`). If you ask about a market that
doesn't exist, they say "never heard of it" (`404`).

*In the code: `api.py` — every window is one route.*

### 10. Practice runs (tests + demo)

Before trusting the friend with real money, you run rehearsals:
"if I buy 1 @100 and 1 @200, the card should say 2 @150" — 47 little checks
like that, all run in under a second. There's also a scripted "day at work"
(`scripts/demo.py`) that plays the whole day end to end so anyone can watch
the friend do their job.

## The whole thing in one picture

```
  receipts from 3 markets ──► [translators] ──► "already have this one?"
       (all different)      each market has      yes → "duplicate"
        Binance Bazaar        its own                 no
        Coinbase Chowk        translator              ▼
        CoinDCX Market                          "do we have enough
                                                fruit to sell?"
                                                 no  → "rejected (OVERSELL)"
                                                 yes
                                                  ▼
                                        write in the big notebook
                                        update the fruit card
                                                  │
                    ┌─────────────────────────────┼──────────────────────┐
                    ▼                             ▼                      ▼
             "what do we hold?"            "how much did we        "does our notebook
              → fruit cards                 make? → ink + pencil    match the market's?"
              (positions)                   (PnL)                   → breaks (reconcile)
```

## One day at work (the demo, as a story)

1. Binance receipt arrives: bought 0.5 mango at ₹67,000. Friend writes it.
   Card: *0.5 @ ₹67,000*.
2. Coinbase receipt: bought 4 oranges at ₹3,400. New card for oranges.
3. You sell 0.2 mango at ₹69,000 → realized profit: **₹399 in ink**.
   Card: *0.3 mangoes*.
4. Your phone sends the same 0.1-mango buy twice with sticky note K-1 →
   friend answers "done" twice but only writes it once. Card: *0.4 mangoes*.
5. You try to sell 99 mangoes → **rejected**, you only have 0.4.
6. End of day, Binance says "you have 0.35." Notebook says 0.4 →
   **break: off by 0.05**, flagged for tomorrow's investigation.

## Cheat sheet — story words → real words → code

| Story word | Real word | Where in the code |
|------------|-----------|-------------------|
| market | venue / exchange | `venues/` adapters |
| receipt | trade fill | `Trade` model |
| translator | venue adapter | `venues/*.py` |
| big notebook | trades table | `models.py: Trade` |
| fruit card | position | `models.py: Position` |
| "already have this?" | dedup / idempotency | `ingest.py`, `_idempotent()` |
| sticky note | Idempotency-Key header | `api.py` |
| average what we paid | avg cost basis (AVCO) | `ledger.py` |
| ink profit | realized PnL | `realized_pnl` |
| pencil profit | unrealized PnL | `pnl_report()` |
| today's price tag | mark price | `MarkPrice` |
| "can't sell 5, have 3" | oversell check | `OversellError` |
| homework check | reconciliation | `reconcile()` |
| mismatch found | break | `ReconcileRun.results` |
| exact numbers | Decimal-as-string | `decimals.py` |
| the counter window | REST API | `api.py` |
| secret password | API key | `require_api_key` |
| rehearsals | test suite | `tests/` (47 tests) |

## Why this matters for the interview

If you can tell the fruit-stall story in 60 seconds, you understand every
part of the system — then you just swap the story words for the real words:
*translator → adapter, fruit card → position, ink → realized, pencil →
unrealized, sticky note → idempotency key, homework check → reconciliation.*
