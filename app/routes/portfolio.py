from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_api_key
from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.coinbase.normalize import ZERO, account_quantity, account_symbol, money, number
from app.config import get_settings

router = APIRouter(dependencies=[Depends(require_api_key)])


async def _holdings() -> list[dict]:
    client = CoinbaseClient(get_settings())
    try:
        accounts = await client.list_accounts()
        result: list[dict] = []
        for account in accounts:
            symbol, quantity = account_symbol(account), account_quantity(account)
            if not symbol or quantity <= ZERO:
                continue
            price = Decimal("1") if symbol == "USD" else Decimal((await client.get_product(f"{symbol}-USD"))["price"])
            result.append({"symbol": symbol, "name": account.get("name") or symbol, "quantity": quantity, "price": price, "value": quantity * price})
        total = sum((item["value"] for item in result), ZERO)
        for item in result:
            item["allocation_pct"] = (item["value"] / total * Decimal("100")) if total else ZERO
        return sorted(result, key=lambda item: item["value"], reverse=True)
    except CoinbaseError as exc:
        raise HTTPException(503, detail={"error": "coinbase_unavailable", "message": "Live Coinbase data is temporarily unavailable."}) from exc
    finally:
        await client.aclose()


def _render(items: list[dict]) -> list[dict]:
    return [{
        "symbol": item["symbol"], "name": item["name"], "quantity": number(item["quantity"]),
        "price": money(item["price"]), "value": money(item["value"]),
        "allocation_pct": money(item["allocation_pct"]),
    } for item in items]


@router.get(
    "/api/portfolio/holdings",
    operation_id="get_holdings",
    summary="Get current non-zero Coinbase holdings",
    description="Returns current non-zero Coinbase asset balances, quantities, USD values, and allocation percentages. Use for owned assets or portfolio composition.",
)
async def get_holdings() -> dict:
    items = await _holdings()
    return {"currency": "USD", "holdings": _render(items), "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}


@router.get(
    "/api/portfolio/summary",
    operation_id="get_portfolio_summary",
    summary="Get the user's current Coinbase portfolio value and summarized positions",
    description="Returns the user's current Coinbase portfolio value and summarized positions. Use for total portfolio value or overall portfolio status.",
)
async def get_portfolio_summary() -> dict:
    items = await _holdings()
    total = sum((item["value"] for item in items), ZERO)
    cash = sum((item["value"] for item in items if item["symbol"] == "USD"), ZERO)
    return {
        "currency": "USD", "total_value": money(total), "crypto_value": money(total - cash), "cash_value": money(cash),
        "holding_count": len(items), "top_holdings": _render(items[:5]),
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
