# Coinbase Foundry portfolio API

A personal, read-only FastAPI adapter that exposes live Coinbase Advanced Trade balances, USD market data, and manually captured portfolio history to a Microsoft Foundry OpenAPI tool. It has no trading, transfer, scheduling, or user-management functionality.

## Security boundary

```text
Foundry OpenAPI tool -- X-API-Key --> Container App -- request-bound JWT --> Coinbase
```

`COINBASE_API_KEY_SECRET` and Coinbase JWTs remain inside the Container App. Create a Coinbase CDP key limited to the intended portfolio with `view` permission, ECDSA/ES256 signing, and an IP allowlist if the deployment egress can be made stable. Never put either secret in the Foundry tool spec, prompt, repository, or logs.

## Local run

Python 3.12+ is required. The local server needs valid-looking configuration even for `/health`; mocked tests do not make Coinbase calls.

```bash
cp .env.example .env
# Populate all three secrets in .env; preserve PEM newlines (or use literal \n).
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
curl http://127.0.0.1:8000/health
curl -H "X-API-Key: $PORTFOLIO_API_KEY" http://127.0.0.1:8000/openapi.json
pytest
```

Portfolio history is intentionally captured manually, never through an HTTP route or background scheduler:

```bash
python -m app.capture_snapshot
```

Snapshots are stored only in SQLite at `PORTFOLIO_HISTORY_DB_PATH` (default: `data/portfolio_history.db`). One capture is retained per UTC hour, so repeating the command within that hour is idempotent. History starts at the first successful capture; the API never backfills or invents points.

## Docker

```bash
docker build -t coinbase-portfolio-api .
docker run --rm -p 8000:8000 --env-file .env coinbase-portfolio-api
```

## Azure

Build and push the image to GHCR, make the package pullable by Azure Container Apps, then deploy [the minimal Bicep template](deploy/containerapp.bicep). It creates a Consumption environment with disabled app logs plus one Container App with HTTPS ingress, secret-backed environment variables, and `minReplicas: 0`, `maxReplicas: 1`.

See [deployment instructions](deploy/README.md). The included GitHub workflow expects Azure OIDC configuration (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) and repository variables `AZURE_RESOURCE_GROUP` and `CONTAINER_APP_NAME`. It only updates an already-created app, keeping initial secret provisioning out of CI logs.

## Foundry setup

1. Open `<app-url>/openapi.json` and confirm its seven operations: `get_holdings`, `get_portfolio_summary`, `get_market_price`, `get_portfolio_history`, `get_market_history`, `get_portfolio_analytics`, and `get_market_context`.
2. In the Foundry project, create a custom-key connection whose key is `X-API-Key` and value is the same random value stored as the Container App `portfolio-api-key` secret.
3. Attach the OpenAPI schema to the agent with project-connection authentication. The security scheme is already declared as an `apiKey` header.
4. Apply [SYSTEM_PROMPT.md](SYSTEM_PROMPT.md) as the agent instructions.
5. Ask for a portfolio value, holdings (optionally with cost basis or market-cap context), and a `BTC-USD` price with market context. The first request after idle may cold-start the app.

Foundry currently requires every exposed operation to have an `operationId`, and API-key injection depends on the schema security scheme matching the project connection header name. The health route is deliberately absent from the OpenAPI schema.

## Deliberate constraints

- Product IDs are restricted to uppercase `BASE-USD`, preventing path injection and non-USD valuation ambiguity.
- Public product reads avoid using Coinbase account credentials for market data; account reads remain authenticated.
- Values use `Decimal` and are serialized as strings, preventing binary floating-point drift.
- The implementation assumes spot account balances. Futures and margin are outside V2; cost basis and P/L are derived from fills history via an opt-in `include_cost_basis` flag and are shown only when fills fully explain the current position.
- Coinbase is the only market-history source. CoinGecko provides optional broader context only for explicitly mapped Coinbase symbols; an unmapped symbol returns unavailable/null context rather than a ticker guess.
- All historical portfolio change fields are changes in observed account value, not cash-flow-adjusted investment returns. Deposits and withdrawals are not modeled.

