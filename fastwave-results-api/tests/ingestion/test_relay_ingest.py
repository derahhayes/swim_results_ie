import pytest
from sqlalchemy import func, select

from app.ingestion.service import ingest_file
from app.ingestion.storage import LocalDirStorage
from app.models import Club, RelayLeg, Result, Swimmer

from .relay_fixture import build_synthetic_relay_hy3

pytestmark = pytest.mark.usefixtures("clean_db")

SWIMMER_IDS_A = ["1", "2", "3", "4"]
SWIMMER_IDS_B = ["5", "6", "7", "8"]
SWIMMER_IDS_BBBB_A = ["9", "10", "11", "12"]


async def _ingest(db_session, tmp_path, storage, **overrides):
    data = build_synthetic_relay_hy3(
        SWIMMER_IDS_A, SWIMMER_IDS_B, SWIMMER_IDS_BBBB_A, **overrides
    )
    path = tmp_path / f"synthetic_relay_{overrides.get('time_a', 'base')}.hy3"
    path.write_bytes(data)
    return await ingest_file(path, "dev@derahsoftware.com", db_session, storage=storage)


async def test_relay_teams_are_distinct_rows(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    result = await _ingest(db_session, tmp_path, storage)

    assert result.status == "promoted"

    relay_results = (
        (await db_session.execute(select(Result).where(Result.swimmerId.is_(None)))).scalars().all()
    )
    assert len(relay_results) == 3

    by_team = {(r.relayTeamId, r.clubId) for r in relay_results}
    assert len(by_team) == 3  # AAAA/A, AAAA/B, BBBB/A all distinct

    clubs = {c.code: c.id for c in (await db_session.execute(select(Club))).scalars().all()}
    aaaa_teams = {r.relayTeamId for r in relay_results if r.clubId == clubs["AAAA"]}
    assert aaaa_teams == {"A", "B"}
    bbbb_teams = {r.relayTeamId for r in relay_results if r.clubId == clubs["BBBB"]}
    assert bbbb_teams == {"A"}

    swimmer_count = (await db_session.execute(select(func.count()).select_from(Swimmer))).scalar_one()
    assert swimmer_count == 12

    leg_count = (await db_session.execute(select(func.count()).select_from(RelayLeg))).scalar_one()
    assert leg_count == 12  # 3 teams x 4 legs

    for r in relay_results:
        legs = (
            (await db_session.execute(select(RelayLeg).where(RelayLeg.resultId == r.id))).scalars().all()
        )
        assert len(legs) == 4
        assert {leg.legOrder for leg in legs} == {1, 2, 3, 4}


async def test_reingest_updates_relay_rows_in_place(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    first = await _ingest(db_session, tmp_path, storage, time_a="120.45")
    assert first.status == "promoted"

    result_count_1 = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()
    leg_count_1 = (await db_session.execute(select(func.count()).select_from(RelayLeg))).scalar_one()

    # Different bytes (changed time) -> different sha256 -> not a dedup hit,
    # promote() actually runs a second time and must upsert, not duplicate.
    second = await _ingest(db_session, tmp_path, storage, time_a="119.80")
    assert second.duplicate is False
    assert second.status == "promoted"

    result_count_2 = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()
    leg_count_2 = (await db_session.execute(select(func.count()).select_from(RelayLeg))).scalar_one()

    assert result_count_2 == result_count_1
    assert leg_count_2 == leg_count_1

    clubs = {c.code: c.id for c in (await db_session.execute(select(Club))).scalars().all()}
    updated = (
        await db_session.execute(
            select(Result).where(
                Result.swimmerId.is_(None), Result.clubId == clubs["AAAA"], Result.relayTeamId == "A"
            )
        )
    ).scalar_one()
    assert updated.timeHs == 11980

    legs = (
        (await db_session.execute(select(RelayLeg).where(RelayLeg.resultId == updated.id))).scalars().all()
    )
    assert len(legs) == 4
    assert {leg.legOrder for leg in legs} == {1, 2, 3, 4}
    swimmer_ids = {leg.swimmerId for leg in legs}
    db_swimmers = (
        (await db_session.execute(select(Swimmer).where(Swimmer.id.in_(swimmer_ids)))).scalars().all()
    )
    assert {s.firstName for s in db_swimmers} == {"First0", "First1", "First2", "First3"}
