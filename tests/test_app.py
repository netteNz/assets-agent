import os

os.environ.update({"PORTFOLIO_API_KEY": "test-key", "COINBASE_API_KEY_ID": "test-key-id", "COINBASE_API_KEY_SECRET": "test-secret"})

from fastapi.testclient import TestClient

from app.main import app
from app.routes import portfolio


def test_health_has_no_authentication_or_upstream_work() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_or_invalid_key_is_unauthorized() -> None:
    client = TestClient(app)
    assert client.get("/api/portfolio/summary").json() == {"error": "unauthorized"}
    assert client.get("/api/portfolio/summary", headers={"X-API-Key": "wrong"}).status_code == 401


def test_openapi_has_only_foundry_operations_and_api_key_security() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    operations = {operation["operationId"] for path in schema["paths"].values() for operation in path.values()}
    assert operations == {"get_portfolio_summary", "get_holdings", "get_market_price"}
    scheme = schema["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}


def test_invalid_product_is_rejected_before_upstream_call() -> None:
    response = TestClient(app).get("/api/market/BTC-USD/evil", headers={"X-API-Key": "test-key"})
    assert response.status_code == 404
    response = TestClient(app).get("/api/market/BTC-USDT", headers={"X-API-Key": "test-key"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_product"


def test_summary_filters_zero_balances_and_uses_decimal_values(monkeypatch) -> None:
    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def list_accounts(self):
            return [
                {"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "0.1"}},
                {"currency": "USD", "available_balance": {"value": "10.01"}},
                {"currency": "ETH", "available_balance": {"value": "0"}},
            ]
        async def get_product(self, product_id):
            assert product_id == "BTC-USD"
            return {"price": "123.45"}
        async def aclose(self): pass

    monkeypatch.setattr(portfolio, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/portfolio/summary", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_value"] == "22.36"
    assert body["crypto_value"] == "12.35"
    assert body["cash_value"] == "10.01"
    assert body["holding_count"] == 2
    assert body["top_holdings"][0]["allocation_pct"] == "55.22"
