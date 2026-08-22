import os

os.environ.update({"PORTFOLIO_API_KEY": "test-key", "COINBASE_API_KEY_ID": "test-key-id", "COINBASE_API_KEY_SECRET": "test-secret"})

from app.coinbase.client import CoinbaseClient
from app.config import get_settings


def _client_with_pages(monkeypatch, pages):
    client = CoinbaseClient(get_settings())
    calls = []

    async def fake_get(path, *, authenticated):
        calls.append(path)
        return pages.pop(0)

    monkeypatch.setattr(client, "_get", fake_get)
    return client, calls


async def test_list_fills_walks_multiple_pages(monkeypatch) -> None:
    pages = [
        {"fills": [{"id": "1"}], "cursor": "abc"},
        {"fills": [{"id": "2"}], "cursor": ""},
    ]
    client, calls = _client_with_pages(monkeypatch, pages)
    fills = await client.list_fills()
    assert [f["id"] for f in fills] == ["1", "2"]
    assert len(calls) == 2
    assert "cursor=abc" in calls[1]


async def test_list_fills_stops_on_empty_cursor(monkeypatch) -> None:
    pages = [{"fills": [{"id": "1"}], "cursor": ""}]
    client, calls = _client_with_pages(monkeypatch, pages)
    fills = await client.list_fills()
    assert len(fills) == 1
    assert len(calls) == 1


async def test_list_fills_stops_on_empty_batch(monkeypatch) -> None:
    pages = [{"fills": [], "cursor": "abc"}]
    client, calls = _client_with_pages(monkeypatch, pages)
    fills = await client.list_fills()
    assert fills == []
    assert len(calls) == 1
