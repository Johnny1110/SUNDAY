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

    # trading / strategy (milestone 1.0 — single symbol, hardcoded)
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    ema_fast: int = 20
    ema_slow: int = 50
    target_notional_usd: float = 500.0  # size to open per entry (within max_position_usd)
    leverage: int = 3

    # deterministic risk envelope (hard caps; NOT the LLM's job)
    max_position_usd: float = 2000.0
    max_total_exposure_usd: float = 4000.0
    max_leverage: int = 3
    stop_pct: float = 0.02


settings = Settings()
