"""Read-only portfolio history and combined analytics endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_api_key
from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.coinbase.normalize import money
from app.config import get_settings
from app.history import PortfolioHistoryRepository, PortfolioSnapshot
from app.market_analytics import normalize_candles
from app.portfolio_analytics import aligned_returns, concentration, correlation_and_beta, high_low, max_drawdown_pct, realized_volatility_pct, value_change
from app.portfolio_service import get_holdings_data
from app.schemas import ErrorDetail, HistoryPoint, PortfolioAnalyticsResponse, PortfolioHistoryResponse

router = APIRouter(dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)
PERIOD_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365}
HISTORY_GRANULARITIES = {("1d", "hour"), ("7d", "hour"), ("7d", "day"), ("30d", "day"), ("90d", "day"), ("1y", "day"), ("all", "day")}


def get_history_repository() -> PortfolioHistoryRepository:
    return PortfolioHistoryRepository(get_settings().portfolio_history_db_path)


def _start(period: str, now: datetime) -> datetime | None:
    return now - timedelta(days=PERIOD_DAYS[period]) if period != "all" else None


def _bucketed(points: list[PortfolioSnapshot], granularity: str) -> list[PortfolioSnapshot]:
    """Use the latest persisted sample in each requested bucket; never synthesize samples."""
    selected: dict[str, PortfolioSnapshot] = {}
    for point in points:
        key = point.captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H") if granularity == "hour" else point.captured_at.astimezone(UTC).strftime("%Y-%m-%d")
        selected[key] = point
    return list(selected.values())


def _render_history(points: list[PortfolioSnapshot]) -> list[HistoryPoint]:
    return [HistoryPoint(timestamp=point.captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"), total_value=money(point.total_value),
                         crypto_value=money(point.crypto_value), cash_value=money(point.cash_value), priced_value=money(point.priced_value), unknown_price_count=point.unknown_price_count) for point in points]


@router.get("/api/portfolio/history", operation_id="get_portfolio_history", summary="Get persisted portfolio value history", description="Returns only manually captured SQLite snapshots. Change fields describe value change, not cash-flow-adjusted investment return.", response_model=PortfolioHistoryResponse)
async def get_portfolio_history(
    period: Literal["1d", "7d", "30d", "90d", "1y", "all"] = Query("30d"),
    granularity: Literal["hour", "day"] = Query("day"),
) -> PortfolioHistoryResponse:
    if (period, granularity) not in HISTORY_GRANULARITIES:
        raise HTTPException(422, detail={"error": "unsupported_period_granularity", "message": "This period/granularity combination is not supported."})
    now = datetime.now(UTC)
    points = _bucketed(get_history_repository().query(_start(period, now), now), granularity)
    logger.info("portfolio_history_query period=%s granularity=%s points=%d", period, granularity, len(points))
    values = [point.total_value for point in points]
    changes = value_change(values)
    high, low = high_low(values)
    return PortfolioHistoryResponse(period=period, granularity=granularity, points=_render_history(points), point_count=len(points), available=bool(points),
        coverage_start=points[0].captured_at.isoformat().replace("+00:00", "Z") if points else None,
        coverage_end=points[-1].captured_at.isoformat().replace("+00:00", "Z") if points else None,
        change_value=money(changes["change_value"]) if changes["change_value"] is not None else None,
        change_pct=money(changes["change_pct"]) if changes["change_pct"] is not None else None,
        high_value=money(high) if high is not None else None, low_value=money(low) if low is not None else None,
        retrieved_at=now.isoformat().replace("+00:00", "Z"))


@router.get("/api/portfolio/analytics", operation_id="get_portfolio_analytics", summary="Get portfolio value, concentration, and benchmark analytics", description="Combines live holdings, persisted snapshots, and aligned Coinbase benchmark candles. Value changes are not investment returns.", response_model=PortfolioAnalyticsResponse, responses={503: {"model": ErrorDetail}})
async def get_portfolio_analytics(
    period: Literal["1d", "7d", "30d", "90d", "1y", "all"] = Query("30d"),
    benchmark: str = Query("BTC-USD", pattern=r"^[A-Z0-9]{2,20}-USD$"),
) -> PortfolioAnalyticsResponse:
    started = time.monotonic()
    benchmark = benchmark.upper()
    now = datetime.now(UTC)
    items, cost_basis_available, _ = await get_holdings_data(include_cost_basis=True)
    snapshots = get_history_repository().query(_start(period, now), now)
    values = [point.total_value for point in snapshots]
    changes = value_change(values)
    high, low = high_low(values)
    benchmark_candles: list[dict] = []
    benchmark_available = False
    client = CoinbaseClient(get_settings())
    try:
        # "all" is bounded by the actual persisted coverage: the API never asks
        # Coinbase for an invented epoch or reports unavailable benchmark data as zero.
        benchmark_start = _start(period, now) or (snapshots[0].captured_at if snapshots else None)
        if benchmark_start is not None:
            raw = await client.get_candles(benchmark, granularity="ONE_DAY", start=str(int(benchmark_start.timestamp())), end=str(int(now.timestamp())))
            benchmark_candles = normalize_candles(raw)
            benchmark_available = True
    except CoinbaseError as exc:
        raise HTTPException(503, detail={"error": "coinbase_unavailable", "message": "Coinbase benchmark history is temporarily unavailable."}) from exc
    finally:
        await client.aclose()
    snapshot_series = [(point.captured_at.astimezone(UTC).strftime("%Y-%m-%d"), point.total_value) for point in snapshots]
    benchmark_series = [(point["timestamp"][:10], point["close"]) for point in benchmark_candles]
    aligned = aligned_returns(snapshot_series, benchmark_series)
    corr, beta = correlation_and_beta(*aligned) if aligned else (None, None)
    benchmark_change = value_change([point["close"] for point in benchmark_candles])["change_pct"] if benchmark_candles else None
    known = [item for item in items if item.get("cost_basis") is not None]
    scope = sum((item["value"] for item in known), Decimal("0"))
    priced = sum((item["value"] for item in items if item.get("value") is not None), Decimal("0"))
    pnl = sum((item["unrealized_pnl"] for item in known), Decimal("0")) if known else None
    response = PortfolioAnalyticsResponse(period=period, benchmark=benchmark, current_total_value=money(priced), history_available=bool(snapshots),
        coverage_start=snapshots[0].captured_at.isoformat().replace("+00:00", "Z") if snapshots else None, coverage_end=snapshots[-1].captured_at.isoformat().replace("+00:00", "Z") if snapshots else None,
        value_change=money(changes["change_value"]) if changes["change_value"] is not None else None, value_change_pct=money(changes["change_pct"]) if changes["change_pct"] is not None else None,
        high_value=money(high) if high is not None else None, low_value=money(low) if low is not None else None, max_drawdown_pct=money(max_drawdown_pct(values)) if max_drawdown_pct(values) is not None else None,
        realized_volatility_pct=money(realized_volatility_pct(values)) if realized_volatility_pct(values) is not None else None,
        concentration={key: money(value) if value is not None else None for key, value in concentration(items).items()},
        pnl_scope_value=money(scope) if cost_basis_available else None, cost_basis_coverage_pct=money(scope / priced * Decimal("100")) if cost_basis_available and priced else None,
        unrealized_pnl=money(pnl) if pnl is not None and cost_basis_available else None, benchmark_change_pct=money(benchmark_change) if benchmark_change is not None else None,
        correlation=money(corr) if corr is not None else None, beta=money(beta) if beta is not None else None,
        data_quality={"cost_basis_available": cost_basis_available, "benchmark_available": benchmark_available, "aligned_return_observations": len(aligned[0]) if aligned else 0, "unknown_price_holdings": sum(item["value"] is None for item in items)},
        retrieved_at=now.isoformat().replace("+00:00", "Z"))
    logger.info("portfolio_analytics_complete period=%s benchmark=%s latency_ms=%d", period, benchmark, (time.monotonic() - started) * 1000)
    return response
