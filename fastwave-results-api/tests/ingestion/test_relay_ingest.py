import pytest
from sqlalchemy import func, select

from app.ingestion.service import ingest_file
from app.ingestion.storage import LocalDirStorage
from app.models import Club, MeetEvent, RelayLeg, Result, ResultSplit, Swimmer

from .relay_fixture import build_synthetic_relay_hy3, f1_line, f2_line, f3_line, g1_line

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


async def test_relay_result_with_all_unrecorded_splits_does_not_crash(db_session, tmp_path):
    """Regression: a G1 line whose splits are all "0.00"/unrecorded (Hy-
    Tek's convention for "no time recorded here") used to crash the whole
    import. _replace_splits filters every split's time_to_hundredths() to
    None, leaving an empty rows list - pg_insert(ResultSplit).values([])
    doesn't no-op, it silently inserts one row using only column defaults
    (id/createdAt/updatedAt), leaving resultId/splitNumber/cumulativeTimeHs
    NULL and violating their NOT NULL constraints. Confirmed via a direct
    SQL-compile check before writing this fix.
    """
    storage = LocalDirStorage(tmp_path)
    raw = build_synthetic_relay_hy3(SWIMMER_IDS_A, SWIMMER_IDS_B, SWIMMER_IDS_BBBB_A)
    lines = raw.decode("cp1252").splitlines()

    # Splice an all-zero G1 split line right after the first relay block's
    # F3 line (AAAA/A's finals result) - "0.00" is Hy-Tek's own convention
    # for an unrecorded split, not a real zero time.
    f3_idx = next(i for i, line in enumerate(lines) if line.startswith("F3"))
    lines.insert(f3_idx + 1, g1_line("F", {1: 0.0, 2: 0.0}))
    raw_with_zero_splits = ("\n".join(lines) + "\n").encode("cp1252")

    path = tmp_path / "relay_all_zero_splits.hy3"
    path.write_bytes(raw_with_zero_splits)

    result = await ingest_file(path, "dev@derahsoftware.com", db_session, storage=storage)
    assert result.status == "promoted", result.report

    # The other two relay teams (unaffected by the zero-splits team) still
    # promoted normally - the bug used to abort the *entire* transaction.
    relay_results = (
        (await db_session.execute(select(Result).where(Result.swimmerId.is_(None)))).scalars().all()
    )
    assert len(relay_results) == 3

    clubs = {c.code: c.id for c in (await db_session.execute(select(Club))).scalars().all()}
    aaaa_a_result = (
        await db_session.execute(
            select(Result).where(
                Result.swimmerId.is_(None), Result.clubId == clubs["AAAA"], Result.relayTeamId == "A"
            )
        )
    ).scalar_one()

    splits = (
        (await db_session.execute(select(ResultSplit).where(ResultSplit.resultId == aaaa_a_result.id)))
        .scalars()
        .all()
    )
    assert splits == []


async def test_unmapped_event_is_isolated_reject_not_a_crash(db_session, tmp_path):
    """An event with no mappable stroke/gender is rejected (event_unmapped)
    and produces zero downstream artifacts for itself, but must not affect
    any other event's promotion in the same file - _build_individual_results
    and _promote_relays both skip an event entirely once event_ids.get(number)
    comes back None (no meet_events row was created for it), so nothing
    downstream ever runs for it in the first place.
    """
    storage = LocalDirStorage(tmp_path)
    raw = build_synthetic_relay_hy3(SWIMMER_IDS_A, SWIMMER_IDS_B, SWIMMER_IDS_BBBB_A)
    lines = raw.decode("cp1252").splitlines()

    # "Z" isn't a real HY3 stroke code (A/B/C/D/E) - select_from_enum falls
    # back to Stroke.UNKNOWN, which STROKE_MAP has no entry for, so
    # _upsert_events rejects this event as event_unmapped. Reuses AAAA's
    # already-registered swimmers 1-4 rather than adding new D1 lines.
    bogus_event = [
        f1_line("AAAA", "A", "M", "M", 200, "Z", "99", "L"),
        f2_line("F", "150.00", "L", 1, 1, "06012026"),
        f3_line(["1", "2", "3", "4"]),
    ]
    z0_idx = next(i for i, line in enumerate(lines) if line.startswith("Z0"))
    lines[z0_idx:z0_idx] = bogus_event
    raw_with_bogus_event = ("\n".join(lines) + "\n").encode("cp1252")

    path = tmp_path / "relay_with_unmapped_event.hy3"
    path.write_bytes(raw_with_bogus_event)

    result = await ingest_file(path, "dev@derahsoftware.com", db_session, storage=storage)
    assert result.status == "promoted", result.report
    assert any(r["reason"] == "event_unmapped" for r in result.report["rejects"])

    # The unmapped event never got a meet_events row at all.
    event_99 = (
        await db_session.execute(select(MeetEvent).where(MeetEvent.eventNo == "99"))
    ).scalar_one_or_none()
    assert event_99 is None

    # Every other event's relay teams still promoted normally - the
    # original crash would have aborted this whole transaction instead.
    relay_results = (
        (await db_session.execute(select(Result).where(Result.swimmerId.is_(None)))).scalars().all()
    )
    assert len(relay_results) == 3
