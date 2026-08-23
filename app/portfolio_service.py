"""Live Coinbase portfolio orchestration, independent of the HTTP route layer."""

from decimal import Decimal

from fastapi import HTTPException

from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.coinbase.cost_basis import compute_average_cost_basis
from app.coinbase.normalize import ZERO, account_quantity, account_symbol
from app.coingecko.client import CoinGeckoClient, CoinGeckoError
from app.config import get_settings

STABLECOINS = {"USD", "USDC", "USDT"}


async def _price(client: CoinbaseClient, symbol: str) -> Decimal | None:
    if symbol in STABLECOINS:
        return Decimal("1")
    try:
        return Decimal((await client.get_product(f"{symbol}-USD"))["price"])
    except CoinbaseError as exc:
        if exc.status_code in (404, 429):
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


async def get_holdings_data(include_cost_basis: bool = False, include_market_context: bool = False) -> tuple[list[dict], bool, bool]:
    """Return normalized live holdings and optional analysis data without HTTP serialization."""
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
                if avg_cost is None or item["value"] is None or position["quantity"] != item["quantity"]:
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
