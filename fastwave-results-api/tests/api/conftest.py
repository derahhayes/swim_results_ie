"""Shared fixtures for API endpoint tests.

Seeds the DB ONCE per test session: ingests both the Michael Bowles 2026
fixture (Step 2) and the synthetic relay fixture (Step 2b), then publishes
both meets via app.cli.publish_meet - the same function the real CLI
subcommand calls. Every endpoint test then reads from this one seeded
dataset, which is both fast and closer to how the API is actually used
(many reads against an already-published meet) than a fresh ingest per
test would be.

Collection-order note: tests/ingestion/'s per-test `clean_db` fixture
truncates every app table before each of its own tests. That's harmless
here only because pytest's default alphabetical collection runs
tests/api/* before tests/ingestion/* - if that ever changes, this
session-scoped seed would get wiped out from under later tests/api tests.
"""

from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.cli import publish_meet
from app.db import AsyncSessionLocal, engine
from app.ingestion.service import ingest_file
from app.ingestion.storage import LocalDirStorage
from app.main import app
from app.models import Meet
from tests.ingestion.relay_fixture import build_synthetic_relay_hy3

FIXTURE = Path(__file__).parent.parent / "fixtures" / "michael_bowles_2026.hy3"

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


@pytest_asyncio.fixture(scope="session")
async def seeded_meets(tmp_path_factory) -> dict[str, str]:
    """{"main": <Michael Bowles meetId>, "relay": <synthetic relay meetId>}, both published."""
    await _truncate_all()

    storage = LocalDirStorage(tmp_path_factory.mktemp("api-test-storage"))

    async with AsyncSessionLocal() as session:
        main_result = await ingest_file(FIXTURE, "dev@derahsoftware.com", session, storage=storage)
        assert main_result.status == "promoted", main_result.report

        relay_bytes = build_synthetic_relay_hy3(
            ["1", "2", "3", "4"], ["5", "6", "7", "8"], ["9", "10", "11", "12"]
        )
        relay_result = await ingest_file(relay_bytes, "dev@derahsoftware.com", session, storage=storage)
        assert relay_result.status == "promoted", relay_result.report

    async with AsyncSessionLocal() as session:
        main_meet = (
            await session.execute(select(Meet).where(Meet.name == "Michael Bowles 2026.05.30"))
        ).scalar_one()
        relay_meet = (await session.execute(select(Meet).where(Meet.name == "Synthetic Relay Meet"))).scalar_one()

    await publish_meet(main_meet.id)
    await publish_meet(relay_meet.id)

    yield {"main": main_meet.id, "relay": relay_meet.id}

    await _truncate_all()


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def api_client(seeded_meets):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
