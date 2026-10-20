# AI usage

I used an AI coding agent (Devin, by Cognition) to build this project, the
same way I use AI tools for other work: the agent does the typing, I own the
decisions and the verification.

## How the work was split

**Me (human):**
- The plan. Before any code I described the approach to the interviewer:
  venue APIs → normalize to one format → keep external and internal books on
  the same page via reconciliation → then positions and PnL. That ordering is
  exactly what the architecture implements.
- Domain decisions: weighted-average-cost accounting, per-(venue, account,
  symbol) positions, oversell-rejects-by-default, two-layer idempotency
  (request key + natural trade-id dedup), Decimal-only money handling,
  venue adapters for Binance / Coinbase / CoinDCX.
- Verification (below).

**The agent:**
- Repo scaffolding, FastAPI/SQLModel wiring, venue adapters, the position
  ledger, API routes, tests, demo script, README and DESIGN.md.
- During the build it caught a real bug — a sell into an empty position was
  being treated as a short open before the oversell check ran — added a
  regression test for it, and fixed the check ordering.

## How I verify AI-written code

Same process I described in the interview, applied concretely here:

1. **Run it.** `pytest` (43 tests) plus `python scripts/demo.py` — a full
   ingest → positions → PnL → reconcile walkthrough that either works or
   doesn't.
2. **Check the math by hand.** The PnL tests encode worked examples I can
   verify mentally: buy 1 @100 + 1 @200 → avg 150; sell 1 @300 → realized
   150; a 0.1-BTC fee at price 100 → +10 cost basis.
3. **Check behavior, not just code.** Reading generated code line-by-line is
   less reliable than exercising it — so the suite tests observable outcomes
   (replay returns identical response and the position moved once; oversell
   leaves quantity untouched; recon diff equals internal − external).
4. **Review the parts AI is bad at.** Precise decimal handling, dedup edge
   cases (same id, different venue), and the accounting branch for shorts got
   the closest read, since those are the places plausible-looking code goes
   subtly wrong.

## What I'd tell a reviewer

The domain model and invariants are mine; the boilerplate is the agent's.
The test suite and demo are the proof of work — anything in this repo I'm
happy to defend line by line.
