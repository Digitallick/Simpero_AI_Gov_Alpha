from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    alembic_database_url: str
    clerk_secret_key: str
    clerk_jwt_audience: str
    clerk_jwks_url: str
    valkey_url: str
    environment: str = "development"
    cors_allowed_origins: str = "http://localhost:3000"
    # Clerk org id of the Simpero platform org (internal staff). "" => the
    # platform-admin surface fails closed (denies everyone) until set.
    simpero_platform_org_id: str = ""
    # Frontend base URL — builds Clerk invitation redirect_url(s) for both the
    # admin seed invite (/admin/sign-up) and product-user invites (/sign-up).
    app_base_url: str = "http://localhost:3000"
    # D3 downgrade-sync grace window (see _ensure_admin_provisioned): a row
    # this app just promoted/reactivated is exempt from the downgrade-only
    # sync for this many seconds, covering the window before a caller's
    # already-issued session JWT refreshes to reflect the new org_role.
    admin_role_sync_grace_seconds: int = 120

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    # lru_cache: Settings are immutable at runtime; re-reading .env on every call is wasteful
    return Settings()  # type: ignore[call-arg]  # pydantic-settings populates fields from env at runtime
