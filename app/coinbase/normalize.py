from decimal import Decimal, ROUND_HALF_UP


ZERO = Decimal("0")


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def number(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def account_quantity(account: dict) -> Decimal:
    return Decimal(account.get("available_balance", {}).get("value", "0"))


def account_symbol(account: dict) -> str:
    return account.get("currency", "").upper()

