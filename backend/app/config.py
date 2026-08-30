from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # UniFi. Note the API key is root-equivalent on the gateway: the settings
    # endpoint returns the device SSH password in plaintext to any key holder.
    # Never log it, never return it to a client, never put it in a diagnostic.
    unifi_host: str = "https://192.168.1.1"
    unifi_api_key: str = Field(repr=False)
    unifi_site: str = "default"
    unifi_verify_tls: bool = False

    database_url: str = "postgresql+asyncpg://unifidns:unifidns@db:5432/unifidns"

    # Apex domains used to synthesise zones. UniFi stores records flat with no
    # zone concept, so grouping is ours to impose.
    default_apexes: list[str] = Field(default_factory=list)

    # OIDC (Authentik et al). Auth is deliberately separate from the UniFi key:
    # OIDC says who the human is, the API key is what the app uses on the gateway.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = Field(default=None, repr=False)
    oidc_redirect_url: str | None = None

    # Trusted forward-auth header, for deployments already behind an SSO proxy.
    trusted_user_header: str | None = None

    session_secret: str = Field(default="change-me-in-production", repr=False)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
