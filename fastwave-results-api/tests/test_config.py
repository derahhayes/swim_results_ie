import re

import pytest

from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="postgresql+asyncpg://x", database_url_direct="postgresql+psycopg2://x")


def test_missing_database_url_fails_loudly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    with pytest.raises(Exception):
        Settings(_env_file=None)


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


def test_docs_enabled_by_default_even_in_production():
    s = Settings(
        database_url="x", database_url_direct="x", environment="production"
    )
    assert s.docs_enabled is True


def test_docs_disabled_when_production_and_docs_public_false():
    s = Settings(
        database_url="x", database_url_direct="x", environment="production", docs_public=False
    )
    assert s.docs_enabled is False


def test_docs_public_false_has_no_effect_outside_production():
    s = Settings(
        database_url="x", database_url_direct="x", environment="development", docs_public=False
    )
    assert s.docs_enabled is True
