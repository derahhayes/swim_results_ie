import pytest_asyncio
from sqlalchemy import select

from app.models import MeetEvent, Swimmer
from tests.api.gdpr import assert_no_pii

BENNY_REG_NO = "30085535"


async def _benny_id(db_session) -> str:
    swimmer = (
        await db_session.execute(select(Swimmer).where(Swimmer.registrationNo == BENNY_REG_NO))
    ).scalar_one()
    return swimmer.id


@pytest_asyncio.fixture
async def anonymised_benny(db_session):
    """Sets isAnonymised=True on Benny Barry for the test, restores it after."""
    swimmer = (
        await db_session.execute(select(Swimmer).where(Swimmer.registrationNo == BENNY_REG_NO))
    ).scalar_one()
    swimmer.isAnonymised = True
    await db_session.commit()
    try:
        yield swimmer.id
    finally:
        swimmer = await db_session.get(Swimmer, swimmer.id)
        swimmer.isAnonymised = False
        await db_session.commit()


async def test_search_finds_benny_by_exact_last_name(api_client, seeded_meets):
    resp = await api_client.get("/api/v1/swimmers/search", params={"q": "barry"})
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)
    assert any(item["displayName"] == "Benny" for item in body["items"])


async def test_search_finds_benny_via_typo(api_client, seeded_meets):
    resp = await api_client.get("/api/v1/swimmers/search", params={"q": "barri"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(item["displayName"] == "Benny" for item in body["items"])


async def test_search_below_min_length_is_422(api_client):
    resp = await api_client.get("/api/v1/swimmers/search", params={"q": "ba"})
    assert resp.status_code == 422


async def test_swimmer_detail(api_client, seeded_meets, db_session):
    swimmer_id = await _benny_id(db_session)
    resp = await api_client.get(f"/api/v1/swimmers/{swimmer_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)
    assert body["displayName"] == "Benny"
    assert body["gender"] == "M"
    assert body["club"]["code"] == "ASG"
    assert body["resultCount"] == 3
    assert body["seasonsActive"] == ["2025/26"]


async def test_swimmer_results_paginated_and_season_filtered(api_client, seeded_meets, db_session):
    swimmer_id = await _benny_id(db_session)

    resp = await api_client.get(f"/api/v1/swimmers/{swimmer_id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert any(row["timeHs"] == 6312 for row in body["items"])

    # All fixture results fall in the 2025/26 season.
    resp = await api_client.get(f"/api/v1/swimmers/{swimmer_id}/results", params={"season": "2025/26"})
    assert resp.json()["total"] == 3

    resp = await api_client.get(f"/api/v1/swimmers/{swimmer_id}/results", params={"season": "2020/21"})
    assert resp.json()["total"] == 0


async def test_swimmer_results_bad_season_label_422(api_client, seeded_meets, db_session):
    swimmer_id = await _benny_id(db_session)
    resp = await api_client.get(f"/api/v1/swimmers/{swimmer_id}/results", params={"season": "garbage"})
    assert resp.status_code == 422


async def test_swimmer_bogus_id_404(api_client):
    resp = await api_client.get("/api/v1/swimmers/does-not-exist")
    assert resp.status_code == 404


async def test_anonymised_swimmer_name_withheld_and_excluded_from_search(
    api_client, seeded_meets, anonymised_benny, db_session
):
    search_resp = await api_client.get("/api/v1/swimmers/search", params={"q": "barry"})
    assert all(item["displayName"] != "Benny" for item in search_resp.json()["items"])

    detail_resp = await api_client.get(f"/api/v1/swimmers/{anonymised_benny}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["displayName"] == "Name withheld"
    assert_no_pii(detail_resp.json())

    # Results rows remain (event integrity) but the swimmer's name is withheld,
    # both from their own results listing and from the event-results view.
    results_resp = await api_client.get(f"/api/v1/swimmers/{anonymised_benny}/results")
    assert results_resp.json()["total"] == 3
    assert_no_pii(results_resp.json())

    event = (
        await db_session.execute(
            select(MeetEvent).where(MeetEvent.meetId == seeded_meets["main"], MeetEvent.eventNo == "3A")
        )
    ).scalar_one()
    event_resp = await api_client.get(f"/api/v1/events/{event.id}/results")
    matching = [
        row
        for row in event_resp.json()["rounds"][0]["results"]
        if row["swimmer"] is not None and row["swimmer"]["id"] == anonymised_benny
    ]
    assert len(matching) == 1
    assert matching[0]["swimmer"]["displayName"] == "Name withheld"
    assert_no_pii(event_resp.json())
