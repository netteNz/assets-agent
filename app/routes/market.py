import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_api_key
from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.coingecko.client import CoinGeckoClient, CoinGeckoError
from app.config import get_settings
from app.coinbase.normalize import money
from app.market_analytics import market_metrics, normalize_candles
from app.schemas import ErrorDetail, MarketCandle, MarketContextResponse, MarketHistoryResponse, MarketPriceResponse

router = APIRouter(dependencies=[Depends(require_api_key)])
PRODUCT_ID = re.compile(r"^[A-Z0-9]{2,20}-USD$")
PERIOD_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365}
MARKET_GRANULARITIES = {
    ("1d", "1h"): "ONE_HOUR", ("7d", "1h"): "ONE_HOUR", ("7d", "6h"): "SIX_HOUR",
    ("30d", "1d"): "ONE_DAY", ("90d", "1d"): "ONE_DAY", ("1y", "1d"): "ONE_DAY",
}


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


async def _market_context(base_symbol: str) -> tuple[dict, bool]:
    settings = get_settings()
    gecko = CoinGeckoClient(timeout_seconds=settings.coinbase_timeout_seconds, market_cache_ttl_seconds=settings.coingecko_market_cache_ttl_seconds, metadata_cache_ttl_seconds=settings.coingecko_metadata_cache_ttl_seconds)
    try:
        markets = await gecko.get_markets([base_symbol])
        coin = markets.get(base_symbol.upper())
        if not coin:
            return {"market_cap_rank": None, "market_cap": None, "ath": None, "ath_change_pct": None}, True
        return {
            "market_cap_rank": coin["market_cap_rank"],
            "market_cap": money(Decimal(str(coin["market_cap"]))) if coin["market_cap"] is not None else None,
            "ath": money(Decimal(str(coin["ath"]))) if coin["ath"] is not None else None,
            "ath_change_pct": money(Decimal(str(coin["ath_change_percentage"]))) if coin["ath_change_percentage"] is not None else None,
        }, True
    except (CoinGeckoError, ValueError, KeyError):
        return {"market_cap_rank": None, "market_cap": None, "ath": None, "ath_change_pct": None}, False
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
    context, market_context_available = await _market_context(product_id.split("-")[0])
    return MarketPriceResponse(
        product_id=product_id, price=money(price), currency="USD",
        change_24h_pct=money(change_24h_pct) if change_24h_pct is not None else None,
        change_7d_pct=money(change_7d_pct) if change_7d_pct is not None else None,
        bid=money(bid) if bid is not None else None,
        ask=money(ask) if ask is not None else None,
        spread_pct=money(spread_pct) if spread_pct is not None else None,
        market_context_available=market_context_available,
        **context,
        retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _validate_product(product_id: str) -> str:
    product_id = product_id.upper()
    if not PRODUCT_ID.fullmatch(product_id):
        raise HTTPException(400, detail={"error": "invalid_product", "message": "The requested Coinbase product is invalid or unsupported."})
    return product_id


def _context_null(product_id: str, available: bool = False, coin_id: str | None = None) -> MarketContextResponse:
    return MarketContextResponse(product_id=product_id, market_context_available=available, coin_id=coin_id, market_cap_rank=None,
                                 market_cap=None, fully_diluted_valuation=None, circulating_supply=None, total_supply=None, max_supply=None,
                                 ath=None, ath_change_pct=None, total_volume=None, volume_market_cap_ratio=None, change_24h_pct=None,
                                 change_7d_pct=None, change_30d_pct=None, change_1y_pct=None, retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))


@router.get("/api/market/{product_id}/history", operation_id="get_market_history", summary="Get normalized Coinbase USD candle history", description="Returns Coinbase-only candle history and deterministic price-risk metrics. Approved period/granularity combinations cap output at 150 candles.", response_model=MarketHistoryResponse, responses={400: {"model": ErrorDetail}, 503: {"model": ErrorDetail}})
async def get_market_history(
    product_id: str,
    period: Literal["1d", "7d", "30d", "90d", "1y"] = Query("7d"),
    granularity: Literal["1h", "6h", "1d"] = Query("1h"),
) -> MarketHistoryResponse:
    product_id = _validate_product(product_id)
    coinbase_granularity = MARKET_GRANULARITIES.get((period, granularity))
    if coinbase_granularity is None:
        raise HTTPException(422, detail={"error": "unsupported_period_granularity", "message": "This period/granularity combination is not supported."})
    now = datetime.now(UTC)
    client = CoinbaseClient(get_settings())
    try:
        # Validate product before accepting an empty candle result as history.
        await client.get_product(product_id)
        raw = await client.get_candles(product_id, granularity=coinbase_granularity, start=str(int((now - timedelta(days=PERIOD_DAYS[period])).timestamp())), end=str(int(now.timestamp())))
    except CoinbaseError as exc:
        if exc.status_code == 404:
            raise HTTPException(400, detail={"error": "invalid_product", "message": "The requested Coinbase product is invalid or unsupported."}) from exc
        raise HTTPException(503, detail={"error": "coinbase_unavailable", "message": "Coinbase market history is temporarily unavailable."}) from exc
    finally:
        await client.aclose()
    candles = normalize_candles(raw)[-150:]
    metrics = market_metrics(candles, 365 * (24 if granularity == "1h" else 4 if granularity == "6h" else 1))
    return MarketHistoryResponse(product_id=product_id, period=period, granularity=granularity,
        candles=[MarketCandle(timestamp=item["timestamp"], **{key: money(item[key]) for key in ("open", "high", "low", "close", "volume")}) for item in candles],
        point_count=len(candles), **{key: money(value) if value is not None else None for key, value in metrics.items()},
        retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))


@router.get("/api/market/{product_id}/context", operation_id="get_market_context", summary="Get mapped CoinGecko market context", description="Returns broader optional CoinGecko context for explicitly mapped Coinbase assets. Unmapped assets and CoinGecko failures return null fields without substituting another asset.", response_model=MarketContextResponse, responses={400: {"model": ErrorDetail}})
async def get_market_context(product_id: str) -> MarketContextResponse:
    product_id = _validate_product(product_id)
    symbol = product_id.split("-")[0]
    settings = get_settings()
    gecko = CoinGeckoClient(timeout_seconds=settings.coinbase_timeout_seconds, market_cache_ttl_seconds=settings.coingecko_market_cache_ttl_seconds, metadata_cache_ttl_seconds=settings.coingecko_metadata_cache_ttl_seconds)
    try:
        coin_id, context = await gecko.get_context(symbol)
        if context is None:
            return _context_null(product_id, coin_id=coin_id)
        volume = context["total_volume"]
        market_cap = context["market_cap"]
        ratio = Decimal(str(volume)) / Decimal(str(market_cap)) if volume is not None and market_cap not in (None, 0) else None
        rendered = {key: money(Decimal(str(context[key]))) if context.get(key) is not None else None for key in ("market_cap", "fully_diluted_valuation", "circulating_supply", "total_supply", "max_supply", "ath", "ath_change_pct", "total_volume", "change_24h_pct", "change_7d_pct", "change_30d_pct", "change_1y_pct")}
        return MarketContextResponse(product_id=product_id, market_context_available=True, coin_id=coin_id, market_cap_rank=context["market_cap_rank"], volume_market_cap_ratio=money(ratio) if ratio is not None else None, retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"), **rendered)
    except CoinGeckoError:
        return _context_null(product_id)
    finally:
        await gecko.aclose()
