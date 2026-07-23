import csv
import io

import pytest_asyncio
from sqlalchemy import select

from app.models import Club, User
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user
from tests.api.gdpr import assert_no_pii
from tests.api.test_swimmers import BENNY_REG_NO

ASG_CODE = "ASG"


async def _club_id(db_session, code: str = ASG_CODE) -> str:
    club = (await db_session.execute(select(Club).where(Club.code == code))).scalar_one()
    return club.id


@pytest_asyncio.fixture
async def admin_user(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isAdmin=True)
    return user


async def test_create_affiliation_and_admin_approval_sets_is_coach(api_client, db_session, seeded_meets, admin_user):
    club_id = await _club_id(db_session)
    user = await register_verified_user(api_client)

    create_resp = await api_client.post(
        "/api/v1/coach-affiliations", json={"clubId": club_id}, headers=auth_headers(user["access_token"])
    )
    assert create_resp.status_code == 201, create_resp.text
    affiliation = create_resp.json()
    assert affiliation["status"] == "pending"

    approve_resp = await api_client.post(
        f"/api/v1/coach-affiliations/{affiliation['id']}/approve",
        json={},
        headers=auth_headers(admin_user["access_token"]),
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"

    me = await api_client.get("/api/v1/users/me", headers=auth_headers(user["access_token"]))
    body = me.json()
    assert body["isCoach"] is True
    assert body["affiliations"][0]["status"] == "approved"


async def test_duplicate_affiliation_is_409(api_client, db_session, seeded_meets):
    club_id = await _club_id(db_session)
    user = await register_verified_user(api_client)
    headers = auth_headers(user["access_token"])

    first = await api_client.post("/api/v1/coach-affiliations", json={"clubId": club_id}, headers=headers)
    assert first.status_code == 201
    second = await api_client.post("/api/v1/coach-affiliations", json={"clubId": club_id}, headers=headers)
    assert second.status_code == 409


async def test_affiliation_unknown_club_is_404(api_client):
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        "/api/v1/coach-affiliations", json={"clubId": "does-not-exist"}, headers=auth_headers(user["access_token"])
    )
    assert resp.status_code == 404


async def test_reject_affiliation_requires_reason(api_client, db_session, seeded_meets, admin_user):
    club_id = await _club_id(db_session)
    user = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/coach-affiliations", json={"clubId": club_id}, headers=auth_headers(user["access_token"])
    )
    affiliation_id = create_resp.json()["id"]

    missing_reason = await api_client.post(
        f"/api/v1/coach-affiliations/{affiliation_id}/reject",
        json={},
        headers=auth_headers(admin_user["access_token"]),
    )
    assert missing_reason.status_code == 422

    with_reason = await api_client.post(
        f"/api/v1/coach-affiliations/{affiliation_id}/reject",
        json={"reason": "Not a recognised coach for this club"},
        headers=auth_headers(admin_user["access_token"]),
    )
    assert with_reason.status_code == 200
    assert with_reason.json()["status"] == "rejected"


async def test_non_admin_cannot_list_or_decide_affiliations(api_client, db_session, seeded_meets):
    club_id = await _club_id(db_session)
    user = await register_verified_user(api_client)
    other = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/coach-affiliations", json={"clubId": club_id}, headers=auth_headers(user["access_token"])
    )
    affiliation_id = create_resp.json()["id"]

    list_resp = await api_client.get("/api/v1/coach-affiliations", headers=auth_headers(other["access_token"]))
    assert list_resp.status_code == 403

    decide_resp = await api_client.post(
        f"/api/v1/coach-affiliations/{affiliation_id}/approve", json={}, headers=auth_headers(other["access_token"])
    )
    assert decide_resp.status_code == 403


async def test_coach_view_requires_approved_affiliation(api_client, db_session, seeded_meets):
    club_id = await _club_id(db_session)
    user = await register_verified_user(api_client)

    before = await api_client.get(f"/api/v1/clubs/{club_id}/coach-view", headers=auth_headers(user["access_token"]))
    assert before.status_code == 403

    create_resp = await api_client.post(
        "/api/v1/coach-affiliations", json={"clubId": club_id}, headers=auth_headers(user["access_token"])
    )
    affiliation_id = create_resp.json()["id"]

    still_pending = await api_client.get(
        f"/api/v1/clubs/{club_id}/coach-view", headers=auth_headers(user["access_token"])
    )
    assert still_pending.status_code == 403

    admin = await register_verified_user(api_client)
    await grant_roles(db_session, admin["id"], isAdmin=True)
    await api_client.post(
        f"/api/v1/coach-affiliations/{affiliation_id}/approve",
        json={},
        headers=auth_headers(admin["access_token"]),
    )

    after = await api_client.get(f"/api/v1/clubs/{club_id}/coach-view", headers=auth_headers(user["access_token"]))
    assert after.status_code == 200
    body = after.json()
    assert body["club"]["code"] == ASG_CODE
    benny = next(s for s in body["swimmers"] if s["displayName"] == "Benny")
    assert benny["registrationNo"] == BENNY_REG_NO
    assert benny["dateOfBirth"] is not None

    # DOB/registrationNo are the explicit allowlisted exception here - no
    # other PII (email/citizenship/address) should leak.
    assert_no_pii(body, allow=("dateOfBirth", "registrationNo"))


async def test_admin_can_view_any_club_coach_view_without_affiliation(api_client, db_session, seeded_meets, admin_user):
    club_id = await _club_id(db_session)
    resp = await api_client.get(f"/api/v1/clubs/{club_id}/coach-view", headers=auth_headers(admin_user["access_token"]))
    assert resp.status_code == 200


async def test_coach_view_csv_export(api_client, db_session, seeded_meets, admin_user):
    club_id = await _club_id(db_session)
    resp = await api_client.get(
        f"/api/v1/clubs/{club_id}/coach-view/export.csv", headers=auth_headers(admin_user["access_token"])
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0] == ["id", "displayName", "gender", "dateOfBirth", "registrationNo"]
    benny_rows = [r for r in rows if r[1] == "Benny"]
    assert len(benny_rows) == 1
    assert benny_rows[0][4] == BENNY_REG_NO


async def test_coach_view_unknown_club_is_404(api_client, admin_user):
    resp = await api_client.get("/api/v1/clubs/does-not-exist/coach-view", headers=auth_headers(admin_user["access_token"]))
    assert resp.status_code == 404
