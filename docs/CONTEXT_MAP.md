# Context Map - Coinbase Portfolio API

_Last updated: 2026-08-23_

## Project Overview

**Project:** Coinbase Portfolio & Market Data API
**Framework:** FastAPI (async Python)
**Purpose:** Read-only live Coinbase portfolio and USD market data, with persisted history and cross-asset analytics
**Current Status:** History persistence and combined analytics endpoints have landed; test coverage added for new modules; structured logging introduced across external clients

---

## Files to Modify (currently dirty in working tree)

| File | Purpose | Changes Needed |
|------|---------|-----------------|
| `app/routes/portfolio.py` | Portfolio holdings & summary endpoints | Now delegates concentration/P&L math to `portfolio_analytics`; stricter cost-basis validity check (`position["quantity"] != item["quantity"]`) |
| `app/routes/market.py` | Market price/history/context endpoints | Added `/history` and `/context` endpoints; `_market_context` now returns availability flag; uses `market_analytics` |
| `app/schemas.py` | Response models | +106 lines: `HistoryPoint`, `PortfolioHistoryResponse`, `MarketCandle`, `MarketHistoryResponse`, `MarketContextResponse`, `PortfolioAnalyticsResponse`, expanded `PortfolioSummaryResponse` |
| `app/config.py` | Settings | Added `portfolio_history_db_path`, `coingecko_market_cache_ttl_seconds`, `coingecko_metadata_cache_ttl_seconds` |
| `app/coingecko/client.py` | CoinGecko client | Added `get_context()` for single-asset V2 context, `COINBASE_SYMBOL_TO_ID` explicit mapping, separate market/metadata cache TTLs, structured logging |
| `app/coinbase/client.py` | Coinbase client | Added request-latency structured logging (`coinbase_request` / `coinbase_request_failed`) |
| `app/main.py` | FastAPI app | Registers new `history_router` under `"portfolio"` tag |
| `README.md`, `SYSTEM_PROMPT.md`, `.env.example`, `openapi.json` | Docs/generated schema | Updated to reflect new endpoints and settings |
| `tests/test_app.py` | Integration tests | +11 lines, updated for new response shape |

---

## New Files Added (untracked)

| File | Purpose | Key Responsibility |
|------|---------|---------------------|
| `app/routes/history.py` | **New router** — portfolio history & combined analytics | `GET /api/portfolio/history`, `GET /api/portfolio/analytics` |
| `app/capture_snapshot.py` | **New CLI entrypoint** — `python -m app.capture_snapshot` | Writes one hourly `PortfolioSnapshot` to SQLite; intended to be cron/task-scheduled |
| `app/history.py` | Portfolio history persistence (SQLite) | `PortfolioSnapshot` dataclass, `PortfolioHistoryRepository` |
| `app/portfolio_analytics.py` | Pure Decimal portfolio calculations | Concentration, cost-basis summary, correlation & beta, drawdown, volatility |
| `app/market_analytics.py` | Candle normalization & market risk metrics | `normalize_candles`, `market_metrics` |
| `tests/test_analytics.py` | Unit tests | Covers `portfolio_analytics.py` / `market_analytics.py` pure functions |
| `tests/test_history.py` | Unit/integration tests | Covers `PortfolioHistoryRepository` SQLite behavior |
| `CONTEXT_MAP.md` | This document | — |

---

## Dependencies (import graph)

```
app/main.py
 ├─ app.routes.portfolio.router
 ├─ app.routes.market.router
 └─ app.routes.history.router          [NEW]

app/routes/history.py                  [NEW FILE]
 ├─ app.history.{PortfolioHistoryRepository, PortfolioSnapshot}
 ├─ app.market_analytics.normalize_candles
 ├─ app.portfolio_analytics.{aligned_returns, concentration, correlation_and_beta,
 │                            high_low, max_drawdown_pct, realized_volatility_pct, value_change}
 ├─ app.routes.portfolio._holdings      (reuses portfolio's private holdings fetch)
 └─ app.schemas.{HistoryPoint, PortfolioAnalyticsResponse, PortfolioHistoryResponse}

app/capture_snapshot.py                [NEW FILE]
 ├─ app.history.{PortfolioHistoryRepository, PortfolioSnapshot, snapshot_bucket}
 └─ app.routes.portfolio._holdings      (same private helper reused here too)

app/routes/portfolio.py
 └─ app.portfolio_analytics.{concentration, cost_basis_summary}   [NEW IMPORT]

app/routes/market.py
 └─ app.market_analytics.{market_metrics, normalize_candles}      [NEW IMPORT]
```

**Notable coupling:** `app/routes/history.py` and `app/capture_snapshot.py` both import the private `_holdings()` helper from `app/routes/portfolio.py`. That function is doing double duty as a shared internal API — not exported from a neutral module. Worth flagging if `portfolio.py` internals ever need to change shape.

---

## Test Files

| Test | Coverage |
|------|----------|
| `tests/test_analytics.py` | **New.** `portfolio_analytics.py` (concentration, cost_basis_summary, correlation/beta, drawdown, volatility) and `market_analytics.py` (normalize_candles, market_metrics) |
| `tests/test_history.py` | **New.** `PortfolioHistoryRepository`: write/query/coverage, idempotent hourly bucketing |
| `tests/test_app.py` | Modified (+11/-?) — integration tests through FastAPI `TestClient`, likely extended for `/api/portfolio/history` and `/api/portfolio/analytics` |
| `tests/test_coinbase_client.py` | Existing — unaffected by this diff (not in `git diff --stat`) |
| `tests/test_coingecko_client.py` | Existing — should be checked against new `get_context()` / dual-TTL constructor signature; not shown as modified, so may need new cases |
| `tests/test_cost_basis.py` | Existing — unaffected |

**Gap:** No dedicated test file for `app/routes/history.py` or `app/capture_snapshot.py` endpoints/CLI themselves (only the underlying `history.py` repository and `portfolio_analytics.py`/`market_analytics.py` pure functions are unit-tested). Endpoint-level behavior (period/granularity validation, 503 on benchmark failure, `data_quality` block) is presumably covered in `tests/test_app.py`, but worth confirming.

---

## New/Changed API Surface

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/portfolio/holdings` | GET | Existing, logic refactored | Cost-basis validity now also requires `position.quantity == item.quantity` (prevents false cost basis across partial transfers) |
| `/api/portfolio/summary` | GET | Existing, response expanded | Adds `concentration`, `pnl_scope_value`, `cost_basis_coverage_pct`, `profitable_count`/`losing_count`/`flat_count`, `largest_known_gain`/`largest_known_loss` |
| `/api/market/{product_id}` | GET | Existing, response expanded | Adds `market_context_available` flag |
| `/api/market/{product_id}/history` | GET | **New** | Coinbase-only candles + `max_drawdown_pct`/`realized_volatility_pct`; period/granularity combos capped, max 150 candles |
| `/api/market/{product_id}/context` | GET | **New** | Single-asset CoinGecko V2 context (`coin_id`, supply, ATH, volume/mcap ratio, 24h/7d/30d/1y change) via explicit `COINBASE_SYMBOL_TO_ID` map |
| `/api/portfolio/history` | GET | **New** (`app/routes/history.py`) | Reads only manually captured SQLite snapshots; `period` × `granularity` allow-list; never synthesizes samples |
| `/api/portfolio/analytics` | GET | **New** (`app/routes/history.py`) | Combines live holdings + persisted snapshots + Coinbase benchmark candles → concentration, drawdown, volatility, correlation, beta, `data_quality` block |

---

## Reference Patterns

| File | Pattern |
|------|---------|
| `app/portfolio_analytics.py`, `app/market_analytics.py` | Pure Decimal functions, no I/O, return `None` instead of raising on insufficient/invalid data |
| `app/routes/market.py::_market_context`, `app/routes/history.py` | Graceful degradation: `*_available` boolean flags propagate upstream-failure state into the response instead of raising |
| `app/coinbase/client.py`, `app/coingecko/client.py` | Structured logging around every external call: `<service>_request` on success (with `latency_ms`) and `<service>_request_failed` on error (with `reason`/`status`) — **new convention as of this diff**, not yet applied inside `app/routes/history.py`'s own logic beyond the two `logger.info` summary lines |
| `app/history.py::_bucketed` (in history.py route) | "Use the latest persisted sample per bucket, never synthesize" — deliberate no-interpolation policy for financial history |
| `app/capture_snapshot.py` | CLI/cron entrypoint pattern (`asyncio.run` + `if __name__ == "__main__"`) reusing route-layer private helpers directly rather than going through HTTP |

---

## Risk Assessment

- [ ] **Breaking API changes:** All new response fields are additive/optional — backward compatible. `MarketPriceResponse.market_context_available` is non-optional but was already effectively always present.
- [ ] **Database migrations needed:** New SQLite table `portfolio_snapshots` auto-created on first `PortfolioHistoryRepository` instantiation (idempotent `CREATE TABLE IF NOT EXISTS`). No migration tooling — schema changes later would need manual handling.
- [ ] **Configuration changes required:** New settings (`portfolio_history_db_path`, two CoinGecko TTLs) all have defaults — no `.env` changes strictly required, though `.env.example` was updated for documentation.
- [ ] **Operational dependency introduced:** `/api/portfolio/history` and `/api/portfolio/analytics` are only useful if `app/capture_snapshot.py` is actually run on a schedule (cron/Task Scheduler) — this is external infra not enforced by the app itself. Confirm a scheduler is configured wherever this deploys.
- [ ] **Shared private helper:** `_holdings()` in `portfolio.py` is now imported by two other new modules (`history.py` route, `capture_snapshot.py`). Consider whether it should be promoted to a public/shared module if it changes signature again.
- [ ] **Untested endpoint layer:** New `/history` and `/analytics` route logic (period/granularity validation, 503 handling, benchmark alignment) doesn't appear to have dedicated endpoint tests yet beyond whatever `tests/test_app.py`'s +11 lines cover — verify before shipping.
- [ ] **CoinGecko rate limits:** `get_context()` adds a second CoinGecko call pattern (single-coin detail endpoint) on top of the existing batched `/coins/markets` — worth confirming free-tier rate limits aren't at risk under combined load from `/summary`, `/market/*`, and `/context` all in flight.

---

## Configuration Notes (current)

| Setting | Default | Used by |
|---------|---------|---------|
| `portfolio_history_db_path` | `data/portfolio_history.db` | `app/history.py`, `app/routes/history.py`, `app/capture_snapshot.py` |
| `coingecko_market_cache_ttl_seconds` | `55.0` | `CoinGeckoClient.get_markets` |
| `coingecko_metadata_cache_ttl_seconds` | `3600.0` | `CoinGeckoClient.get_context` |
| `coinbase_timeout_seconds` | `10.0` | Both Coinbase and CoinGecko client construction (reused as shared timeout) |

---

## Next Steps Before Implementation Review

1. Confirm `tests/test_app.py` actually exercises `/api/portfolio/history` and `/api/portfolio/analytics` end-to-end (success + 503 + 422 paths).
2. Decide whether `_holdings()` should move out of `app/routes/portfolio.py` into a shared module now that two more callers depend on it.
3. Verify `app/capture_snapshot.py` has a documented scheduling mechanism (cron entry, Windows Task Scheduler, etc.) in deployment docs.
4. Check `tests/test_coingecko_client.py` covers the new `get_context()` method and dual-TTL constructor — add cases if missing.
5. Review `openapi.json` diff (+1459/-?) was regenerated from current schemas, not hand-edited.
