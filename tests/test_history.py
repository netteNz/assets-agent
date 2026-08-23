from datetime import UTC, datetime
from decimal import Decimal

from app.history import PortfolioHistoryRepository, PortfolioSnapshot, snapshot_bucket


def _snapshot(hour: int) -> PortfolioSnapshot:
    timestamp = datetime(2025, 1, 1, hour, tzinfo=UTC)
    return PortfolioSnapshot(timestamp, snapshot_bucket(timestamp), Decimal("100"), Decimal("90"), Decimal("10"), Decimal("100"), 0)


def test_sqlite_text_values_and_idempotent_bucket() -> None:
    repo = PortfolioHistoryRepository(":memory:")
    assert repo.write(_snapshot(1)) is True
    assert repo.write(_snapshot(1)) is False
    points = repo.query()
    assert points[0].total_value == Decimal("100")
    assert repo.coverage() == (points[0].captured_at, points[0].captured_at)
