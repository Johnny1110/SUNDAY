from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://root:root@localhost:5432/sunday"
    redis_url: str = "redis://localhost:6379/0"
    evva_webhook_url: str = "http://127.0.0.1:8888/api/swarm/sunday/event"

    binance_testnet_key: str = ""
    binance_testnet_secret: str = ""

    sunday_host: str = "127.0.0.1"
    sunday_port: int = 7777

    # Comma-separated USDⓈ-M perp symbols the engine trades (the basket, M1.2).
    sunday_symbols: str = "BTCUSDT"


settings = Settings()
