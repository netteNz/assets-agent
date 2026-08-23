from urllib.parse import urlparse
import logging
import time

import httpx

from app.coinbase.auth import build_rest_jwt
from app.config import Settings

logger = logging.getLogger(__name__)


class CoinbaseError(Exception):
    def __init__(self, status_code: int | None = None):
        self.status_code = status_code


class CoinbaseClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.coinbase_timeout_seconds)
        self._owns_client = client is None
        parsed = urlparse(settings.coinbase_api_base_url)
        self._base_url = settings.coinbase_api_base_url.rstrip("/")
        self._host = parsed.netloc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, *, authenticated: bool) -> dict:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + build_rest_jwt(
                key_id=self.settings.coinbase_api_key_id,
                private_key_pem=self.settings.coinbase_api_key_secret.get_secret_value(),
                method="GET", host=self._host, path=path.split("?", 1)[0],
            )
        try:
            started = time.monotonic()
            response = await self._client.get(f"{self._base_url}{path}", headers=headers)
            response.raise_for_status()
            logger.info("coinbase_request endpoint=%s status=%s latency_ms=%d", path.split("?", 1)[0], response.status_code, (time.monotonic() - started) * 1000)
            return response.json()
        except httpx.TimeoutException as exc:
            logger.warning("coinbase_request_failed endpoint=%s reason=timeout", path.split("?", 1)[0])
            raise CoinbaseError() from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("coinbase_request_failed endpoint=%s status=%s", path.split("?", 1)[0], exc.response.status_code)
            raise CoinbaseError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("coinbase_request_failed endpoint=%s reason=transport", path.split("?", 1)[0])
            raise CoinbaseError() from exc

    async def list_accounts(self) -> list[dict]:
        accounts: list[dict] = []
        path = "/api/v3/brokerage/accounts"
        while path:
            payload = await self._get(path, authenticated=True)
            accounts.extend(payload.get("accounts", []))
            cursor = payload.get("cursor")
            path = f"/api/v3/brokerage/accounts?cursor={cursor}" if cursor and payload.get("has_next") else ""
        return accounts

    async def get_product(self, product_id: str) -> dict:
        return await self._get(f"/api/v3/brokerage/market/products/{product_id}", authenticated=False)

    async def list_fills(self) -> list[dict]:
        fills: list[dict] = []
        path = "/api/v3/brokerage/orders/historical/fills?limit=100"
        for _ in range(500):
            payload = await self._get(path, authenticated=True)
            batch = payload.get("fills", [])
            if not batch:
                break
            fills.extend(batch)
            cursor = payload.get("cursor")
            if not cursor:
                break
            path = f"/api/v3/brokerage/orders/historical/fills?limit=100&cursor={cursor}"
        return fills

    async def get_candles(self, product_id: str, *, granularity: str, start: str, end: str) -> list[dict]:
        payload = await self._get(
            f"/api/v3/brokerage/market/products/{product_id}/candles?start={start}&end={end}&granularity={granularity}",
            authenticated=False,
        )
        return payload.get("candles", [])

    async def get_best_bid_ask(self, product_ids: list[str]) -> list[dict]:
        query = "&".join(f"product_ids={pid}" for pid in product_ids)
        payload = await self._get(f"/api/v3/brokerage/best_bid_ask?{query}", authenticated=True)
        return payload.get("pricebooks", [])

