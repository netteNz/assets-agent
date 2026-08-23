"""Pure Coinbase-candle normalization and market risk calculations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.portfolio_analytics import high_low, max_drawdown_pct, realized_volatility_pct, value_change


def normalize_candles(candles: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for candle in candles:
        try:
            raw_time = candle.get("start", candle.get("timestamp"))
            timestamp = datetime.fromtimestamp(int(raw_time), UTC).isoformat().replace("+00:00", "Z") if str(raw_time).isdigit() else str(raw_time)
            normalized.append({"timestamp": timestamp, "open": Decimal(str(candle["open"])), "high": Decimal(str(candle["high"])),
                               "low": Decimal(str(candle["low"])), "close": Decimal(str(candle["close"])), "volume": Decimal(str(candle.get("volume", "0")))})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(normalized, key=lambda item: item["timestamp"])


def market_metrics(candles: list[dict], periods_per_year: int) -> dict[str, Decimal | None]:
    closes = [item["close"] for item in candles]
    high, low = high_low(closes)
    return {**value_change(closes), "high": high, "low": low, "max_drawdown_pct": max_drawdown_pct(closes),
            "realized_volatility_pct": realized_volatility_pct(closes, periods_per_year)}
