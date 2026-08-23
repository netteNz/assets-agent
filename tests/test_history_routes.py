from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os

os.environ.update({"PORTFOLIO_API_KEY": "test-key", "COINBASE_API_KEY_ID": "test-key-id", "COINBASE_API_KEY_SECRET": "test-secret"})

from fastapi.testclient import TestClient

from app.coinbase.client import CoinbaseError
from app.history import PortfolioSnapshot, snapshot_bucket
from app.main import app
from app.routes import history

HEADERS = {"X-API-Key": "test-key"}


class FakeRepository:
    def __init__(self, points: list[PortfolioSnapshot]):
        self.points = points

    def query(self, *_args, **_kwargs) -> list[PortfolioSnapshot]:
        return self.points


def _snapshot(days_ago: int, total: str) -> PortfolioSnapshot:
    timestamp = datetime.now(UTC) - timedelta(days=days_ago)
    return PortfolioSnapshot(timestamp, snapshot_bucket(timestamp), Decimal(total), Decimal(total), Decimal("0"), Decimal(total), 0)


def test_portfolio_history_empty_db_returns_explicit_unavailability(monkeypatch) -> None:
    monkeypatch.setattr(history, "get_history_repository", lambda: FakeRepository([]))

    response = TestClient(app).get("/api/portfolio/history?period=all&granularity=day", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["points"] == []
    assert body["coverage_start"] is None
    assert body["coverage_end"] is None
    assert body["change_value"] is None
    assert body["change_pct"] is None
    assert body["high_value"] is None
    assert body["low_value"] is None


def test_portfolio_history_accepts_supported_period_granularity_combinations(monkeypatch) -> None:
    monkeypatch.setattr(history, "get_history_repository", lambda: FakeRepository([_snapshot(0, "120"), _snapshot(1, "100")]))
    client = TestClient(app)

    for query in ("period=1d&granularity=hour", "period=30d&granularity=day", "period=all&granularity=day"):
        response = client.get(f"/api/portfolio/history?{query}", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["available"] is True


def test_portfolio_history_rejects_unsupported_period_granularity(monkeypatch) -> None:
    monkeypatch.setattr(history, "get_history_repository", lambda: FakeRepository([]))

    response = TestClient(app).get("/api/portfolio/history?period=1d&granularity=day", headers=HEADERS)

    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_period_granularity"


def test_portfolio_analytics_wires_data_quality_and_benchmark(monkeypatch) -> None:
    points = [_snapshot(2, "100"), _snapshot(1, "110"), _snapshot(0, "121")]

    async def fake_holdings(**_kwargs):
        return [
            {"symbol": "BTC", "value": Decimal("100"), "cost_basis": Decimal("80"), "unrealized_pnl": Decimal("20")},
            {"symbol": "MOBILE", "value": None, "cost_basis": None, "unrealized_pnl": None},
        ], True, True

    class FakeCoinbaseClient:
        def __init__(self, *_): pass
        async def get_candles(self, *_args, **_kwargs):
            return [
                {"start": str(int(point.captured_at.timestamp())), "open": "10", "high": "12", "low": "9", "close": str(10 + index), "volume": "1"}
                for index, point in enumerate(points)
            ]
        async def aclose(self): pass

    monkeypatch.setattr(history, "get_history_repository", lambda: FakeRepository(points))
    monkeypatch.setattr(history, "get_holdings_data", fake_holdings)
    monkeypatch.setattr(history, "CoinbaseClient", FakeCoinbaseClient)

    response = TestClient(app).get("/api/portfolio/analytics?period=7d", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["history_available"] is True
    assert body["pnl_scope_value"] == "100.00"
    assert body["data_quality"] == {"cost_basis_available": True, "benchmark_available": True, "aligned_return_observations": 2, "unknown_price_holdings": 1}


def test_portfolio_analytics_returns_503_for_benchmark_failure(monkeypatch) -> None:
    async def fake_holdings(**_kwargs):
        return [], True, True

    class FailingCoinbaseClient:
        def __init__(self, *_): pass
        async def get_candles(self, *_args, **_kwargs):
            raise CoinbaseError()
        async def aclose(self): pass

    monkeypatch.setattr(history, "get_history_repository", lambda: FakeRepository([_snapshot(0, "100")]))
    monkeypatch.setattr(history, "get_holdings_data", fake_holdings)
    monkeypatch.setattr(history, "CoinbaseClient", FailingCoinbaseClient)

    response = TestClient(app).get("/api/portfolio/analytics?period=7d", headers=HEADERS)

    assert response.status_code == 503
    assert response.json()["error"] == "coinbase_unavailable"
