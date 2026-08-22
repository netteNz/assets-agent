import re
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_api_key
from app.coinbase.client import CoinbaseClient, CoinbaseError
from app.config import get_settings
from app.coinbase.normalize import money

router = APIRouter(dependencies=[Depends(require_api_key)])
PRODUCT_ID = re.compile(r"^[A-Z0-9]{2,20}-USD$")


@router.get(
    "/api/market/{product_id}",
    operation_id="get_market_price",
    summary="Get a live Coinbase USD product price",
    description="Returns the current Coinbase price for a requested product such as BTC-USD or ETH-USD. Use for live price questions.",
)
async def get_market_price(product_id: str) -> dict:
    product_id = product_id.upper()
    if not PRODUCT_ID.fullmatch(product_id):
        raise HTTPException(400, detail={"error": "invalid_product", "message": "The requested Coinbase product is invalid or unsupported."})
    client = CoinbaseClient(get_settings())
    try:
        product = await client.get_product(product_id)
    except CoinbaseError as exc:
        if exc.status_code == 404:
            raise HTTPException(400, detail={"error": "invalid_product", "message": "The requested Coinbase product is invalid or unsupported."}) from exc
        raise HTTPException(503, detail={"error": "coinbase_unavailable", "message": "Live Coinbase data is temporarily unavailable."}) from exc
    finally:
        await client.aclose()
    price = Decimal(product["price"])
    return {"product_id": product_id, "price": money(price), "currency": "USD", "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
