"""SQLite persistence for explicitly captured portfolio value snapshots."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class PortfolioSnapshot:
    captured_at: datetime
    bucket: str
    total_value: Decimal
    crypto_value: Decimal
    cash_value: Decimal
    priced_value: Decimal
    unknown_price_count: int


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def snapshot_bucket(value: datetime) -> str:
    """An hourly UTC bucket makes repeated CLI runs idempotent without inventing data."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:00:00Z")


class PortfolioHistoryRepository:
    """The sole V2 history store. Monetary values are deliberately stored as TEXT."""

    def __init__(self, path: str):
        self.path = path
        self._memory_connection: sqlite3.Connection | None = None
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self._memory_connection = sqlite3.connect(path)
            self._memory_connection.row_factory = sqlite3.Row
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    captured_at TEXT NOT NULL,
                    bucket TEXT NOT NULL UNIQUE,
                    total_value TEXT NOT NULL,
                    crypto_value TEXT NOT NULL,
                    cash_value TEXT NOT NULL,
                    priced_value TEXT NOT NULL,
                    unknown_price_count INTEGER NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_captured_at ON portfolio_snapshots(captured_at)")

    def write(self, snapshot: PortfolioSnapshot) -> bool:
        """Insert once per bucket. False means this capture was already persisted."""
        with self._connect() as connection:
            result = connection.execute(
                """INSERT INTO portfolio_snapshots
                (captured_at, bucket, total_value, crypto_value, cash_value, priced_value, unknown_price_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket) DO NOTHING""",
                (utc_iso(snapshot.captured_at), snapshot.bucket, str(snapshot.total_value), str(snapshot.crypto_value),
                 str(snapshot.cash_value), str(snapshot.priced_value), snapshot.unknown_price_count),
            )
            return result.rowcount == 1

    def query(self, start: datetime | None = None, end: datetime | None = None) -> list[PortfolioSnapshot]:
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("captured_at >= ?")
            params.append(utc_iso(start))
        if end is not None:
            clauses.append("captured_at <= ?")
            params.append(utc_iso(end))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM portfolio_snapshots" + where + " ORDER BY captured_at", params).fetchall()
        return [PortfolioSnapshot(datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00")), row["bucket"],
                                  Decimal(row["total_value"]), Decimal(row["crypto_value"]), Decimal(row["cash_value"]),
                                  Decimal(row["priced_value"]), int(row["unknown_price_count"])) for row in rows]

    def coverage(self) -> tuple[datetime | None, datetime | None]:
        with self._connect() as connection:
            row = connection.execute("SELECT MIN(captured_at) AS first, MAX(captured_at) AS last FROM portfolio_snapshots").fetchone()
        if not row or row["first"] is None:
            return None, None
        return (datetime.fromisoformat(row["first"].replace("Z", "+00:00")), datetime.fromisoformat(row["last"].replace("Z", "+00:00")))
