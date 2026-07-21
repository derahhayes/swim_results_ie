"""Fixtures for the ingestion integration tests.

These hit a live Postgres DB (the Neon dev branch configured via
DATABASE_URL) rather than mocking the database - see README for how to
point this at a throwaway branch. `clean_db` truncates all app tables
before a test runs so each integration test starts from a known-empty
state; a session-scoped fixture truncates again once the whole suite
finishes so the branch isn't left with test data.
"""

import pytest_asyncio
from sqlalchemy import text

from app.db import AsyncSessionLocal, engine

TestSessionLocal = AsyncSessionLocal

# Order doesn't matter - CASCADE handles FK dependencies.
TABLES = [
    "relay_legs",
    "result_splits",
    "results",
    "meet_events",
    "meets",
    "match_reviews",
    "uploads",
    "swimmer_claims",
    "coach_affiliations",
    "swimmers",
    "clubs",
    "users",
]


async def _truncate_all() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} CASCADE"))


@pytest_asyncio.fixture
async def clean_db():
    await _truncate_all()
    yield


@pytest_asyncio.fixture
async def db_session():
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _cleanup_after_session():
    yield
    await _truncate_all()
    await engine.dispose()
