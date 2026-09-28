from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", env_file=".env", extra="ignore", frozen=True)

    database_url: str
    session_secret: str
    admin_password_hash: str
    offline_after_seconds: int = 90
    timezone: str = "Asia/Seoul"
    session_max_age_seconds: int = 7 * 24 * 3600
    public_url: str = "http://localhost:8020"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_topic_id: str | None = None
