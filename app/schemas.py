from pydantic import BaseModel, Field


class Holding(BaseModel):
    symbol: str
    name: str
    quantity: str
    price: str | None = Field(description="USD price; null when Coinbase has no SYMBOL-USD product (unknown, not zero).")
    value: str | None = Field(description="quantity * price; null when price is null.")
    allocation_pct: str | None = Field(description="Percent of total valued holdings; null when value is null or the portfolio total is zero.")
    cost_basis: str | None = Field(default=None, description="Average-cost basis in USD, derived from fills history. Present only when include_cost_basis=true. Null = unknown (no fills history for this asset), not zero.")
    unrealized_pnl: str | None = Field(default=None, description="value - cost_basis. Same availability as cost_basis.")
    unrealized_pnl_pct: str | None = Field(default=None, description="unrealized_pnl / cost_basis * 100. Null if cost_basis is null or zero.")
    market_cap_rank: int | None = Field(default=None, description="CoinGecko market cap rank. Present only when include_market_context=true. Null = not listed on CoinGecko, not unranked-zero.")
    market_cap: str | None = Field(default=None, description="USD market cap. Same availability as market_cap_rank.")


class HoldingsResponse(BaseModel):
    currency: str
    holdings: list[Holding]
    retrieved_at: str
    cost_basis_available: bool | None = Field(default=None, description="Null when include_cost_basis=false. False when the fills lookup failed upstream (holdings' cost_basis fields are then all null). True otherwise.")
    market_context_available: bool | None = Field(default=None, description="Null when include_market_context=false. False when CoinGecko failed upstream. True otherwise.")


class PortfolioSummaryResponse(BaseModel):
    currency: str
    total_value: str
    crypto_value: str
    cash_value: str
    holding_count: int
    top_holdings: list[Holding]
    retrieved_at: str
    holdings_with_unknown_price: int = Field(description="Count of held assets with no priceable Coinbase product. 0 when all are priced.")
    cost_basis_available: bool | None = Field(default=None, description="Null when include_cost_basis=false. False when the fills lookup failed upstream. True otherwise.")
    cost_basis_value: str | None = Field(default=None, description="Sum of known cost bases across holdings. Null when include_cost_basis=false or no holding has a known cost basis.")
    unrealized_pnl: str | None = Field(default=None, description="Sum of unrealized P&L across holdings with a known cost basis. Same availability as cost_basis_value.")
    unrealized_pnl_pct: str | None = Field(default=None, description="unrealized_pnl / cost_basis_value * 100. Null if cost_basis_value is null or zero.")
    holdings_with_unknown_cost_basis: int | None = Field(default=None, description="Null when include_cost_basis=false. Count of holdings with unknown cost basis otherwise (may be 0).")
    market_context_available: bool | None = Field(default=None, description="Null when include_market_context=false. False when CoinGecko failed upstream. True otherwise.")
    concentration: dict[str, str | None] = Field(default_factory=dict, description="Current valued-holding concentration metrics; unavailable metrics are null.")
    pnl_scope_value: str | None = Field(default=None, description="Current value covered by fully fill-traceable cost basis. Null when fills retrieval failed or was not requested.")
    cost_basis_coverage_pct: str | None = Field(default=None, description="Percent of priced current value inside the known P&L scope.")
    profitable_count: int | None = None
    losing_count: int | None = None
    flat_count: int | None = None
    largest_known_gain: Holding | None = None
    largest_known_loss: Holding | None = None


class MarketPriceResponse(BaseModel):
    product_id: str
    price: str
    currency: str
    change_24h_pct: str | None = Field(description="24h percent change. Null when candle history is unavailable, not zero change.")
    change_7d_pct: str | None = Field(description="7d percent change. Null when candle history is unavailable, not zero change.")
    bid: str | None = Field(description="Best bid. Null when the order book is empty or unavailable.")
    ask: str | None = Field(description="Best ask. Null when the order book is empty or unavailable.")
    spread_pct: str | None = Field(description="(ask - bid) / ask * 100. Null when bid or ask is null.")
    market_cap_rank: int | None = Field(description="CoinGecko market cap rank. Null when CoinGecko does not list this asset.")
    market_cap: str | None = Field(description="USD market cap. Null when CoinGecko does not list this asset.")
    ath: str | None = Field(description="All-time-high USD price per CoinGecko. Null when CoinGecko does not list this asset.")
    ath_change_pct: str | None = Field(description="Percent change from ath to the current price. Null when ath is null.")
    market_context_available: bool = Field(description="False when optional CoinGecko context is unavailable; live Coinbase price is still valid.")
    retrieved_at: str


class HistoryPoint(BaseModel):
    timestamp: str
    total_value: str
    crypto_value: str | None = None
    cash_value: str | None = None
    priced_value: str | None = None
    unknown_price_count: int | None = None


class PortfolioHistoryResponse(BaseModel):
    currency: str = "USD"
    period: str
    granularity: str
    points: list[HistoryPoint]
    point_count: int
    available: bool
    coverage_start: str | None
    coverage_end: str | None
    change_value: str | None = Field(description="Change in portfolio value, not cash-flow-adjusted investment return.")
    change_pct: str | None = Field(description="Percent change in portfolio value, not investment return.")
    high_value: str | None
    low_value: str | None
    retrieved_at: str


class MarketCandle(BaseModel):
    timestamp: str
    open: str
    high: str
    low: str
    close: str
    volume: str


class MarketHistoryResponse(BaseModel):
    product_id: str
    currency: str = "USD"
    period: str
    granularity: str
    candles: list[MarketCandle]
    point_count: int
    change_value: str | None
    change_pct: str | None
    high: str | None
    low: str | None
    max_drawdown_pct: str | None
    realized_volatility_pct: str | None = Field(description="Annualized sample volatility of log returns; null with fewer than two returns.")
    retrieved_at: str


class MarketContextResponse(BaseModel):
    product_id: str
    currency: str = "USD"
    market_context_available: bool
    coin_id: str | None
    market_cap_rank: int | None
    market_cap: str | None
    fully_diluted_valuation: str | None
    circulating_supply: str | None
    total_supply: str | None
    max_supply: str | None
    ath: str | None
    ath_change_pct: str | None
    total_volume: str | None
    volume_market_cap_ratio: str | None
    change_24h_pct: str | None
    change_7d_pct: str | None
    change_30d_pct: str | None
    change_1y_pct: str | None
    retrieved_at: str


class PortfolioAnalyticsResponse(BaseModel):
    currency: str = "USD"
    period: str
    benchmark: str
    current_total_value: str
    history_available: bool
    coverage_start: str | None
    coverage_end: str | None
    value_change: str | None = Field(description="Portfolio value change, not investment return.")
    value_change_pct: str | None
    high_value: str | None
    low_value: str | None
    max_drawdown_pct: str | None
    realized_volatility_pct: str | None
    concentration: dict[str, str | None]
    pnl_scope_value: str | None
    cost_basis_coverage_pct: str | None
    unrealized_pnl: str | None
    benchmark_change_pct: str | None
    correlation: str | None
    beta: str | None
    data_quality: dict[str, bool | int | None]
    retrieved_at: str


class ErrorDetail(BaseModel):
    error: str
    message: str | None = None
