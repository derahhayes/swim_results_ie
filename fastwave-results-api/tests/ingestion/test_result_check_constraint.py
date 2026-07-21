from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Club, Meet, MeetEvent, Result, Swimmer
from app.models.base import new_id
from app.models.enums import Course, Gender, ResultStatus, Round, Stroke

pytestmark = pytest.mark.usefixtures("clean_db")


async def _seed(db_session) -> dict:
    club = Club(id=new_id(), code="ZZZZ", name="Check Constraint Test Club")
    db_session.add(club)
    await db_session.flush()

    meet = Meet(
        id=new_id(),
        name="Check Constraint Test Meet",
        startDate=date(2026, 1, 1),
        endDate=date(2026, 1, 1),
        course=Course.LCM,
    )
    db_session.add(meet)
    await db_session.flush()

    event = MeetEvent(
        id=new_id(),
        meetId=meet.id,
        eventNo="1",
        distance=200,
        stroke=Stroke.FREE,
        course=Course.LCM,
        gender=Gender.M,
        isRelay=True,
    )
    db_session.add(event)

    swimmer = Swimmer(id=new_id(), firstName="Test", lastName="Swimmer", gender=Gender.M, clubId=club.id)
    db_session.add(swimmer)
    await db_session.flush()

    return {"club": club, "meet": meet, "event": event, "swimmer": swimmer}


def _base_row(seed: dict) -> dict:
    return dict(
        id=new_id(),
        meetId=seed["meet"].id,
        eventId=seed["event"].id,
        clubId=seed["club"].id,
        round=Round.FINAL,
        status=ResultStatus.OK,
    )


async def test_both_swimmer_and_relay_team_set_violates_check(db_session):
    seed = await _seed(db_session)
    bad = Result(**_base_row(seed), swimmerId=seed["swimmer"].id, relayTeamId="A")
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="ck_result_relay_shape"):
        await db_session.flush()
    await db_session.rollback()


async def test_neither_swimmer_nor_relay_team_set_violates_check(db_session):
    seed = await _seed(db_session)
    bad = Result(**_base_row(seed), swimmerId=None, relayTeamId=None)
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="ck_result_relay_shape"):
        await db_session.flush()
    await db_session.rollback()


async def test_individual_shape_is_valid(db_session):
    seed = await _seed(db_session)
    ok = Result(**_base_row(seed), swimmerId=seed["swimmer"].id, relayTeamId=None)
    db_session.add(ok)
    await db_session.flush()  # should not raise


async def test_relay_shape_is_valid(db_session):
    seed = await _seed(db_session)
    ok = Result(**_base_row(seed), swimmerId=None, relayTeamId="A")
    db_session.add(ok)
    await db_session.flush()  # should not raise
