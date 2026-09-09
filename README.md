# Vendor Orchestration API

A compliance case-management backend that orchestrates checks across
multiple third-party vendors (identity, sanctions, document) with retry,
timeout, and fallback resilience — the core pattern behind platforms that
coordinate dozens of external compliance vendors per customer onboarding.

Built with FastAPI, SQLAlchemy (SQLite locally / PostgreSQL-ready),
structured JSON logging, health/readiness probes, a basic metrics
endpoint, a full pytest suite, and a GitHub Actions CI pipeline that lints
and tests on every push.

**Note on deployment:** this API is fully built, tested, and verified
working locally (9/9 tests passing, lint clean, full request flow
confirmed via FastAPI's interactive docs at `/docs`) but is not currently
hosted live. Clone and run with `uvicorn app.main:app --reload` to try it
yourself — see below for a walkthrough of a working session.

## What this demonstrates

- **REST API + case management** — endpoints to create a case, run
  orchestrated vendor checks, and retrieve results, backed by a real
  relational schema (Case → many VendorCheck records)
- **Vendor orchestration resilience** — per-vendor timeout, retry with
  backoff, and fallback-vendor chains, so one flaky vendor never fails
  the whole onboarding flow
- **Response normalization** — maps each vendor's own status vocabulary
  to a consistent internal result enum, the "vendor response mapping"
  problem every multi-vendor integration platform has to solve
- **Observability** — `/health` (liveness), `/ready` (readiness, checks
  DB connectivity), `/metrics` (request/failure counters), and
  structured JSON logs on every request and vendor call
- **CI/CD** — GitHub Actions runs lint (ruff) and the full pytest suite
  on every push, plus a Docker build step
- **Containerized** — Dockerfile with a healthcheck, ready to deploy

## Setup

```bash
git clone https://github.com/Dhayalramesh/vendor-orchestration-api.git
cd vendor-orchestration-api
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for interactive API documentation
(FastAPI's auto-generated Swagger UI) — use "Try it out" on any endpoint
to send real requests without writing any client code.

## Run tests

```bash
pip install ruff pytest
ruff check app tests
pytest -v
```

9 tests covering the happy path, error handling (404s), observability
endpoints, response normalization, and — most importantly — the
fallback-vendor logic under a forced failure.

## Run with Docker

```bash
docker build -t vendor-orchestration-api .
docker run -p 8000:8000 vendor-orchestration-api
```

## Example usage

```bash
curl -X POST http://localhost:8000/cases \
  -H "Content-Type: application/json" \
  -d '{"customer_name": "Acme Corp", "customer_country": "US"}'

curl -X POST http://localhost:8000/cases/1/run

curl http://localhost:8000/cases/1
curl http://localhost:8000/metrics
```

## Vendors: simulated, not real

Real compliance-vendor APIs (identity verification, sanctions screening)
require paid contracts, so vendor calls here are simulated locally with
randomized latency and a realistic failure/timeout rate — the
orchestration logic (retries, fallback chains, response normalization)
is the real, portfolio-relevant part. See `app/vendors.py` for
`call_real_vendor()`, showing the one-line change needed to swap in an
actual vendor HTTP call.

## Using PostgreSQL instead of SQLite

Set the `DATABASE_URL` environment variable before starting the app:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/orchestration"
```

The SQLAlchemy models are database-agnostic — no code changes needed.

## Project structure

```
vendor-orchestration-api/
├── app/
│   ├── main.py       # FastAPI routes, logging, observability endpoints
│   ├── db.py          # SQLAlchemy models (Case, VendorCheck)
│   └── vendors.py     # orchestration logic: retry/timeout/fallback
├── tests/
│   └── test_api.py    # pytest suite (9 tests)
├── .github/workflows/
│   └── ci.yml          # lint + test + docker build on every push
├── Dockerfile
└── requirements.txt
```
