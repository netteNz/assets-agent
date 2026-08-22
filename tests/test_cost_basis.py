from decimal import Decimal

from app.coinbase.cost_basis import compute_average_cost_basis


def _fill(product_id, side, size, price, trade_time, commission="0"):
    return {"product_id": product_id, "side": side, "size": size, "price": price, "trade_time": trade_time, "commission": commission}


def test_average_cost_blends_across_buys() -> None:
    fills = [
        _fill("BTC-USD", "BUY", "0.5", "20000", "2024-01-01T00:00:00Z"),
        _fill("BTC-USD", "BUY", "0.5", "25000", "2024-02-01T00:00:00Z"),
    ]
    positions = compute_average_cost_basis(fills)
    assert positions["BTC"]["avg_cost"] == Decimal("22500")
    assert positions["BTC"]["quantity"] == Decimal("1.0")


def test_sell_does_not_change_avg_cost_and_tracks_realized_pnl() -> None:
    fills = [
        _fill("BTC-USD", "BUY", "1.0", "100", "2024-01-01T00:00:00Z"),
        _fill("BTC-USD", "SELL", "0.4", "150", "2024-01-02T00:00:00Z"),
    ]
    positions = compute_average_cost_basis(fills)
    assert positions["BTC"]["avg_cost"] == Decimal("100")
    assert positions["BTC"]["quantity"] == Decimal("0.6")
    assert positions["BTC"]["realized_pnl"] == Decimal("20")


def test_fully_sold_position_resets_avg_cost() -> None:
    fills = [
        _fill("ETH-USD", "BUY", "1.0", "1000", "2024-01-01T00:00:00Z"),
        _fill("ETH-USD", "SELL", "1.0", "1200", "2024-01-02T00:00:00Z"),
    ]
    positions = compute_average_cost_basis(fills)
    assert positions["ETH"]["quantity"] == Decimal("0")
    assert positions["ETH"]["avg_cost"] is None


def test_crypto_quoted_pairs_are_skipped() -> None:
    fills = [_fill("BTC-ETH", "BUY", "1.0", "10", "2024-01-01T00:00:00Z")]
    assert compute_average_cost_basis(fills) == {}


def test_stablecoin_quoted_pairs_are_treated_as_usd_equivalent() -> None:
    fills = [
        _fill("BTC-USDC", "BUY", "0.5", "20000", "2024-01-01T00:00:00Z"),
        _fill("BTC-USDT", "BUY", "0.5", "22000", "2024-01-02T00:00:00Z"),
    ]
    positions = compute_average_cost_basis(fills)
    assert positions["BTC"]["avg_cost"] == Decimal("21000")


def test_oversell_clamps_quantity_at_zero() -> None:
    fills = [
        _fill("BTC-USD", "BUY", "1.0", "100", "2024-01-01T00:00:00Z"),
        _fill("BTC-USD", "SELL", "5.0", "150", "2024-01-02T00:00:00Z"),
    ]
    positions = compute_average_cost_basis(fills)
    assert positions["BTC"]["quantity"] == Decimal("0")
