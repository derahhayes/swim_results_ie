import pytest
from sqlalchemy.engine import make_url

from app.db import to_asyncpg_url


def test_bare_postgresql_scheme_gets_asyncpg_driver():
    result = to_asyncpg_url("postgresql://user:pass@host/db")
    assert make_url(result).drivername == "postgresql+asyncpg"


def test_legacy_postgres_scheme_gets_asyncpg_driver():
    result = to_asyncpg_url("postgres://user:pass@host/db")
    assert make_url(result).drivername == "postgresql+asyncpg"


def test_already_correct_url_passes_through():
    result = to_asyncpg_url("postgresql+asyncpg://user:pass@host/db?ssl=require")
    url = make_url(result)
    assert url.drivername == "postgresql+asyncpg"
    assert url.query["ssl"] == "require"


def test_strips_libpq_only_query_params():
    result = to_asyncpg_url("postgresql://user:pass@host/db?sslmode=require&channel_binding=require")
    url = make_url(result)
    assert "sslmode" not in url.query
    assert "channel_binding" not in url.query


def test_adds_ssl_require_when_missing():
    result = to_asyncpg_url("postgresql://user:pass@host/db")
    assert make_url(result).query["ssl"] == "require"


def test_sslmode_require_translates_to_ssl_require():
    result = to_asyncpg_url("postgresql://user:pass@host/db?sslmode=require&channel_binding=require")
    assert make_url(result).query["ssl"] == "require"


def test_preserves_credentials_and_host():
    result = to_asyncpg_url("postgresql://user:secret@myhost:5432/mydb")
    url = make_url(result)
    assert url.username == "user"
    assert url.password == "secret"
    assert url.host == "myhost"
    assert url.port == 5432
    assert url.database == "mydb"


def test_unexpected_driver_raises():
    with pytest.raises(ValueError, match="drivername"):
        to_asyncpg_url("postgresql+psycopg2://user:pass@host/db")
