from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    portfolio_api_key: SecretStr
    coinbase_api_key_id: str
    coinbase_api_key_secret: SecretStr
    coinbase_api_base_url: str = "https://api.coinbase.com"
    coinbase_timeout_seconds: float = 10.0
    portfolio_history_db_path: str = "data/portfolio_history.db"
    coingecko_market_cache_ttl_seconds: float = 55.0
    coingecko_metadata_cache_ttl_seconds: float = 3600.0
    public_base_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

