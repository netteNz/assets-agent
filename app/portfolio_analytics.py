"""Pure Decimal portfolio calculations. Null represents unavailable evidence."""

from __future__ import annotations

from decimal import Decimal, getcontext
from math import sqrt
from typing import Iterable

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _values(items: Iterable[dict]) -> list[Decimal]:
    return [Decimal(item["value"]) for item in items if item.get("value") is not None and Decimal(item["value"]) >= ZERO]


def concentration(items: Iterable[dict]) -> dict[str, Decimal | None]:
    valued = [item for item in items if item.get("value") is not None]
    total = sum((Decimal(item["value"]) for item in valued), ZERO)
    if total <= ZERO:
        return {"largest_position_pct": None, "top_2_pct": None, "top_3_pct": None, "top_5_pct": None,
                "hhi": None, "btc_pct": None, "eth_pct": None, "long_tail_pct": None}
    shares = sorted((Decimal(item["value"]) / total * HUNDRED for item in valued), reverse=True)
    by_symbol = {str(item.get("symbol", "")).upper(): Decimal(item["value"]) / total * HUNDRED for item in valued}
    return {
        "largest_position_pct": shares[0],
        "top_2_pct": sum(shares[:2], ZERO), "top_3_pct": sum(shares[:3], ZERO), "top_5_pct": sum(shares[:5], ZERO),
        "hhi": sum((share * share for share in shares), ZERO),
        "btc_pct": by_symbol.get("BTC"), "eth_pct": by_symbol.get("ETH"),
        "long_tail_pct": sum(shares[5:], ZERO),
    }


def value_change(values: list[Decimal]) -> dict[str, Decimal | None]:
    if len(values) < 2:
        return {"change_value": None, "change_pct": None}
    change = values[-1] - values[0]
    return {"change_value": change, "change_pct": change / values[0] * HUNDRED if values[0] != ZERO else None}


def high_low(values: list[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    return (max(values), min(values)) if values else (None, None)


def max_drawdown_pct(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    peak: Decimal | None = None
    worst = ZERO
    for value in values:
        if peak is None or value > peak:
            peak = value
        if peak and peak != ZERO:
            worst = min(worst, (value - peak) / peak * HUNDRED)
    return worst


def log_returns(values: list[Decimal]) -> list[Decimal] | None:
    # Decimal has no portable ln before 3.11; float conversion only serves the logarithm,
    # while all public monetary calculations remain Decimal.
    import math
    if len(values) < 3 or any(value <= ZERO for value in values):
        return None
    return [Decimal(str(math.log(float(values[i] / values[i - 1])))) for i in range(1, len(values))]


def realized_volatility_pct(values: list[Decimal], periods_per_year: int = 365) -> Decimal | None:
    returns = log_returns(values)
    if returns is None or len(returns) < 2:
        return None
    mean = sum(returns, ZERO) / Decimal(len(returns))
    variance = sum(((item - mean) ** 2 for item in returns), ZERO) / Decimal(len(returns) - 1)
    return variance.sqrt() * Decimal(str(sqrt(periods_per_year))) * HUNDRED


def aligned_returns(left: list[tuple[str, Decimal]], right: list[tuple[str, Decimal]]) -> tuple[list[Decimal], list[Decimal]] | None:
    left_map, right_map = dict(left), dict(right)
    keys = sorted(set(left_map) & set(right_map))
    if len(keys) < 3:
        return None
    left_values, right_values = [left_map[key] for key in keys], [right_map[key] for key in keys]
    if any(item <= ZERO for item in left_values + right_values):
        return None
    return ([left_values[i] / left_values[i - 1] - 1 for i in range(1, len(keys))],
            [right_values[i] / right_values[i - 1] - 1 for i in range(1, len(keys))])


def correlation_and_beta(left_returns: list[Decimal] | None, benchmark_returns: list[Decimal] | None) -> tuple[Decimal | None, Decimal | None]:
    if left_returns is None or benchmark_returns is None or len(left_returns) < 2 or len(left_returns) != len(benchmark_returns):
        return None, None
    left_mean = sum(left_returns, ZERO) / Decimal(len(left_returns))
    right_mean = sum(benchmark_returns, ZERO) / Decimal(len(benchmark_returns))
    denominator = Decimal(len(left_returns) - 1)
    covariance = sum(((a - left_mean) * (b - right_mean) for a, b in zip(left_returns, benchmark_returns)), ZERO) / denominator
    left_variance = sum(((a - left_mean) ** 2 for a in left_returns), ZERO) / denominator
    right_variance = sum(((b - right_mean) ** 2 for b in benchmark_returns), ZERO) / denominator
    if left_variance == ZERO or right_variance == ZERO:
        return None, None
    return covariance / (left_variance * right_variance).sqrt(), covariance / right_variance


def cost_basis_summary(items: list[dict], fills_available: bool) -> dict[str, Decimal | int | None]:
    if not fills_available:
        return {"pnl_scope_value": None, "cost_basis_coverage_pct": None, "cost_basis_value": None, "unrealized_pnl": None,
                "unrealized_pnl_pct": None, "profitable_count": None, "losing_count": None, "flat_count": None,
                "largest_known_gain": None, "largest_known_loss": None}
    valued = [item for item in items if item.get("value") is not None]
    known = [item for item in valued if item.get("cost_basis") is not None]
    scope = sum((Decimal(item["value"]) for item in known), ZERO)
    total = sum((Decimal(item["value"]) for item in valued), ZERO)
    cost = sum((Decimal(item["cost_basis"]) for item in known), ZERO)
    pnl = sum((Decimal(item["unrealized_pnl"]) for item in known), ZERO)
    pnls = [Decimal(item["unrealized_pnl"]) for item in known]
    return {"pnl_scope_value": scope, "cost_basis_coverage_pct": scope / total * HUNDRED if total else None,
            "cost_basis_value": cost if known else None, "unrealized_pnl": pnl if known else None,
            "unrealized_pnl_pct": pnl / cost * HUNDRED if cost else None,
            "profitable_count": sum(item > ZERO for item in pnls), "losing_count": sum(item < ZERO for item in pnls), "flat_count": sum(item == ZERO for item in pnls),
            "largest_known_gain": max((item for item in known if Decimal(item["unrealized_pnl"]) > ZERO), key=lambda item: Decimal(item["unrealized_pnl"]), default=None),
            "largest_known_loss": min((item for item in known if Decimal(item["unrealized_pnl"]) < ZERO), key=lambda item: Decimal(item["unrealized_pnl"]), default=None)}
