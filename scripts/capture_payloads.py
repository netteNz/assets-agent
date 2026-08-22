"""Dump raw Coinbase responses and this app's rendered API responses to disk for inspection.

Usage: .venv/Scripts/python.exe scripts/capture_payloads.py [PRODUCT_ID]
Requires a real .env with working Coinbase credentials. Reads live data, no writes.
"""
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.coinbase.client import CoinbaseClient
from app.routes.market import get_market_price
from app.routes.portfolio import get_holdings, get_portfolio_summary

OUT_DIR = Path(__file__).resolve().parent.parent / "captures"


def _default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not serializable: {type(obj)}")


async def _capture(name: str, coro) -> None:
    try:
        payload = await coro
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - capture script, want to record failures too
        payload = {"error": str(exc)}
        status = "error"
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=_default), encoding="utf-8")
    count = len(payload) if isinstance(payload, list) else ""
    print(f"[{status}] {name} -> {path} {count}")


async def main() -> None:
    product_id = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC-USD"
    OUT_DIR.mkdir(exist_ok=True)

    settings = get_settings()
    client = CoinbaseClient(settings)
    try:
        await _capture("raw_list_accounts", client.list_accounts())
        await _capture("raw_list_fills", client.list_fills())
        await _capture("raw_get_product", client.get_product(product_id))
        now = datetime.now(UTC)
        await _capture("raw_get_candles", client.get_candles(
            product_id, granularity="ONE_DAY",
            start=str(int(now.timestamp()) - 8 * 86400), end=str(int(now.timestamp())),
        ))
        await _capture("raw_get_best_bid_ask", client.get_best_bid_ask([product_id]))
    finally:
        await client.aclose()

    await _capture("rendered_holdings", get_holdings(include_cost_basis=True))
    await _capture("rendered_portfolio_summary", get_portfolio_summary(include_cost_basis=True))
    await _capture("rendered_market_price", get_market_price(product_id))

    print(f"\nAll payloads written to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
