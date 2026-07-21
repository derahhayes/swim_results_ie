from datetime import date
from pathlib import Path

import pytest
from hytek_parser._utils import extract
from sqlalchemy import func, select

from app.ingestion._mappings import GENDER_MAP
from app.ingestion.checksums import compute_checksum
from app.ingestion.service import ingest_file
from app.ingestion.storage import LocalDirStorage
from app.models import Club, Meet, MeetEvent, RelayLeg, Result, ResultSplit, Swimmer, Upload
from app.models.base import new_id
from app.models.enums import Course, Round

FIXTURE = Path(__file__).parent.parent / "fixtures" / "michael_bowles_2026.hy3"

pytestmark = pytest.mark.usefixtures("clean_db")


def _mutate_time(line: str, new_time_str: str) -> str:
    new_field = new_time_str.rjust(8)
    mutated = line[:3] + new_field + line[11:]
    return mutated[:128] + compute_checksum(mutated)


def _write_mutated_fixture(tmp_path: Path, event_no: str, swimmer_meet_id: str, new_time: str) -> Path:
    lines = FIXTURE.read_bytes().decode("cp1252").splitlines()
    target_idx = None
    for i, line in enumerate(lines):
        if line[0:2] == "E1" and extract(line, 39, 4) == event_no and extract(line, 4, 5) == swimmer_meet_id:
            target_idx = i + 1
            break
    assert target_idx is not None, "target E1 line not found in fixture"
    assert lines[target_idx][0:2] == "E2"
    lines[target_idx] = _mutate_time(lines[target_idx], new_time)

    mutated_path = tmp_path / "michael_bowles_2026_mutated.hy3"
    mutated_path.write_bytes(("\n".join(lines) + "\n").encode("cp1252"))
    return mutated_path


async def test_full_ingest_of_fixture(db_session, tmp_path):
    result = await ingest_file(FIXTURE, "dev@derahsoftware.com", db_session, storage=LocalDirStorage(tmp_path))

    assert result.status == "promoted"
    assert not result.duplicate

    meet = (await db_session.execute(select(Meet))).scalar_one()
    assert meet.course == Course.LCM
    assert meet.startDate == date(2026, 5, 30)
    assert meet.endDate == date(2026, 5, 30)

    club_count = (await db_session.execute(select(func.count()).select_from(Club))).scalar_one()
    assert club_count == 29

    swimmer_count = (await db_session.execute(select(func.count()).select_from(Swimmer))).scalar_one()
    assert swimmer_count == 448

    results = (await db_session.execute(select(Result))).scalars().all()
    assert len(results) == 1602
    assert all(r.round == Round.FINAL for r in results)

    split_count = (await db_session.execute(select(func.count()).select_from(ResultSplit))).scalar_one()
    assert split_count > 0

    relay_leg_count = (await db_session.execute(select(func.count()).select_from(RelayLeg))).scalar_one()
    assert relay_leg_count == 0
    relay_results = [r for r in results if r.swimmerId is None]
    assert len(relay_results) == 0

    # The exact split count should match the source data: every G1 split
    # entry the parser extracted, minus the ones that are genuinely
    # unrecorded (Hy-Tek writes "0.00" for a split slot that wasn't timed -
    # e.g. this fixture has a real "F 4    0.00" split alongside recorded
    # ones for at least one 200 IM swim). 0.0 is filtered the same way
    # elsewhere in this pipeline (see test_conversions.py's zero-handling
    # test), not dropped or double counted.
    from hytek_parser import parse_hy3

    from app.ingestion.promote import time_to_hundredths
    from app.ingestion.service import HY3_ENCODING, _force_open_encoding

    with _force_open_encoding(HY3_ENCODING):
        parsed = parse_hy3(str(FIXTURE), validate_checksums=False, default_country="IRL")

    expected_split_count = sum(
        1
        for ev in parsed.meet.events.values()
        for entry in ev.entries
        if not entry.relay
        for t in entry.finals_splits.values()
        if time_to_hundredths(t) is not None
    )
    assert expected_split_count > 0
    assert split_count == expected_split_count


async def test_spot_check_benny_barry(db_session, tmp_path):
    await ingest_file(FIXTURE, "dev@derahsoftware.com", db_session, storage=LocalDirStorage(tmp_path))

    swimmers = (
        (await db_session.execute(select(Swimmer).where(Swimmer.registrationNo == "30085535"))).scalars().all()
    )
    assert len(swimmers) == 1
    swimmer = swimmers[0]
    assert swimmer.firstName == "Benjamin"
    assert swimmer.lastName == "Barry"
    assert swimmer.preferredName == "Benny"
    assert swimmer.dateOfBirth == date(2012, 1, 30)

    club = (await db_session.execute(select(Club).where(Club.id == swimmer.clubId))).scalar_one()
    assert club.code == "ASG"

    results = (
        (await db_session.execute(select(Result).where(Result.swimmerId == swimmer.id))).scalars().all()
    )
    assert len(results) == 3
    assert any(r.timeHs == 6312 for r in results)


async def test_reingest_is_idempotent(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    first = await ingest_file(FIXTURE, "dev@derahsoftware.com", db_session, storage=storage)
    assert first.status == "promoted"

    result_count_1 = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()
    swimmer_count_1 = (await db_session.execute(select(func.count()).select_from(Swimmer))).scalar_one()

    second = await ingest_file(FIXTURE, "dev@derahsoftware.com", db_session, storage=storage)
    assert second.duplicate is True
    assert second.upload_id == first.upload_id

    result_count_2 = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()
    swimmer_count_2 = (await db_session.execute(select(func.count()).select_from(Swimmer))).scalar_one()

    assert result_count_2 == result_count_1
    assert swimmer_count_2 == swimmer_count_1

    upload_count = (await db_session.execute(select(func.count()).select_from(Upload))).scalar_one()
    assert upload_count == 1


async def test_reingest_mutated_copy_updates_in_place(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    await ingest_file(FIXTURE, "dev@derahsoftware.com", db_session, storage=storage)

    result_count_before = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()

    mutated_path = _write_mutated_fixture(tmp_path, event_no="3A", swimmer_meet_id="190", new_time="62.55")
    mutated = await ingest_file(mutated_path, "dev@derahsoftware.com", db_session, storage=storage)

    assert mutated.duplicate is False  # different bytes -> different sha256 -> not a dedup hit
    assert mutated.status == "promoted"

    result_count_after = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()
    assert result_count_after == result_count_before  # updated in place, not duplicated

    swimmer = (
        await db_session.execute(select(Swimmer).where(Swimmer.registrationNo == "30085535"))
    ).scalar_one()
    updated_result = (
        await db_session.execute(
            select(Result)
            .join(MeetEvent, Result.eventId == MeetEvent.id)
            .where(Result.swimmerId == swimmer.id, MeetEvent.eventNo == "3A")
        )
    ).scalar_one()
    assert updated_result.timeHs == 6255


async def test_ambiguous_swimmer_needs_review(db_session, tmp_path):
    # Find a second real swimmer in the fixture (not the Benny Barry spot-check one)
    # so we can pre-seed a name+DOB collision in a different club.
    from hytek_parser import parse_hy3

    from app.ingestion.service import HY3_ENCODING, _force_open_encoding

    with _force_open_encoding(HY3_ENCODING):
        parsed = parse_hy3(str(FIXTURE), validate_checksums=False, default_country="IRL")
    target = next(
        s for s in parsed.meet.swimmers.values() if s.usa_swimming_id != "30085535" and s.date_of_birth
    )

    other_club = Club(id=new_id(), code="ZZZZ", name="Placeholder Other Club")
    db_session.add(other_club)
    await db_session.flush()

    decoy = Swimmer(
        id=new_id(),
        registrationNo=None,
        firstName=target.first_name,
        lastName=target.last_name,
        gender=GENDER_MAP[target.gender],
        dateOfBirth=target.date_of_birth,
        clubId=other_club.id,
    )
    db_session.add(decoy)
    await db_session.commit()

    result = await ingest_file(FIXTURE, "dev@derahsoftware.com", db_session, storage=LocalDirStorage(tmp_path))

    assert result.status == "needs_review"
    assert result.report["swimmers_needs_review"] >= 1

    # No new swimmer row should have been created for the ambiguous incoming
    # record - only the pre-seeded decoy exists under that name, and it has
    # no results (withheld pending review).
    swimmer_rows = (
        await db_session.execute(
            select(Swimmer).where(Swimmer.firstName == target.first_name, Swimmer.lastName == target.last_name)
        )
    ).scalars().all()
    assert len(swimmer_rows) == 1  # just the pre-seeded decoy - no duplicate created

    # Meanwhile the vast majority of the meet still promoted fine.
    result_count = (await db_session.execute(select(func.count()).select_from(Result))).scalar_one()
    assert result_count > 1500
