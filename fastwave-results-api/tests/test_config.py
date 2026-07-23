import re

import pytest

from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x",
        database_url_direct="postgresql+psycopg2://x",
        jwt_secret_key="test-secret",
    )


def test_missing_database_url_fails_loudly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_missing_jwt_secret_fails_loudly(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None, database_url="x", database_url_direct="x")


def test_cors_origin_regex_matches_lovable_preview_domain(settings):
    assert re.fullmatch(settings.cors_origin_regex, "https://foo.lovable.app")
    assert re.fullmatch(settings.cors_origin_regex, "https://my-project-abc123.lovable.app")


def test_cors_origin_regex_rejects_other_domains(settings):
    assert not re.fullmatch(settings.cors_origin_regex, "https://evil.com")
    assert not re.fullmatch(settings.cors_origin_regex, "https://lovable.app")  # no subdomain
    assert not re.fullmatch(settings.cors_origin_regex, "https://foo.lovable.app.evil.com")
    assert not re.fullmatch(settings.cors_origin_regex, "http://foo.lovable.app")  # not https


def test_cors_origins_list_includes_localhost_by_default(settings):
    assert "http://localhost:5173" in settings.cors_origins_list


def test_admin_emails_list_parses_and_lowercases(settings):
    settings = settings.model_copy(update={"admin_emails": "A@Example.com, b@example.com"})
    assert settings.admin_emails_list == ["a@example.com", "b@example.com"]


def test_admin_emails_list_empty_when_unset(settings):
    # Not relying on "unset by default" against the real .env (which sets
    # ADMIN_EMAILS for local dev) - explicitly override it to empty instead.
    settings = settings.model_copy(update={"admin_emails": ""})
    assert settings.admin_emails_list == []


def test_docs_enabled_by_default_even_in_production():
    s = Settings(
        database_url="x",
        database_url_direct="x",
        jwt_secret_key="x",
        environment="production",
        storage_backend="r2",
    )
    assert s.docs_enabled is True


def test_docs_disabled_when_production_and_docs_public_false():
    s = Settings(
        database_url="x",
        database_url_direct="x",
        jwt_secret_key="x",
        environment="production",
        storage_backend="r2",
        docs_public=False,
    )
    assert s.docs_enabled is False


def test_docs_public_false_has_no_effect_outside_production():
    s = Settings(
        database_url="x",
        database_url_direct="x",
        jwt_secret_key="x",
        environment="development",
        docs_public=False,
    )
    assert s.docs_enabled is True


def test_production_with_local_storage_fails_loudly():
    with pytest.raises(Exception, match="STORAGE_BACKEND must be 'r2'"):
        Settings(
            database_url="x",
            database_url_direct="x",
            jwt_secret_key="x",
            environment="production",
            storage_backend="local",
        )


def test_production_with_r2_storage_is_fine():
    s = Settings(
        database_url="x",
        database_url_direct="x",
        jwt_secret_key="x",
        environment="production",
        storage_backend="r2",
    )
    assert s.storage_backend == "r2"


def test_development_with_local_storage_is_fine(settings):
    assert settings.is_production is False
    assert settings.storage_backend == "local"
