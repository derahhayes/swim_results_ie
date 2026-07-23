from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No defaults on either DB URL, deliberately: pydantic-settings raises a
    # ValidationError at Settings() construction time (i.e. at import time,
    # before the app can serve a single request) if either is unset. There
    # is no "falls back to localhost" failure mode here - a missing
    # DATABASE_URL in production fails loudly at startup, not silently at
    # the first query.
    database_url: str
    database_url_direct: str

    cors_origins: str = "http://localhost:5173"
    # Starlette's CORSMiddleware `allow_origins` only does exact string
    # matches, which can't express "any Lovable preview subdomain" - that
    # needs `allow_origin_regex` instead. Fully anchored (^...$) regardless
    # of whether the installed Starlette uses .match() or .fullmatch()
    # internally, so "https://foo.lovable.app.evil.com" can't sneak past a
    # regex that was only anchored at the start.
    cors_origin_regex: str = r"^https://[\w-]+\.lovable\.app$"

    storage_dir: str = "./storage"

    # dev | production. Gates the /docs toggle (see docs_public) and,
    # below, which STORAGE_BACKEND is acceptable.
    environment: str = "development"
    # Lovable and we both use /docs against the deployed API, so it stays
    # public by default even in production; set false to lock it down.
    docs_public: bool = True

    # No default, same "fail loudly, not silently" reasoning as the DB
    # URLs - a JWT secret that silently defaulted to something guessable
    # would be a real vulnerability, not a convenience.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # Comma-separated. Checked idempotently against existing `users` rows
    # on every app startup (see app.auth.bootstrap) - this is the only way
    # to grant the first admin, since every admin-granting endpoint itself
    # requires an existing admin.
    admin_emails: str = ""

    # local | r2. local (LocalDirStorage) is fine for dev/test, where the
    # filesystem doesn't need to survive a redeploy; production must use
    # r2 (Cloudflare R2, S3-compatible) - see the validator below and
    # KNOWN_ISSUES.md's (resolved) ephemeral-storage entry.
    storage_backend: str = "local"
    r2_account_id: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket: Optional[str] = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def admin_emails_list(self) -> list[str]:
        return [email.strip().lower() for email in self.admin_emails.split(",") if email.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def docs_enabled(self) -> bool:
        return self.docs_public or not self.is_production

    @model_validator(mode="after")
    def _check_production_storage_backend(self) -> "Settings":
        if self.is_production and self.storage_backend.lower() != "r2":
            raise ValueError(
                "STORAGE_BACKEND must be 'r2' when ENVIRONMENT=production - local "
                "disk storage does not survive a Railway redeploy (KNOWN_ISSUES.md)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
