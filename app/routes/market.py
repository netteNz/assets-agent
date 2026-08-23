import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_api_key
from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.coingecko.client import CoinGeckoClient, CoinGeckoError
from app.config import get_settings
from app.coinbase.normalize import money
from app.schemas import ErrorDetail, MarketPriceResponse

router = APIRouter(dependencies=[Depends(require_api_key)])
PRODUCT_ID = re.compile(r"^[A-Z0-9]{2,20}-USD$")


def _pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    return (new - old) / old * Decimal("100") if old != 0 else None


async def _price_changes(client: CoinbaseClient, product_id: str) -> tuple[Decimal | None, Decimal | None]:
    try:
        now = datetime.now(UTC)
        candles = await client.get_candles(
            product_id, granularity="ONE_DAY",
            start=str(int((now - timedelta(days=8)).timestamp())), end=str(int(now.timestamp())),
        )
        change_24h = _pct_change(Decimal(candles[1]["close"]), Decimal(candles[0]["close"]))
        change_7d = _pct_change(Decimal(candles[min(6, len(candles) - 1)]["close"]), Decimal(candles[0]["close"]))
        return change_24h, change_7d
    except (CoinbaseError, IndexError, KeyError, ValueError):
        return None, None


async def _spread(client: CoinbaseClient, product_id: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    try:
        pricebooks = await client.get_best_bid_ask([product_id])
        book = pricebooks[0]
        bid = Decimal(book["bids"][0]["price"])
        ask = Decimal(book["asks"][0]["price"])
        spread_pct = (ask - bid) / ask * Decimal("100") if ask != 0 else None
        return bid, ask, spread_pct
    except (CoinbaseError, IndexError, KeyError, ValueError):
        return None, None, None


async def _market_context(base_symbol: str) -> dict:
    gecko = CoinGeckoClient(timeout_seconds=get_settings().coinbase_timeout_seconds)
    try:
        markets = await gecko.get_markets([base_symbol])
        coin = markets.get(base_symbol.upper())
        if not coin:
            return {"market_cap_rank": None, "market_cap": None, "ath": None, "ath_change_pct": None}
        return {
            "market_cap_rank": coin["market_cap_rank"],
            "market_cap": money(Decimal(str(coin["market_cap"]))) if coin["market_cap"] is not None else None,
            "ath": money(Decimal(str(coin["ath"]))) if coin["ath"] is not None else None,
            "ath_change_pct": money(Decimal(str(coin["ath_change_percentage"]))) if coin["ath_change_percentage"] is not None else None,
        }
    except (CoinGeckoError, ValueError, KeyError):
        return {"market_cap_rank": None, "market_cap": None, "ath": None, "ath_change_pct": None}
    finally:
        await gecko.aclose()


@router.get(
    "/api/market/{product_id}",
    operation_id="get_market_price",
    summary="Get a live Coinbase USD product price",
    description="Returns the current Coinbase price for a requested product such as BTC-USD or ETH-USD. Use for live price questions.",
    response_model=MarketPriceResponse,
    responses={400: {"model": ErrorDetail}, 503: {"model": ErrorDetail}},
)
async def get_market_price(product_id: str) -> MarketPriceResponse:
    product_id = product_id.upper()
    if not PRODUCT_ID.fullmatch(product_id):
        raise HTTPException(400, detail={"error": "invalid_product", "message": "The requested Coinbase product is invalid or unsupported."})
    client = CoinbaseClient(get_settings())
    try:
        product = await client.get_product(product_id)
        change_24h_pct, change_7d_pct = await _price_changes(client, product_id)
        bid, ask, spread_pct = await _spread(client, product_id)
    except CoinbaseError as exc:
        if exc.status_code == 404:
            raise HTTPException(400, detail={"error": "invalid_product", "message": "The requested Coinbase product is invalid or unsupported."}) from exc
        raise HTTPException(503, detail={"error": "coinbase_unavailable", "message": "Live Coinbase data is temporarily unavailable."}) from exc
    finally:
        await client.aclose()
    price = Decimal(product["price"])
    context = await _market_context(product_id.split("-")[0])
    return MarketPriceResponse(
        product_id=product_id, price=money(price), currency="USD",
        change_24h_pct=money(change_24h_pct) if change_24h_pct is not None else None,
        change_7d_pct=money(change_7d_pct) if change_7d_pct is not None else None,
        bid=money(bid) if bid is not None else None,
        ask=money(ask) if ask is not None else None,
        spread_pct=money(spread_pct) if spread_pct is not None else None,
        **context,
        retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
