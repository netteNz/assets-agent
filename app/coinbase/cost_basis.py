from datetime import datetime
from decimal import Decimal

from app.coinbase.normalize import ZERO

USD_EQUIVALENT_QUOTES = ("USD", "USDC", "USDT")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _base_currency(product_id: str) -> str | None:
    base, _, quote = product_id.rpartition("-")
    return base if quote in USD_EQUIVALENT_QUOTES else None


def compute_average_cost_basis(fills: list[dict]) -> dict[str, dict]:
    """Moving-average cost basis per base currency, derived from historical fills.

    Returns {base_currency: {"quantity": Decimal, "avg_cost": Decimal | None, "realized_pnl": Decimal}}.
    SELL fills reduce quantity without changing avg_cost (standard average-cost accounting);
    quantity is clamped at zero rather than going negative if fills alone don't explain a balance
    (e.g. a transfer into the account that isn't reflected as a fill).
    """
    positions: dict[str, dict] = {}
    for fill in sorted(fills, key=lambda f: _parse_time(f["trade_time"])):
        base = _base_currency(fill.get("product_id", ""))
        if base is None:
            continue
        position = positions.setdefault(base, {"quantity": ZERO, "avg_cost": None, "realized_pnl": ZERO})

        size = Decimal(fill["size"])
        price = Decimal(fill["price"])
        commission = Decimal(fill.get("commission", "0"))

        if fill.get("side") == "BUY":
            cost = size * price + commission
            existing_cost = (position["avg_cost"] or ZERO) * position["quantity"]
            new_quantity = position["quantity"] + size
            position["avg_cost"] = (existing_cost + cost) / new_quantity if new_quantity > ZERO else None
            position["quantity"] = new_quantity
        elif fill.get("side") == "SELL":
            sold = min(size, position["quantity"])
            proceeds = sold * price - commission
            cost_removed = (position["avg_cost"] or ZERO) * sold
            position["realized_pnl"] += proceeds - cost_removed
            position["quantity"] = max(position["quantity"] - size, ZERO)
            if position["quantity"] == ZERO:
                position["avg_cost"] = None

    return positions
