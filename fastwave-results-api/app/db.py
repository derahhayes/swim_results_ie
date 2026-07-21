from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


def to_asyncpg_url(raw_url: str) -> str:
    """Normalize DATABASE_URL to the postgresql+asyncpg:// form create_async_engine needs.

    Some hosts (Railway, when a Postgres connection string is passed
    through as a plain variable rather than typed in by hand) hand over a
    bare postgres:// or postgresql:// URL with no driver specified.
    SQLAlchemy resolves that to the sync psycopg2 driver, which
    create_async_engine can't use at all - the app crashed at import time,
    before it could serve a single request (every healthcheck attempt saw
    "service unavailable" until Railway gave up). Idempotent: a URL that's
    already postgresql+asyncpg (e.g. our own .env for local dev) passes
    through with its query string otherwise untouched.

    Also drops libpq-only query params (sslmode, channel_binding) that
    asyncpg's connect() doesn't accept as keyword arguments - SQLAlchemy's
    asyncpg dialect passes the URL's query string through to
    asyncpg.connect() verbatim, so a Neon-style
    "?sslmode=require&channel_binding=require" query string would
    otherwise crash with a *different* TypeError right after fixing the
    scheme (see README's asyncpg-vs-libpq query param note - same reason
    DATABASE_URL_DIRECT and DATABASE_URL use different query params).
    """
    url = make_url(raw_url)

    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+asyncpg")
    elif url.drivername != "postgresql+asyncpg":
        raise ValueError(
            "DATABASE_URL must be a postgres://, postgresql://, or "
            f"postgresql+asyncpg:// URL - got drivername={url.drivername!r}"
        )

    query = dict(url.query)
    query.pop("sslmode", None)
    query.pop("channel_binding", None)
    query.setdefault("ssl", "require")
    url = url.set(query=query)

    return url.render_as_string(hide_password=False)


settings = get_settings()

engine = create_async_engine(
    to_asyncpg_url(settings.database_url),
    pool_pre_ping=True,
    # DATABASE_URL goes through Neon's pooled (PgBouncer, transaction-mode)
    # endpoint, which can hand the same client connection off to a
    # different backend between statements. asyncpg's client-side prepared
    # statement cache assumes a stable backend and breaks under that
    # (surfaces as "could not resolve query result and/or argument types in
    # N attempts", most reliably reproduced right after a DDL change like
    # dropping/recreating an enum type changes its OID mid-session).
    # Disabling the cache is the standard fix for asyncpg + PgBouncer.
    connect_args={"statement_cache_size": 0},
)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
