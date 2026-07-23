"""GET /api/v1/me/swimmer-results and DELETE /api/v1/me."""

from datetime import date

import pytest_asyncio
from sqlalchemy import select

from app.models import Club, RefreshToken, Swimmer
from app.models.enums import Gender
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user
from tests.api.gdpr import assert_no_pii


@pytest_asyncio.fixture
async def admin_user(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isAdmin=True)
    return user


async def _make_swimmer(db_session, *, club_code: str = "ASG") -> str:
    club = (await db_session.execute(select(Club).where(Club.code == club_code))).scalar_one()
    swimmer = Swimmer(
        firstName="Private",
        lastName="ViewTest",
        gender=Gender.M,
        dateOfBirth=date(1999, 5, 17),
        registrationNo=None,
        clubId=club.id,
    )
    db_session.add(swimmer)
    await db_session.commit()
    await db_session.refresh(swimmer)
    return swimmer.id


async def test_swimmer_results_empty_without_any_approved_claim(api_client):
    user = await register_verified_user(api_client)
    resp = await api_client.get("/api/v1/me/swimmer-results", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_swimmer_results_requires_bearer_token(api_client):
    resp = await api_client.get("/api/v1/me/swimmer-results")
    assert resp.status_code == 401


async def test_swimmer_results_shows_own_dob_after_approved_claim(api_client, db_session, admin_user):
    swimmer_id = await _make_swimmer(db_session)
    user = await register_verified_user(api_client)

    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer_id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    claim_id = create_resp.json()["id"]

    # Not visible yet - claim is still pending.
    still_pending = await api_client.get("/api/v1/me/swimmer-results", headers=auth_headers(user["access_token"]))
    assert still_pending.json() == []

    await api_client.post(
        f"/api/v1/claims/{claim_id}/approve", json={}, headers=auth_headers(admin_user["access_token"])
    )

    resp = await api_client.get("/api/v1/me/swimmer-results", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["swimmer"]["id"] == swimmer_id
    assert body[0]["swimmer"]["dateOfBirth"] == "1999-05-17"
    assert body[0]["results"] == []  # this synthetic swimmer has no results, just a claimed identity

    # DOB is the explicit allowlisted exception here - nothing else
    # (email/citizenship/address/registrationNo-when-null-is-fine-too)
    # should leak.
    assert_no_pii(body, allow=("dateOfBirth", "registrationNo"))


async def test_delete_me_revokes_refresh_tokens_and_writes_audit_row(api_client, db_session):
    user = await register_verified_user(api_client)

    resp = await api_client.delete("/api/v1/me", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    assert "administrator" in resp.json()["message"].lower()

    reuse = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert reuse.status_code == 401

    tokens = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.userId == user["id"])))
        .scalars()
        .all()
    )
    assert tokens and all(t.revokedAt is not None for t in tokens)
