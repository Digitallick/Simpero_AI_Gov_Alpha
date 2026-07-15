from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    alembic_database_url: str
    clerk_secret_key: str
    clerk_jwt_audience: str
    clerk_jwks_url: str
    environment: str = "development"
    cors_allowed_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    # lru_cache: Settings are immutable at runtime; re-reading .env on every call is wasteful
    return Settings()  # type: ignore[call-arg]  # pydantic-settings populates fields from env at runtime
