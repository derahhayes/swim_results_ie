from datetime import date

import pytest_asyncio
from sqlalchemy import select

from app.models import Club, Swimmer, User
from app.models.enums import Gender
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user, unique_email


def _dob_for_age(age_years: int) -> date:
    today = date.today()
    return today.replace(year=today.year - age_years)


async def _make_swimmer(db_session, *, age_years: int, club_code: str = "ASG") -> str:
    club = (await db_session.execute(select(Club).where(Club.code == club_code))).scalar_one()
    swimmer = Swimmer(
        firstName="Claimable",
        lastName=f"Swimmer-{unique_email('x')[:8]}",
        gender=Gender.M,
        dateOfBirth=_dob_for_age(age_years),
        clubId=club.id,
    )
    db_session.add(swimmer)
    await db_session.commit()
    await db_session.refresh(swimmer)
    return swimmer.id


@pytest_asyncio.fixture
async def admin_user(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isAdmin=True)
    return user


async def test_create_claim_and_admin_approval_sets_is_swimmer(api_client, db_session, admin_user):
    swimmer_id = await _make_swimmer(db_session, age_years=25)
    user = await register_verified_user(api_client)

    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    assert create_resp.status_code == 201, create_resp.text
    claim = create_resp.json()
    assert claim["status"] == "pending"

    approve_resp = await api_client.post(
        f"/api/v1/claims/{claim['id']}/approve",
        json={},
        headers=auth_headers(admin_user["access_token"]),
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"

    me = await api_client.get("/api/v1/users/me", headers=auth_headers(user["access_token"]))
    body = me.json()
    assert body["isSwimmer"] is True
    assert body["claims"][0]["status"] == "approved"


async def test_duplicate_claim_is_409(api_client, db_session):
    swimmer_id = await _make_swimmer(db_session, age_years=30)
    user = await register_verified_user(api_client)
    headers = auth_headers(user["access_token"])

    first = await api_client.post("/api/v1/claims", json={"swimmerId": swimmer_id, "relationship": "self"}, headers=headers)
    assert first.status_code == 201
    second = await api_client.post("/api/v1/claims", json={"swimmerId": swimmer_id, "relationship": "self"}, headers=headers)
    assert second.status_code == 409


async def test_claim_unknown_swimmer_is_404(api_client):
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": "does-not-exist", "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 404


async def test_under_16_self_claim_is_rejected(api_client, db_session):
    swimmer_id = await _make_swimmer(db_session, age_years=10)
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 422
    assert "under 16" in resp.json()["detail"].lower()


async def test_under_16_parent_claim_is_allowed(api_client, db_session):
    swimmer_id = await _make_swimmer(db_session, age_years=10)
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "parent"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 201


async def test_reject_claim_requires_reason(api_client, db_session, admin_user):
    swimmer_id = await _make_swimmer(db_session, age_years=40)
    user = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    claim_id = create_resp.json()["id"]

    missing_reason = await api_client.post(
        f"/api/v1/claims/{claim_id}/reject", json={}, headers=auth_headers(admin_user["access_token"])
    )
    assert missing_reason.status_code == 422

    with_reason = await api_client.post(
        f"/api/v1/claims/{claim_id}/reject",
        json={"reason": "Details don't match"},
        headers=auth_headers(admin_user["access_token"]),
    )
    assert with_reason.status_code == 200
    assert with_reason.json()["status"] == "rejected"


async def test_deciding_already_decided_claim_is_409(api_client, db_session, admin_user):
    swimmer_id = await _make_swimmer(db_session, age_years=22)
    user = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    claim_id = create_resp.json()["id"]

    first = await api_client.post(
        f"/api/v1/claims/{claim_id}/approve", json={}, headers=auth_headers(admin_user["access_token"])
    )
    assert first.status_code == 200
    second = await api_client.post(
        f"/api/v1/claims/{claim_id}/approve", json={}, headers=auth_headers(admin_user["access_token"])
    )
    assert second.status_code == 409


async def test_non_admin_cannot_list_or_decide_claims(api_client, db_session):
    swimmer_id = await _make_swimmer(db_session, age_years=22)
    user = await register_verified_user(api_client)
    other = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    claim_id = create_resp.json()["id"]

    list_resp = await api_client.get("/api/v1/claims", headers=auth_headers(other["access_token"]))
    assert list_resp.status_code == 403

    decide_resp = await api_client.post(
        f"/api/v1/claims/{claim_id}/approve", json={}, headers=auth_headers(other["access_token"])
    )
    assert decide_resp.status_code == 403


async def test_list_claims_filtered_by_status(api_client, db_session, admin_user):
    swimmer_id = await _make_swimmer(db_session, age_years=33)
    user = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    claim_id = create_resp.json()["id"]

    pending_list = await api_client.get(
        "/api/v1/claims", params={"status_filter": "pending"}, headers=auth_headers(admin_user["access_token"])
    )
    assert pending_list.status_code == 200
    assert any(c["id"] == claim_id for c in pending_list.json()["items"])

    await api_client.post(
        f"/api/v1/claims/{claim_id}/approve", json={}, headers=auth_headers(admin_user["access_token"])
    )

    pending_after = await api_client.get(
        "/api/v1/claims", params={"status_filter": "pending"}, headers=auth_headers(admin_user["access_token"])
    )
    assert all(c["id"] != claim_id for c in pending_after.json()["items"])

    approved_after = await api_client.get(
        "/api/v1/claims", params={"status_filter": "approved"}, headers=auth_headers(admin_user["access_token"])
    )
    assert any(c["id"] == claim_id for c in approved_after.json()["items"])


async def test_patch_user_roles_requires_admin(api_client):
    user = await register_verified_user(api_client)
    other = await register_verified_user(api_client)
    resp = await api_client.patch(
        f"/api/v1/users/{other['id']}/roles",
        json={"isUploader": True},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 403


async def test_patch_user_roles_updates_only_provided_fields(api_client, admin_user, db_session):
    target = await register_verified_user(api_client)
    resp = await api_client.patch(
        f"/api/v1/users/{target['id']}/roles",
        json={"isUploader": True},
        headers=auth_headers(admin_user["access_token"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["isUploader"] is True
    assert body["isAdmin"] is False
    assert body["isCoach"] is False

    refreshed = await db_session.get(User, target["id"])
    assert refreshed.isUploader is True
    assert refreshed.isAdmin is False
