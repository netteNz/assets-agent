import httpx
import pytest

from app.coingecko.client import CoinGeckoClient, CoinGeckoError


@pytest.fixture(autouse=True)
def _clear_class_level_cache():
    CoinGeckoClient._cache.clear()
    yield
    CoinGeckoClient._cache.clear()


def _transport(coins):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=coins)
    return httpx.MockTransport(handler)


async def test_get_markets_maps_by_uppercase_symbol():
    coins = [{"symbol": "btc", "market_cap_rank": 1, "market_cap": 100, "ath": 1, "ath_change_percentage": -1, "circulating_supply": 1, "total_supply": 1, "max_supply": 1}]
    client = CoinGeckoClient(client=httpx.AsyncClient(transport=_transport(coins)))
    result = await client.get_markets(["btc"])
    assert "BTC" in result
    assert result["BTC"]["market_cap_rank"] == 1
    await client.aclose()


async def test_get_markets_empty_symbols_short_circuits():
    client = CoinGeckoClient(client=httpx.AsyncClient(transport=_transport([])))
    assert await client.get_markets([]) == {}
    await client.aclose()


async def test_get_markets_collision_keeps_best_rank():
    coins = [
        {"symbol": "grt", "market_cap_rank": 500, "market_cap": 1},
        {"symbol": "grt", "market_cap_rank": 179, "market_cap": 2},
    ]
    client = CoinGeckoClient(client=httpx.AsyncClient(transport=_transport(coins)))
    result = await client.get_markets(["grt"])
    assert result["GRT"]["market_cap_rank"] == 179
    await client.aclose()


async def test_get_markets_caches_within_ttl():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[{"symbol": "btc", "market_cap_rank": 1, "market_cap": 100}])

    client = CoinGeckoClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.get_markets(["btc"])
    await client.get_markets(["btc"])
    assert len(calls) == 1
    await client.aclose()


async def test_get_markets_chunks_over_fifty_symbols():
    requested_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        symbols = request.url.params["symbols"].split(",")
        requested_sizes.append(len(symbols))
        return httpx.Response(200, json=[{"symbol": s, "market_cap_rank": i, "market_cap": 1} for i, s in enumerate(symbols)])

    client = CoinGeckoClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    symbols = [f"coin{i}" for i in range(65)]
    result = await client.get_markets(symbols)
    assert requested_sizes == [50, 15]
    assert len(result) == 65
    await client.aclose()


async def test_rate_limit_raises_coingecko_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"status": {"error_code": 429}})

    client = CoinGeckoClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(CoinGeckoError) as exc_info:
        await client.get_markets(["xyz-not-cached-elsewhere"])
    assert exc_info.value.status_code == 429
    await client.aclose()
