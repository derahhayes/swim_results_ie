from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
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
