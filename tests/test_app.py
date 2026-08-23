import os

os.environ.update({"PORTFOLIO_API_KEY": "test-key", "COINBASE_API_KEY_ID": "test-key-id", "COINBASE_API_KEY_SECRET": "test-secret"})

from fastapi.testclient import TestClient

from app.main import app
from app.routes import market, portfolio
from app import portfolio_service


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
    assert operations == {"get_portfolio_summary", "get_holdings", "get_market_price", "get_portfolio_history", "get_market_history", "get_portfolio_analytics", "get_market_context", "get_portfolio_risk_breakdown"}
    scheme = schema["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}


def test_openapi_response_schemas_carry_field_descriptions() -> None:
    schema = TestClient(app).get("/openapi.json").json()
    schemas = schema["components"]["schemas"]
    assert schemas["Holding"]["properties"]["cost_basis"]["description"]
    assert schemas["HoldingsResponse"]["properties"]["cost_basis_available"]["description"]
    assert schemas["PortfolioSummaryResponse"]["properties"]["holdings_with_unknown_cost_basis"]["description"]
    assert schemas["MarketPriceResponse"]["properties"]["market_cap_rank"]["description"]

    paths = schema["paths"]
    for op, code in [("/api/portfolio/holdings", "503"), ("/api/portfolio/summary", "503"), ("/api/market/{product_id}", "400"), ("/api/market/{product_id}", "503")]:
        ref = paths[op]["get"]["responses"][code]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("ErrorDetail")


def test_checked_in_openapi_matches_runtime_contract() -> None:
    import json
    from pathlib import Path

    runtime = TestClient(app).get("/openapi.json").json()
    tracked = json.loads(Path("openapi.json").read_text())
    assert tracked == runtime


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
        async def list_fills(self):
            raise AssertionError("list_fills should not be called when include_cost_basis is omitted")
        async def aclose(self): pass

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/portfolio/summary", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_value"] == "22.36"
    assert body["crypto_value"] == "12.35"
    assert body["cash_value"] == "10.01"
    assert body["holding_count"] == 2
    assert body["top_holdings"][0]["allocation_pct"] == "55.22"
    assert body["cost_basis_available"] is None
    assert body["market_context_available"] is None
    assert body["holdings_with_unknown_cost_basis"] is None
    assert body["holdings_with_unknown_price"] == 0


def _fills_client(accounts, fills, product_prices):
    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def list_accounts(self):
            return accounts
        async def get_product(self, product_id):
            return {"price": product_prices[product_id]}
        async def list_fills(self):
            return fills
        async def aclose(self): pass
    return FakeCoinbaseClient


def _fill(product_id, side, size, price, trade_time, commission="0"):
    return {"product_id": product_id, "side": side, "size": size, "price": price, "trade_time": trade_time, "commission": commission}


def test_holdings_include_cost_basis_average_cost(monkeypatch) -> None:
    accounts = [{"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}}]
    fills = [
        _fill("BTC-USD", "BUY", "0.5", "20000", "2024-01-01T00:00:00Z"),
        _fill("BTC-USD", "BUY", "0.5", "25000", "2024-02-01T00:00:00Z"),
    ]
    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, fills, {"BTC-USD": "30000"}))
    response = TestClient(app).get("/api/portfolio/holdings?include_cost_basis=true", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["cost_basis_available"] is True
    holding = body["holdings"][0]
    assert holding["cost_basis"] == "22500.00"
    assert holding["unrealized_pnl"] == "7500.00"
    assert holding["unrealized_pnl_pct"] == "33.33"


def test_sell_does_not_change_average_cost(monkeypatch) -> None:
    accounts = [{"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "0.7"}}]
    fills = [
        _fill("BTC-USD", "BUY", "1.0", "100", "2024-01-01T00:00:00Z"),
        _fill("BTC-USD", "SELL", "0.4", "150", "2024-01-02T00:00:00Z"),
        _fill("BTC-USD", "BUY", "0.1", "100", "2024-01-03T00:00:00Z"),
    ]
    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, fills, {"BTC-USD": "100"}))
    response = TestClient(app).get("/api/portfolio/holdings?include_cost_basis=true", headers={"X-API-Key": "test-key"})
    holding = response.json()["holdings"][0]
    assert holding["cost_basis"] == "70.00"


def test_holding_with_no_matching_fills_has_null_cost_basis(monkeypatch) -> None:
    accounts = [{"currency": "DOGE", "name": "Dogecoin", "available_balance": {"value": "5"}}]
    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, [], {"DOGE-USD": "0.1"}))
    response = TestClient(app).get("/api/portfolio/holdings?include_cost_basis=true", headers={"X-API-Key": "test-key"})
    holding = response.json()["holdings"][0]
    assert holding["cost_basis"] is None
    assert holding["unrealized_pnl"] is None
    assert holding["unrealized_pnl_pct"] is None


def test_list_fills_failure_degrades_gracefully(monkeypatch) -> None:
    from app.coinbase.client import CoinbaseError

    class FailingFillsClient:
        def __init__(self, *_): pass
        async def list_accounts(self):
            return [{"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}}]
        async def get_product(self, product_id):
            return {"price": "100"}
        async def list_fills(self):
            raise CoinbaseError(500)
        async def aclose(self): pass

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", FailingFillsClient)
    response = TestClient(app).get("/api/portfolio/holdings?include_cost_basis=true", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["cost_basis_available"] is False
    assert body["holdings"][0]["cost_basis"] is None


def test_summary_cost_basis_rollup_with_mixed_known_and_unknown(monkeypatch) -> None:
    accounts = [
        {"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}},
        {"currency": "DOGE", "name": "Dogecoin", "available_balance": {"value": "5"}},
    ]
    fills = [_fill("BTC-USD", "BUY", "1.0", "100", "2024-01-01T00:00:00Z")]
    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, fills, {"BTC-USD": "150", "DOGE-USD": "0.1"}))
    response = TestClient(app).get("/api/portfolio/summary?include_cost_basis=true", headers={"X-API-Key": "test-key"})
    body = response.json()
    assert body["cost_basis_value"] == "100.00"
    assert body["unrealized_pnl"] == "50.00"
    assert body["holdings_with_unknown_cost_basis"] == 1


def test_market_price_includes_change_and_spread(monkeypatch) -> None:
    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def get_product(self, product_id):
            return {"price": "65000"}
        async def get_candles(self, product_id, *, granularity, start, end):
            return [{"close": str(65000 - i * 100)} for i in range(8)]
        async def get_best_bid_ask(self, product_ids):
            return [{"bids": [{"price": "64990"}], "asks": [{"price": "65010"}]}]
        async def aclose(self): pass

    monkeypatch.setattr(market, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/market/BTC-USD", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["bid"] == "64990.00"
    assert body["ask"] == "65010.00"
    assert body["change_24h_pct"] is not None
    assert body["change_7d_pct"] is not None
    assert body["spread_pct"] is not None


def test_market_price_survives_candle_failure(monkeypatch) -> None:
    from app.coinbase.client import CoinbaseError

    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def get_product(self, product_id):
            return {"price": "65000"}
        async def get_candles(self, product_id, *, granularity, start, end):
            raise CoinbaseError(500)
        async def get_best_bid_ask(self, product_ids):
            return [{"bids": [{"price": "64990"}], "asks": [{"price": "65010"}]}]
        async def aclose(self): pass

    monkeypatch.setattr(market, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/market/BTC-USD", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == "65000.00"
    assert body["change_24h_pct"] is None
    assert body["change_7d_pct"] is None


def test_holding_with_no_usd_product_does_not_fail_whole_request(monkeypatch) -> None:
    from app.coinbase.client import CoinbaseError

    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def list_accounts(self):
            return [
                {"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}},
                {"currency": "MOBILE", "name": "Helium Mobile", "available_balance": {"value": "100"}},
            ]
        async def get_product(self, product_id):
            if product_id == "MOBILE-USD":
                raise CoinbaseError(404)
            return {"price": "50000"}
        async def aclose(self): pass

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/portfolio/summary", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_value"] == "50000.00"
    assert body["holdings_with_unknown_price"] == 1
    mobile = next(h for h in body["top_holdings"] if h["symbol"] == "MOBILE")
    assert mobile["price"] is None
    assert mobile["value"] is None
    assert mobile["allocation_pct"] is None


def test_stablecoin_holding_priced_at_one_without_upstream_call(monkeypatch) -> None:
    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def list_accounts(self):
            return [{"currency": "USDC", "name": "USD Coin", "available_balance": {"value": "10"}}]
        async def get_product(self, product_id):
            raise AssertionError("stablecoins should not require a product lookup")
        async def aclose(self): pass

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/portfolio/summary", headers={"X-API-Key": "test-key"})
    body = response.json()
    assert body["total_value"] == "10.00"
    assert body["cash_value"] == "10.00"
    assert body["crypto_value"] == "0.00"


def test_holdings_include_market_context(monkeypatch) -> None:
    accounts = [{"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}}]

    class FakeCoinGeckoClient:
        def __init__(self, *_, **__): pass
        async def get_markets(self, symbols):
            assert symbols == ["BTC"]
            return {"BTC": {"market_cap_rank": 1, "market_cap": 1500000000000, "ath": 126080, "ath_change_percentage": -38.9}}
        async def aclose(self): pass

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, [], {"BTC-USD": "70000"}))
    monkeypatch.setattr(portfolio_service, "CoinGeckoClient", FakeCoinGeckoClient)
    response = TestClient(app).get("/api/portfolio/holdings?include_market_context=true", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["market_context_available"] is True
    holding = body["holdings"][0]
    assert holding["market_cap_rank"] == 1
    assert holding["market_cap"] == "1500000000000.00"


def test_market_context_omitted_by_default(monkeypatch) -> None:
    accounts = [{"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}}]

    class FailingCoinGeckoClient:
        def __init__(self, *_, **__): pass
        async def get_markets(self, symbols):
            raise AssertionError("get_markets should not be called when include_market_context is omitted")

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, [], {"BTC-USD": "70000"}))
    monkeypatch.setattr(portfolio_service, "CoinGeckoClient", FailingCoinGeckoClient)
    response = TestClient(app).get("/api/portfolio/holdings", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["holdings"][0]["market_cap_rank"] is None


def test_market_context_failure_degrades_gracefully(monkeypatch) -> None:
    from app.coingecko.client import CoinGeckoError

    accounts = [{"currency": "BTC", "name": "Bitcoin", "available_balance": {"value": "1.0"}}]

    class FailingCoinGeckoClient:
        def __init__(self, *_, **__): pass
        async def get_markets(self, symbols):
            raise CoinGeckoError(429)
        async def aclose(self): pass

    monkeypatch.setattr(portfolio_service, "CoinbaseClient", _fills_client(accounts, [], {"BTC-USD": "70000"}))
    monkeypatch.setattr(portfolio_service, "CoinGeckoClient", FailingCoinGeckoClient)
    response = TestClient(app).get("/api/portfolio/holdings?include_market_context=true", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["market_context_available"] is False
    assert body["holdings"][0]["market_cap_rank"] is None


def test_market_price_includes_coingecko_context(monkeypatch) -> None:
    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def get_product(self, product_id):
            return {"price": "65000"}
        async def get_candles(self, product_id, *, granularity, start, end):
            return [{"close": str(65000 - i * 100)} for i in range(8)]
        async def get_best_bid_ask(self, product_ids):
            return [{"bids": [{"price": "64990"}], "asks": [{"price": "65010"}]}]
        async def aclose(self): pass

    class FakeCoinGeckoClient:
        def __init__(self, *_, **__): pass
        async def get_markets(self, symbols):
            assert symbols == ["BTC"]
            return {"BTC": {"market_cap_rank": 1, "market_cap": 1500000000000, "ath": 126080, "ath_change_percentage": -48.4}}
        async def aclose(self): pass

    monkeypatch.setattr(market, "CoinbaseClient", FakeCoinbaseClient)
    monkeypatch.setattr(market, "CoinGeckoClient", FakeCoinGeckoClient)
    response = TestClient(app).get("/api/market/BTC-USD", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["market_cap_rank"] == 1
    assert body["ath"] == "126080.00"
    assert body["ath_change_pct"] == "-48.40"


def test_market_price_survives_coingecko_failure(monkeypatch) -> None:
    from app.coingecko.client import CoinGeckoError

    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def get_product(self, product_id):
            return {"price": "65000"}
        async def get_candles(self, product_id, *, granularity, start, end):
            return [{"close": "65000"}] * 8
        async def get_best_bid_ask(self, product_ids):
            return []
        async def aclose(self): pass

    class FailingCoinGeckoClient:
        def __init__(self, *_, **__): pass
        async def get_markets(self, symbols):
            raise CoinGeckoError(429)
        async def aclose(self): pass

    monkeypatch.setattr(market, "CoinbaseClient", FakeCoinbaseClient)
    monkeypatch.setattr(market, "CoinGeckoClient", FailingCoinGeckoClient)
    response = TestClient(app).get("/api/market/BTC-USD", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == "65000.00"
    assert body["market_cap_rank"] is None


def test_market_price_handles_empty_pricebook(monkeypatch) -> None:
    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def get_product(self, product_id):
            return {"price": "65000"}
        async def get_candles(self, product_id, *, granularity, start, end):
            return [{"close": "65000"}] * 8
        async def get_best_bid_ask(self, product_ids):
            return []
        async def aclose(self): pass

    monkeypatch.setattr(market, "CoinbaseClient", FakeCoinbaseClient)
    response = TestClient(app).get("/api/market/BTC-USD", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["bid"] is None
    assert body["ask"] is None
    assert body["spread_pct"] is None
