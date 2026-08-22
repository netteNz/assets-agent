import time

import httpx


class CoinGeckoError(Exception):
    def __init__(self, status_code: int | None = None):
        self.status_code = status_code


class CoinGeckoClient:
    """Public CoinGecko market-data client, batched and cached to respect the free tier's tight rate limit."""

    BASE_URL = "https://api.coingecko.com/api/v3"
    CACHE_TTL_SECONDS = 55
    MAX_SYMBOLS_PER_REQUEST = 50

    _cache: dict[tuple[str, ...], tuple[float, dict[str, dict]]] = {}

    def __init__(self, *, timeout_seconds: float = 10.0, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

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
        if cached and time.monotonic() - cached[0] < self.CACHE_TTL_SECONDS:
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

    async def _fetch_chunk(self, symbols: tuple[str, ...]) -> list[dict]:
        try:
            response = await self._client.get(
                f"{self.BASE_URL}/coins/markets",
                params={"vs_currency": "usd", "symbols": ",".join(symbols), "order": "market_cap_desc"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise CoinGeckoError() from exc
        except httpx.HTTPStatusError as exc:
            raise CoinGeckoError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CoinGeckoError() from exc
