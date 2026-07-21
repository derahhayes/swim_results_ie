from sqlalchemy import select

from app.models import MeetEvent, Swimmer
from tests.api.gdpr import assert_no_pii


async def _relay_event_id(db_session, meet_id: str) -> str:
    event = (
        await db_session.execute(select(MeetEvent).where(MeetEvent.meetId == meet_id, MeetEvent.eventNo == "9"))
    ).scalar_one()
    return event.id


async def test_relay_event_results_shape(api_client, seeded_meets, db_session):
    event_id = await _relay_event_id(db_session, seeded_meets["relay"])
    resp = await api_client.get(f"/api/v1/events/{event_id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)

    results = body["rounds"][0]["results"]
    assert len(results) == 3

    for row in results:
        assert row["swimmer"] is None
        assert row["relayTeam"] is not None
        legs = row["relayTeam"]["legs"]
        assert len(legs) == 4
        assert [leg["legOrder"] for leg in legs] == [1, 2, 3, 4]
        assert all(leg["swimmer"] is not None for leg in legs)
        assert all(leg["legTime"] is None for leg in legs)  # legTimeHs not derivable (see KNOWN_ISSUES.md)

    labels = {row["relayTeam"]["label"] for row in results}
    assert len(labels) == 3  # AAAA-A, AAAA-B, BBBB-A all distinct
    assert any(label.endswith("— A") for label in labels)
    assert any(label.endswith("— B") for label in labels)


async def test_relay_swim_appears_in_leg_swimmers_results(api_client, seeded_meets, db_session):
    leg_swimmer = (await db_session.execute(select(Swimmer).where(Swimmer.firstName == "First0"))).scalar_one()

    resp = await api_client.get(f"/api/v1/swimmers/{leg_swimmer.id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)

    relay_rows = [row for row in body["items"] if row["isRelay"]]
    assert len(relay_rows) == 1
    assert relay_rows[0]["legOrder"] == 1
    assert relay_rows[0]["relayTeamId"] == "A"
