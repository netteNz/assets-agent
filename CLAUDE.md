# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## What this is

A read-only FastAPI service exposing live Coinbase portfolio data and USD market data (Coinbase + CoinGecko), consumed as an OpenAPI tool by an Azure AI Foundry agent (`SYSTEM_PROMPT.md` defines that agent's behavior). There is no frontend and no write path — every endpoint is `GET` and every response is either live upstream data or explicitly-`null` unavailable data. Never invent a value Coinbase or CoinGecko did not return.

## Architecture

```
app/
  main.py               FastAPI app, exception handling, OpenAPI 3.0.3 conversion (see below)
  config.py             pydantic-settings; all env vars, one Settings() singleton via get_settings()
  auth.py                require_api_key dependency (X-API-Key header, checked against PORTFOLIO_API_KEY)
  schemas.py             all Pydantic response models (single source of field descriptions for the OpenAPI spec)

  coinbase/
    client.py             Coinbase Advanced Trade REST client (JWT-signed requests), structured logging
    auth.py                JWT signing for Coinbase requests
    cost_basis.py          average-cost-basis calculation from fills history
    normalize.py           account/quantity/symbol parsing, money() / number() string formatting

  coingecko/
    client.py               CoinGecko public API client; get_markets() (batched) + get_context() (single-asset,
                             explicit COINBASE_SYMBOL_TO_ID map only — never substitutes a same-ticker collision)

  portfolio_service.py   get_holdings_data() — the one live-holdings orchestration function, shared by
                          routes/portfolio.py, routes/history.py, and capture_snapshot.py. Not route-specific;
                          if you need holdings data anywhere new, import from here, don't reach into a route module.

  portfolio_analytics.py Pure Decimal functions, no I/O: concentration (HHI, top-N%), cost_basis_summary,
                          value_change, max_drawdown_pct, realized_volatility_pct, correlation_and_beta.
                          Always return None on insufficient/invalid input, never raise.

  market_analytics.py    normalize_candles() (Coinbase candle -> internal dict), market_metrics()
                          (wraps portfolio_analytics functions for a single candle series)

  history.py              SQLite persistence: PortfolioSnapshot dataclass + PortfolioHistoryRepository.
                           One row per UTC hour bucket (snapshot_bucket()), INSERT ... ON CONFLICT DO NOTHING —
                           idempotent, never synthesizes or backfills data.

  capture_snapshot.py     CLI entrypoint: `python -m app.capture_snapshot`. Writes one hourly snapshot.
                           NOT scheduled by anything in this repo — see "No scheduler yet" below.

  routes/
    portfolio.py            GET /api/portfolio/holdings, /api/portfolio/summary
    market.py                GET /api/market/{product_id}, /{product_id}/history, /{product_id}/context
    history.py                GET /api/portfolio/history, /api/portfolio/analytics
```

## Key conventions (don't break these)

- **`null` means unknown, never zero.** Every optional field's docstring/`Field(description=...)` says what null means. Preserve that discipline in any new field.
- **Graceful degradation via `*_available` flags** (`cost_basis_available`, `market_context_available`, `benchmark_available` inside `data_quality`) — most upstream failures degrade to `false` + null fields, not an exception. The one asymmetric exception: `get_portfolio_analytics` raises a hard `503` if the Coinbase benchmark candle fetch itself fails (as opposed to there simply being no snapshot data yet, which degrades gracefully). Know this before changing that endpoint.
- **Portfolio history never synthesizes samples.** `_bucketed()` in `routes/history.py` picks the latest real sample per bucket; it does not interpolate or backfill. Cost-basis is only counted when a fill-derived position's quantity exactly matches the current held quantity (partial-transfer safety — see `portfolio_service.py`).
- **All monetary math is `Decimal`.** Render to string only at the response boundary via `coinbase/normalize.py::money()`/`number()`.
- **Values, not returns.** `PortfolioHistoryResponse`/`PortfolioAnalyticsResponse` explicitly document that value changes are not cash-flow-adjusted investment returns (deposits/withdrawals aren't accounted for).

## Running locally

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Requires `.env` (see `.env.example`) with at minimum `PORTFOLIO_API_KEY`, `COINBASE_API_KEY_ID`, `COINBASE_API_KEY_SECRET`. `PORTFOLIO_API_KEY` authenticates Foundry → this app; it's unrelated to the Coinbase credentials (which authenticate this app → Coinbase).

```bash
python -m pytest -q          # 47 tests as of this writing, all pure/mocked — no live network calls
```

## Manual snapshot capture (no scheduler yet)

```bash
python -m app.capture_snapshot
```

Writes one row to SQLite (`PORTFOLIO_HISTORY_DB_PATH`, default `data/portfolio_history.db`) for the current UTC hour bucket. **Nothing in this repo runs this automatically** — `/api/portfolio/history` and `/api/portfolio/analytics` will report `available: false` / thin single-point series until this has been run manually a few times, or a cron/Task Scheduler entry is set up. This is a known, deliberate gap while testing — see the memory note from 2026-08-22/23 for context. `correlation`/`beta` in particular need 3+ aligned daily observations to populate.

## Exposing this to Azure AI Foundry (current setup: local + ngrok)

There is no deployment yet (`deploy/containerapp.bicep` exists but is unused) — the Foundry agent currently reaches this service through an ngrok tunnel to a laptop-run dev server.

1. **Start the FastAPI server** (`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`).
2. **Start ngrok**: `ngrok http 8000`. Free tier means the public URL rotates every restart — check the current one via the local ngrok admin API: `curl http://127.0.0.1:4040/api/tunnels`.
3. **Set `PUBLIC_BASE_URL`** in `.env` to that tunnel URL. This is baked into the generated OpenAPI spec's `servers` field (see `app/main.py::custom_openapi`) — without it, Foundry has no host to call.
4. **Restart uvicorn** (env vars and the OpenAPI schema are cached per-process — `--reload` does not pick up `.env` changes, and `app.openapi_schema` is cached after first computation).
5. **Regenerate and validate `openapi.json`** at the repo root:
   ```bash
   python -c "from app.main import app; import json; json.dump(app.openapi(), open('openapi.json','w'), indent=2)"
   python -m pytest -q -k checked_in_openapi   # asserts openapi.json == live schema exactly
   ```
6. **Paste `openapi.json` into the Foundry portal's OpenAPI tool spec field**, replacing the previous version.

### Why the OpenAPI spec is generated as 3.0.3, not FastAPI's native 3.1.0

FastAPI (Pydantic v2) generates OpenAPI 3.1 by default, which expresses nullable fields as JSON-Schema-2020-12-style `"anyOf": [{"type": "..."}, {"type": "null"}]`. The Foundry portal's OpenAPI tool import only accepts **3.0.x**, which has no `type: "null"` — it uses `"nullable": true` instead. Pasting a raw FastAPI 3.1 spec into the portal fails with a generic `Invalid OpenAPI specification format` error that gives no field-level detail (confirmed 2026-08-23: the raw spec is valid OAS by generic validators, so this is Foundry's own stricter 3.0-only check, not a spec-conformance bug).

`app/main.py::custom_openapi()` walks the generated schema and rewrites every `anyOf: [..., {type: null}]` into `nullable: true` (single remaining branch collapses onto the parent; multi-branch unions like `dict[str, bool | int | None]` keep a trimmed `anyOf` alongside `nullable: true`), then sets `"openapi": "3.0.3"`. This is the one non-obvious piece of code in this repo — if you touch `custom_openapi()`, re-run `python -m pytest -q -k checked_in_openapi` and re-validate with `openapi_spec_validator` before assuming it still works, since a 3-way union or a new schema shape can slip past the naive 2-branch case.

## Known gaps / not-yet-done (as of 2026-08-23)

- **No scheduler for `capture_snapshot.py`.** Deliberate for now — testing phase. Before relying on `/api/portfolio/history` or `/api/portfolio/analytics` for anything real, set up cron/Task Scheduler (or move to a real deployment with a scheduled job).
- **No real deployment.** `deploy/containerapp.bicep` exists but this all currently runs off a laptop + ngrok. ngrok URL rotation means `PUBLIC_BASE_URL` + `openapi.json`'s `servers` field + the Foundry portal paste all need to be redone after any ngrok restart.
- **`SYSTEM_PROMPT.md` has no guidance for a hard tool-call failure (5xx)**, only for `*_available: false` fields. `get_portfolio_analytics`'s benchmark-fetch 503 is the most likely case to hit this during testing. Flagged, not yet fixed — ask before adding, since the prompt is being tuned interactively against live Foundry Playground runs right now.
