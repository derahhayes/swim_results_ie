from sqlalchemy import select

from app.cli import publish_meet, unpublish_meet
from app.models import MeetEvent
from tests.api.gdpr import assert_no_pii


async def test_meets_lists_seeded_meets_newest_first(api_client, seeded_meets):
    resp = await api_client.get("/api/v1/meets")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)

    ids = [m["id"] for m in body["items"]]
    assert seeded_meets["main"] in ids
    assert seeded_meets["relay"] in ids

    # Relay meet is 2026-06-01, main meet is 2026-05-30 - relay is newer.
    assert ids.index(seeded_meets["relay"]) < ids.index(seeded_meets["main"])

    main = next(m for m in body["items"] if m["id"] == seeded_meets["main"])
    assert main["name"] == "Michael Bowles 2026.05.30"
    assert main["course"] == "L"
    assert main["counts"] == {"eventCount": 51, "swimmerCount": 448, "clubCount": 29}


async def test_meet_detail_has_correct_event_count(api_client, seeded_meets):
    resp = await api_client.get(f"/api/v1/meets/{seeded_meets['main']}")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)
    assert len(body["events"]) == 51
    assert all("resultCount" in e for e in body["events"])


async def test_meet_detail_bogus_id_404(api_client):
    resp = await api_client.get("/api/v1/meets/does-not-exist")
    assert resp.status_code == 404


async def test_meet_clubs_lists_all_clubs_with_results(api_client, seeded_meets):
    resp = await api_client.get(f"/api/v1/meets/{seeded_meets['main']}/clubs")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)
    assert len(body) == 29
    assert any(c["code"] == "ASG" for c in body)
    assert all(c["resultCount"] > 0 for c in body)


async def test_club_results_grouped_by_event(api_client, seeded_meets):
    resp = await api_client.get(f"/api/v1/meets/{seeded_meets['main']}/clubs/ASG/results")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)
    assert body["club"]["code"] == "ASG"
    assert len(body["events"]) > 0

    total_results = sum(len(group["results"]) for group in body["events"])
    assert total_results == body["club"]["resultCount"]

    # Benny Barry (reg 30085535) swims for ASG - his 3 results should be in here.
    all_swimmer_ids = {
        row["swimmer"]["id"]
        for group in body["events"]
        for row in group["results"]
        if row["swimmer"] is not None
    }
    assert len(all_swimmer_ids) > 0


async def test_club_results_bogus_club_404(api_client, seeded_meets):
    resp = await api_client.get(f"/api/v1/meets/{seeded_meets['main']}/clubs/ZZZZ/results")
    assert resp.status_code == 404


async def test_unpublish_hides_meet_and_404s_everything(api_client, seeded_meets, db_session):
    meet_id = seeded_meets["main"]

    event = (
        await db_session.execute(select(MeetEvent).where(MeetEvent.meetId == meet_id).limit(1))
    ).scalar_one()
    event_id = event.id

    try:
        await unpublish_meet(meet_id)

        list_resp = await api_client.get("/api/v1/meets")
        assert meet_id not in [m["id"] for m in list_resp.json()["items"]]

        assert (await api_client.get(f"/api/v1/meets/{meet_id}")).status_code == 404
        assert (await api_client.get(f"/api/v1/meets/{meet_id}/clubs")).status_code == 404
        assert (await api_client.get(f"/api/v1/meets/{meet_id}/clubs/ASG/results")).status_code == 404
        assert (await api_client.get(f"/api/v1/events/{event_id}/results")).status_code == 404
    finally:
        await publish_meet(meet_id)

    # Restored - subsequent tests (and this one, for good measure) see it again.
    assert (await api_client.get(f"/api/v1/meets/{meet_id}")).status_code == 200
