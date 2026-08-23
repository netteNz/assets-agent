from decimal import Decimal

from app.portfolio_analytics import aligned_returns, concentration, correlation_and_beta, cost_basis_summary, max_drawdown_pct, realized_volatility_pct, value_change


def test_concentration_and_hhi() -> None:
    result = concentration([{"symbol": "BTC", "value": Decimal("60")}, {"symbol": "ETH", "value": Decimal("40")}])
    assert result["largest_position_pct"] == Decimal("60")
    assert result["top_2_pct"] == Decimal("100")
    assert result["hhi"] == Decimal("5200")


def test_value_change_drawdown_and_insufficient_volatility() -> None:
    values = [Decimal("100"), Decimal("120"), Decimal("90")]
    assert value_change(values)["change_value"] == Decimal("-10")
    assert max_drawdown_pct(values) == Decimal("-25")
    assert realized_volatility_pct([Decimal("100"), Decimal("101")]) is None


def test_alignment_and_zero_variance_are_unknown() -> None:
    aligned = aligned_returns([("2025-01-01", Decimal("100")), ("2025-01-02", Decimal("110")), ("2025-01-03", Decimal("121"))], [("2025-01-01", Decimal("10")), ("2025-01-02", Decimal("10")), ("2025-01-03", Decimal("10"))])
    assert aligned is not None
    assert correlation_and_beta(*aligned) == (None, None)


def test_cost_scope_is_zero_when_fills_succeed_but_nothing_qualifies() -> None:
    summary = cost_basis_summary([{"value": Decimal("10"), "cost_basis": None}], True)
    assert summary["pnl_scope_value"] == Decimal("0")
    assert summary["cost_basis_coverage_pct"] == Decimal("0")
