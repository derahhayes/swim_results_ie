"""Mixed-gender relay events (Gender.MIXED/"X") - KNOWN_ISSUES.md.

A mixed medley relay + a mixed freestyle relay used to be silently
rejected as event_unmapped (gender came through UNKNOWN, since neither
our GENDER_MAP nor hytek-parser's own Gender enum had a MIXED member),
which combined with the _replace_splits crash (see test_relay_ingest.py)
to abort entire real-world imports containing them.
"""

import pytest
from sqlalchemy import select

from app.ingestion.service import ingest_file
from app.ingestion.storage import LocalDirStorage
from app.models import MeetEvent, RelayLeg, Result
from app.models.enums import Gender, Stroke

from .relay_fixture import build_synthetic_mixed_relay_hy3

pytestmark = pytest.mark.usefixtures("clean_db")

MEDLEY_SWIMMER_IDS = ["1", "2", "3", "4"]
FREESTYLE_SWIMMER_IDS = ["5", "6", "7", "8"]


async def test_mixed_relay_events_promote_with_gender_mixed(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    raw = build_synthetic_mixed_relay_hy3(MEDLEY_SWIMMER_IDS, FREESTYLE_SWIMMER_IDS)
    path = tmp_path / "mixed_relay.hy3"
    path.write_bytes(raw)

    result = await ingest_file(path, "dev@derahsoftware.com", db_session, storage=storage)
    assert result.status == "promoted", result.report
    assert result.report["rejects"] == []

    medley_event = (await db_session.execute(select(MeetEvent).where(MeetEvent.eventNo == "20"))).scalar_one()
    assert medley_event.gender == Gender.MIXED
    assert medley_event.stroke == Stroke.IM
    assert medley_event.isRelay is True

    freestyle_event = (
        await db_session.execute(select(MeetEvent).where(MeetEvent.eventNo == "21"))
    ).scalar_one()
    assert freestyle_event.gender == Gender.MIXED
    assert freestyle_event.stroke == Stroke.FREE

    medley_result = (
        await db_session.execute(select(Result).where(Result.eventId == medley_event.id))
    ).scalar_one()
    assert medley_result.swimmerId is None  # relay result
    assert medley_result.timeHs == 24530

    medley_legs = (
        (await db_session.execute(select(RelayLeg).where(RelayLeg.resultId == medley_result.id)))
        .scalars()
        .all()
    )
    assert len(medley_legs) == 4
    assert {leg.legOrder for leg in medley_legs} == {1, 2, 3, 4}

    freestyle_result = (
        await db_session.execute(select(Result).where(Result.eventId == freestyle_event.id))
    ).scalar_one()
    assert freestyle_result.timeHs == 21015


async def test_mixed_relay_reingest_is_idempotent_no_duplicates(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    raw = build_synthetic_mixed_relay_hy3(MEDLEY_SWIMMER_IDS, FREESTYLE_SWIMMER_IDS)

    path1 = tmp_path / "mixed_relay_1.hy3"
    path1.write_bytes(raw)
    first = await ingest_file(path1, "dev@derahsoftware.com", db_session, storage=storage)
    assert first.status == "promoted", first.report

    # Different filename, identical bytes - receive_upload's sha256 dedup
    # recognizes this and returns the same upload without reprocessing.
    path2 = tmp_path / "mixed_relay_2.hy3"
    path2.write_bytes(raw)
    second = await ingest_file(path2, "dev@derahsoftware.com", db_session, storage=storage)
    assert second.duplicate is True
    assert second.upload_id == first.upload_id

    events = (await db_session.execute(select(MeetEvent).where(MeetEvent.eventNo.in_(["20", "21"])))).scalars().all()
    assert len(events) == 2  # not 4 - no duplicate meet_events rows

    results = (await db_session.execute(select(Result).where(Result.eventId.in_([e.id for e in events])))).scalars().all()
    assert len(results) == 2  # not 4 - no duplicate results rows
