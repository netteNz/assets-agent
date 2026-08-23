from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_api_key
from app.coinbase.normalize import ZERO, money, number
from app.portfolio_service import get_holdings_data
from app.schemas import ErrorDetail, Holding, HoldingsResponse, PortfolioSummaryResponse
from app.portfolio_analytics import concentration, cost_basis_summary

router = APIRouter(dependencies=[Depends(require_api_key)])

def _render(items: list[dict]) -> list[Holding]:
    return [
        Holding(
            symbol=item["symbol"], name=item["name"], quantity=number(item["quantity"]),
            price=money(item["price"]) if item["price"] is not None else None,
            value=money(item["value"]) if item["value"] is not None else None,
            allocation_pct=money(item["allocation_pct"]) if item["allocation_pct"] is not None else None,
            cost_basis=money(item["cost_basis"]) if item.get("cost_basis") is not None else None,
            unrealized_pnl=money(item["unrealized_pnl"]) if item.get("unrealized_pnl") is not None else None,
            unrealized_pnl_pct=money(item["unrealized_pnl_pct"]) if item.get("unrealized_pnl_pct") is not None else None,
            market_cap_rank=item.get("market_cap_rank"),
            market_cap=money(item["market_cap"]) if item.get("market_cap") is not None else None,
        )
        for item in items
    ]


@router.get(
    "/api/portfolio/holdings",
    operation_id="get_holdings",
    summary="Get current non-zero Coinbase holdings",
    description="Returns current non-zero Coinbase asset balances, quantities, USD values, and allocation percentages. Use for owned assets or portfolio composition.",
    response_model=HoldingsResponse,
    responses={503: {"model": ErrorDetail}},
)
async def get_holdings(
    include_cost_basis: bool = Query(False, description="Include cost basis and unrealized P&L per holding, derived from fills history."),
    include_market_context: bool = Query(False, description="Include CoinGecko market cap rank and market cap per holding, for interpretation and analysis."),
) -> HoldingsResponse:
    items, cost_basis_available, market_context_available = await get_holdings_data(include_cost_basis, include_market_context)
    return HoldingsResponse(
        currency="USD", holdings=_render(items), retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        cost_basis_available=cost_basis_available if include_cost_basis else None,
        market_context_available=market_context_available if include_market_context else None,
    )


@router.get(
    "/api/portfolio/summary",
    operation_id="get_portfolio_summary",
    summary="Get the user's current Coinbase portfolio value and summarized positions",
    description="Returns the user's current Coinbase portfolio value and summarized positions. Use for total portfolio value or overall portfolio status.",
    response_model=PortfolioSummaryResponse,
    responses={503: {"model": ErrorDetail}},
)
async def get_portfolio_summary(
    include_cost_basis: bool = Query(False, description="Include aggregate cost basis and unrealized P&L, derived from fills history."),
    include_market_context: bool = Query(False, description="Include CoinGecko market cap rank and market cap on top holdings, for interpretation and analysis."),
) -> PortfolioSummaryResponse:
    items, cost_basis_available, market_context_available = await get_holdings_data(include_cost_basis, include_market_context)
    total = sum((item["value"] for item in items if item["value"] is not None), ZERO)
    cash = sum((item["value"] for item in items if item["symbol"] == "USD" and item["value"] is not None), ZERO)
    unpriced = sum(1 for item in items if item["value"] is None)

    cost_basis_value = unrealized_pnl = unrealized_pnl_pct = None
    holdings_with_unknown_cost_basis = None
    pnl = cost_basis_summary(items, cost_basis_available) if include_cost_basis else None
    if include_cost_basis:
        known = [item for item in items if item.get("cost_basis") is not None]
        cost_basis_value = money(pnl["cost_basis_value"]) if pnl["cost_basis_value"] is not None else None
        unrealized_pnl = money(pnl["unrealized_pnl"]) if pnl["unrealized_pnl"] is not None else None
        unrealized_pnl_pct = money(pnl["unrealized_pnl_pct"]) if pnl["unrealized_pnl_pct"] is not None else None
        holdings_with_unknown_cost_basis = len(items) - len(known)

    return PortfolioSummaryResponse(
        currency="USD", total_value=money(total), crypto_value=money(total - cash), cash_value=money(cash),
        holding_count=len(items), top_holdings=_render(items[:5]),
        retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        holdings_with_unknown_price=unpriced,
        cost_basis_available=cost_basis_available if include_cost_basis else None,
        cost_basis_value=cost_basis_value, unrealized_pnl=unrealized_pnl, unrealized_pnl_pct=unrealized_pnl_pct,
        holdings_with_unknown_cost_basis=holdings_with_unknown_cost_basis,
        market_context_available=market_context_available if include_market_context else None,
        concentration={key: money(value) if value is not None else None for key, value in concentration(items).items()},
        pnl_scope_value=money(pnl["pnl_scope_value"]) if pnl and pnl["pnl_scope_value"] is not None else None,
        cost_basis_coverage_pct=money(pnl["cost_basis_coverage_pct"]) if pnl and pnl["cost_basis_coverage_pct"] is not None else None,
        profitable_count=pnl["profitable_count"] if pnl else None, losing_count=pnl["losing_count"] if pnl else None, flat_count=pnl["flat_count"] if pnl else None,
        largest_known_gain=_render([pnl["largest_known_gain"]])[0] if pnl and pnl["largest_known_gain"] else None,
        largest_known_loss=_render([pnl["largest_known_loss"]])[0] if pnl and pnl["largest_known_loss"] else None,
    )
