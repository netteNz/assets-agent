"""Manual snapshot command: python -m app.capture_snapshot."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.history import PortfolioHistoryRepository, PortfolioSnapshot, snapshot_bucket
from app.portfolio_service import STABLECOINS, get_holdings_data
from app.config import get_settings
from app.coinbase.normalize import ZERO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def capture() -> bool:
    items, _, _ = await get_holdings_data()
    now = datetime.now(UTC)
    total = sum((item["value"] for item in items if item["value"] is not None), ZERO)
    cash = sum((item["value"] for item in items if item["symbol"] in STABLECOINS and item["value"] is not None), ZERO)
    snapshot = PortfolioSnapshot(now, snapshot_bucket(now), total, total - cash, cash, total, sum(item["value"] is None for item in items))
    written = PortfolioHistoryRepository(get_settings().portfolio_history_db_path).write(snapshot)
    logger.info("portfolio_snapshot_write bucket=%s inserted=%s unknown_price_count=%s", snapshot.bucket, written, snapshot.unknown_price_count)
    return written


def main() -> None:
    inserted = asyncio.run(capture())
    print("snapshot captured" if inserted else "snapshot already exists for this UTC hour")


if __name__ == "__main__":
    main()
