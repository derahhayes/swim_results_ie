from functools import lru_cache

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

    # dev | production. Only gates the /docs toggle for now (see
    # docs_public) - not a broad feature-flag mechanism.
    environment: str = "development"
    # Lovable and we both use /docs against the deployed API, so it stays
    # public by default even in production; set false to lock it down.
    docs_public: bool = True

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def docs_enabled(self) -> bool:
        return self.docs_public or not self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
