import time
import logging
from typing import ClassVar

import httpx

logger = logging.getLogger(__name__)


class CoinGeckoError(Exception):
    def __init__(self, status_code: int | None = None):
        self.status_code = status_code


class CoinGeckoClient:
    """Public CoinGecko market-data client, batched and cached to respect the free tier's tight rate limit."""

    BASE_URL = "https://api.coingecko.com/api/v3"
    CACHE_TTL_SECONDS = 55
    MAX_SYMBOLS_PER_REQUEST = 50

    _cache: dict[tuple[str, ...], tuple[float, dict[str, dict]]] = {}
    _context_cache: ClassVar[dict[str, tuple[float, dict]]] = {}
    # Explicit mapping prevents symbol collisions from leaking into the V2 context API.
    COINBASE_SYMBOL_TO_ID: ClassVar[dict[str, str]] = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "ADA": "cardano", "DOGE": "dogecoin",
        "XRP": "ripple", "AVAX": "avalanche-2", "LINK": "chainlink", "LTC": "litecoin", "BCH": "bitcoin-cash",
        "MATIC": "matic-network", "DOT": "polkadot", "ATOM": "cosmos", "UNI": "uniswap", "AAVE": "aave",
        "SHIB": "shiba-inu", "USDC": "usd-coin", "USDT": "tether", "XLM": "stellar", "ALGO": "algorand",
    }

    def __init__(self, *, timeout_seconds: float = 10.0, client: httpx.AsyncClient | None = None, market_cache_ttl_seconds: float | None = None, metadata_cache_ttl_seconds: float = 3600.0):
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self._market_cache_ttl_seconds = market_cache_ttl_seconds if market_cache_ttl_seconds is not None else self.CACHE_TTL_SECONDS
        self._metadata_cache_ttl_seconds = metadata_cache_ttl_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_markets(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch market context for the given ticker symbols in a single batched call.

        Returns {SYMBOL: {market_cap_rank, market_cap, ath, ath_change_percentage,
        circulating_supply, total_supply, max_supply}}. Symbols CoinGecko doesn't
        recognize are simply absent from the result, not an error. When a symbol
        collides across multiple listed coins, the one with the best (lowest) market
        cap rank wins.
        """
        cache_key = tuple(sorted({s.lower() for s in symbols if s}))
        if not cache_key:
            return {}
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self._market_cache_ttl_seconds:
            logger.info("coingecko_cache_hit kind=markets")
            return cached[1]

        coins: list[dict] = []
        for i in range(0, len(cache_key), self.MAX_SYMBOLS_PER_REQUEST):
            coins.extend(await self._fetch_chunk(cache_key[i:i + self.MAX_SYMBOLS_PER_REQUEST]))

        result: dict[str, dict] = {}
        for coin in coins:
            symbol = coin.get("symbol", "").upper()
            rank = coin.get("market_cap_rank")
            existing = result.get(symbol)
            if existing is not None and existing.get("market_cap_rank") is not None and (rank is None or existing["market_cap_rank"] <= rank):
                continue
            result[symbol] = {
                "market_cap_rank": rank,
                "market_cap": coin.get("market_cap"),
                "ath": coin.get("ath"),
                "ath_change_percentage": coin.get("ath_change_percentage"),
                "circulating_supply": coin.get("circulating_supply"),
                "total_supply": coin.get("total_supply"),
                "max_supply": coin.get("max_supply"),
            }

        self._cache[cache_key] = (time.monotonic(), result)
        return result

    async def get_context(self, symbol: str) -> tuple[str | None, dict | None]:
        """Fetch a single, explicitly mapped asset context; unmapped is not an error."""
        coin_id = self.COINBASE_SYMBOL_TO_ID.get(symbol.upper())
        if not coin_id:
            return None, None
        cached = self._context_cache.get(coin_id)
        if cached and time.monotonic() - cached[0] < self._metadata_cache_ttl_seconds:
            logger.info("coingecko_cache_hit kind=context coin_id=%s", coin_id)
            return coin_id, cached[1]
        try:
            started = time.monotonic()
            response = await self._client.get(
                f"{self.BASE_URL}/coins/{coin_id}",
                params={"localization": "false", "tickers": "false", "market_data": "true", "community_data": "false", "developer_data": "false", "sparkline": "false"},
            )
            response.raise_for_status()
            logger.info("coingecko_request endpoint=context latency_ms=%d", (time.monotonic() - started) * 1000)
            payload = response.json()
            market = payload.get("market_data", {})
            usd = lambda key: market.get(key, {}).get("usd") if isinstance(market.get(key), dict) else None
            changes = market.get("price_change_percentage_24h_in_currency", {})
            context = {"market_cap_rank": payload.get("market_cap_rank"), "market_cap": usd("market_cap"),
                       "fully_diluted_valuation": usd("fully_diluted_valuation"), "circulating_supply": market.get("circulating_supply"),
                       "total_supply": market.get("total_supply"), "max_supply": market.get("max_supply"), "ath": usd("ath"),
                       "ath_change_pct": (market.get("ath_change_percentage", {}) or {}).get("usd"), "total_volume": usd("total_volume"),
                       "change_24h_pct": changes.get("usd"), "change_7d_pct": market.get("price_change_percentage_7d"),
                       "change_30d_pct": market.get("price_change_percentage_30d"), "change_1y_pct": market.get("price_change_percentage_1y")}
            self._context_cache[coin_id] = (time.monotonic(), context)
            return coin_id, context
        except httpx.TimeoutException as exc:
            logger.warning("coingecko_request_failed endpoint=context reason=timeout")
            raise CoinGeckoError() from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("coingecko_request_failed endpoint=context status=%s", exc.response.status_code)
            raise CoinGeckoError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("coingecko_request_failed endpoint=context reason=transport")
            raise CoinGeckoError() from exc

    async def _fetch_chunk(self, symbols: tuple[str, ...]) -> list[dict]:
        try:
            started = time.monotonic()
            response = await self._client.get(
                f"{self.BASE_URL}/coins/markets",
                params={"vs_currency": "usd", "symbols": ",".join(symbols), "order": "market_cap_desc"},
            )
            response.raise_for_status()
            logger.info("coingecko_request endpoint=markets latency_ms=%d", (time.monotonic() - started) * 1000)
            return response.json()
        except httpx.TimeoutException as exc:
            logger.warning("coingecko_request_failed endpoint=markets reason=timeout")
            raise CoinGeckoError() from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("coingecko_request_failed endpoint=markets status=%s", exc.response.status_code)
            raise CoinGeckoError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("coingecko_request_failed endpoint=markets reason=transport")
            raise CoinGeckoError() from exc
