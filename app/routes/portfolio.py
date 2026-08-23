from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_api_key
from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.coinbase.cost_basis import compute_average_cost_basis
from app.coinbase.normalize import ZERO, account_quantity, account_symbol, money, number
from app.coingecko.client import CoinGeckoClient, CoinGeckoError
from app.config import get_settings
from app.schemas import ErrorDetail, Holding, HoldingsResponse, PortfolioSummaryResponse

router = APIRouter(dependencies=[Depends(require_api_key)])

STABLECOINS = {"USD", "USDC", "USDT"}


async def _price(client: CoinbaseClient, symbol: str) -> Decimal | None:
    if symbol in STABLECOINS:
        return Decimal("1")
    try:
        return Decimal((await client.get_product(f"{symbol}-USD"))["price"])
    except CoinbaseError as exc:
        if exc.status_code == 404:
            return None
        raise


async def _market_context(symbols: list[str]) -> tuple[dict[str, dict], bool]:
    gecko = CoinGeckoClient(timeout_seconds=get_settings().coinbase_timeout_seconds)
    try:
        return await gecko.get_markets(symbols), True
    except CoinGeckoError:
        return {}, False
    finally:
        await gecko.aclose()


async def _holdings(include_cost_basis: bool = False, include_market_context: bool = False) -> tuple[list[dict], bool, bool]:
    client = CoinbaseClient(get_settings())
    try:
        accounts = await client.list_accounts()
        result: list[dict] = []
        for account in accounts:
            symbol, quantity = account_symbol(account), account_quantity(account)
            if not symbol or quantity <= ZERO:
                continue
            price = await _price(client, symbol)
            value = quantity * price if price is not None else None
            result.append({"symbol": symbol, "name": account.get("name") or symbol, "quantity": quantity, "price": price, "value": value})
        total = sum((item["value"] for item in result if item["value"] is not None), ZERO)
        for item in result:
            item["allocation_pct"] = (item["value"] / total * Decimal("100")) if item["value"] is not None and total else None

        cost_basis_available = True
        if include_cost_basis:
            try:
                positions = compute_average_cost_basis(await client.list_fills())
            except CoinbaseError:
                positions = {}
                cost_basis_available = False
            for item in result:
                position = positions.get(item["symbol"])
                avg_cost = position["avg_cost"] if position else None
                if avg_cost is None or item["value"] is None:
                    item["cost_basis"] = item["unrealized_pnl"] = item["unrealized_pnl_pct"] = None
                    continue
                cost_basis = avg_cost * item["quantity"]
                unrealized_pnl = item["value"] - cost_basis
                item["cost_basis"] = cost_basis
                item["unrealized_pnl"] = unrealized_pnl
                item["unrealized_pnl_pct"] = (unrealized_pnl / cost_basis * Decimal("100")) if cost_basis > ZERO else None

        market_context_available = True
        if include_market_context:
            context, market_context_available = await _market_context([item["symbol"] for item in result if item["symbol"] not in STABLECOINS])
            for item in result:
                coin = context.get(item["symbol"])
                item["market_cap_rank"] = coin["market_cap_rank"] if coin else None
                item["market_cap"] = Decimal(str(coin["market_cap"])) if coin and coin["market_cap"] is not None else None

        return sorted(result, key=lambda item: item["value"] if item["value"] is not None else ZERO, reverse=True), cost_basis_available, market_context_available
    except CoinbaseError as exc:
        raise HTTPException(503, detail={"error": "coinbase_unavailable", "message": "Live Coinbase data is temporarily unavailable."}) from exc
    finally:
        await client.aclose()


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
    items, cost_basis_available, market_context_available = await _holdings(include_cost_basis, include_market_context)
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
    items, cost_basis_available, market_context_available = await _holdings(include_cost_basis, include_market_context)
    total = sum((item["value"] for item in items if item["value"] is not None), ZERO)
    cash = sum((item["value"] for item in items if item["symbol"] == "USD" and item["value"] is not None), ZERO)
    unpriced = sum(1 for item in items if item["value"] is None)

    cost_basis_value = unrealized_pnl = unrealized_pnl_pct = None
    holdings_with_unknown_cost_basis = None
    if include_cost_basis:
        known = [item for item in items if item.get("cost_basis") is not None]
        cost_basis_total = sum((item["cost_basis"] for item in known), ZERO)
        unrealized_pnl_total = sum((item["unrealized_pnl"] for item in known), ZERO)
        cost_basis_value = money(cost_basis_total) if known else None
        unrealized_pnl = money(unrealized_pnl_total) if known else None
        unrealized_pnl_pct = money(unrealized_pnl_total / cost_basis_total * Decimal("100")) if cost_basis_total > ZERO else None
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
    )
