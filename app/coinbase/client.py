from urllib.parse import urlparse

import httpx

from app.coinbase.auth import build_rest_jwt
from app.config import Settings


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
                method="GET", host=self._host, path=path,
            )
        try:
            response = await self._client.get(f"{self._base_url}{path}", headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise CoinbaseError() from exc
        except httpx.HTTPStatusError as exc:
            raise CoinbaseError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError) as exc:
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

