# Setup guide

From zero to a verified, running service. Takes about two minutes.

## 1. Prerequisites

- **Python 3.11 or newer** — check with `python3 --version`.
  On macOS: `brew install python@3.13`. Everything else is installed by pip.
- **git** (to clone the repo).

## 2. Get the code

```bash
git clone https://github.com/SatyamDev803/trade-pnl-service.git
cd trade-pnl-service
```

## 3. Create a virtual environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This installs the package in editable mode plus test dependencies:
`fastapi`, `uvicorn`, `sqlmodel` (runtime) and `pytest`, `httpx` (dev).

## 4. Verify the install

Three checks, all should pass:

```bash
# a) the full suite — 47 tests, ~1 second
pytest

# b) the narrated end-to-end demo (in-process, no server needed)
python scripts/demo.py

# c) the app builds and exposes all routes
python -c "from tradepnl.main import app; print(sorted(app.openapi()['paths']))"
```

Expected route list: `/health`, `/v1/marks`, `/v1/pnl`, `/v1/positions`,
`/v1/reconcile`, `/v1/reconcile/{run_id}`, `/v1/trades`,
`/v1/venues/{venue}/trades`.

## 5. Run the service

```bash
uvicorn tradepnl.main:app --reload
```

- API: `http://127.0.0.1:8000`
- Interactive docs (try every endpoint in the browser): `http://127.0.0.1:8000/docs`
- Health check: `curl localhost:8000/health`

## 6. Configure (optional)

All settings come from environment variables — set them before starting:

```bash
export DATABASE_URL="sqlite:///./tradepnl.db"   # or a Postgres URL
export API_KEY="some-long-secret"               # require X-API-Key on /v1/*
export ALLOW_SHORT=false                        # permit negative positions
export RECON_TOLERANCE="0.00000001"             # recon diff tolerance
```

| Env var | Default | Effect |
|---------|---------|--------|
| `DATABASE_URL` | `sqlite:///./tradepnl.db` | Any SQLAlchemy URL; Postgres for production |
| `API_KEY` | unset | When set, every `/v1/*` route needs `X-API-Key`; `/health` stays open |
| `ALLOW_SHORT` | `false` | `true` allows short positions (signed-quantity accounting) |
| `RECON_TOLERANCE` | `0.00000001` | Default recon break threshold |

## 7. Load the sample dataset into the running server

```bash
python scripts/seed.py --post http://localhost:8000
```

This ingests the committed `data/sample_trades.json`: 26 fills across
Binance, Coinbase, CoinDCX and a canonical venue — including a duplicate,
an oversell (rejected) and a planted recon break. Then check
`GET /v1/positions` and `GET /v1/pnl`.

To regenerate or anchor prices to real market data:

```bash
pip install -e ".[data]"              # adds yfinance
python scripts/seed.py --yfinance     # real Yahoo hourly closes
python scripts/seed.py                # plain seeded random walk
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `python3 --version` shows < 3.11 | Use a newer interpreter explicitly: `/opt/homebrew/bin/python3.13 -m venv .venv` |
| `pip install -e .` fails on old pip | `pip install --upgrade pip` first |
| `ModuleNotFoundError: tradepnl` | Run from the repo root, and make sure you installed with `-e` in the active venv (`which python` should point at `.venv`) |
| `Address already in use` on uvicorn | Another process holds :8000 — `uvicorn tradepnl.main:app --port 8001` |
| Stale state between runs | Delete the SQLite file: `rm tradepnl.db` |
| `yfinance` import error on `--yfinance` | It's an optional extra: `pip install -e ".[data]"` |
| 401s on `/v1/*` | `API_KEY` is set — send `-H 'X-API-Key: <key>'` or unset the env var |

## Teardown

```bash
deactivate
rm -rf .venv tradepnl.db      # environment + local database
```
