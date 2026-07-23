"""End-to-end match-review resolution: an ambiguous swimmer match creates a
match_reviews row and gets excluded from promotion; resolving it re-runs
process_upload so the swimmer's results actually appear and the upload's
status can flip from needs_review back to promoted.
"""

import random
from datetime import date

import pytest_asyncio
from sqlalchemy import select

from app.models import Club, Swimmer
from app.models.enums import Gender
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user
from tests.ingestion.relay_fixture import build_synthetic_relay_hy3


@pytest_asyncio.fixture
async def uploader(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isUploader=True)
    return user


@pytest_asyncio.fixture
async def ambiguous_upload(api_client, uploader, seeded_meets, db_session):
    """Creates a name+DOB collision with a swimmer in a different club, then
    uploads a synthetic relay file whose first swimmer matches it exactly -
    ambiguous (right name/DOB, wrong club), so it lands in match_reviews
    instead of being auto-matched or silently created as a new swimmer.
    """
    # Randomized meet_ids *and* name prefix so every fixture invocation is
    # distinct: reusing the same file bytes across invocations would make
    # receive_upload's dedup check return a *previous* test's upload/review
    # instead of a fresh one (nothing truncates the DB between tests in
    # this session-scoped-seed suite), and reusing "First0"/"Last0" (the
    # relay builder's default position-0 name) would collide with
    # seeded_meets's relay fixture and tests/api/test_relay.py's
    # `firstName == "First0"` scalar_one() lookup.
    base = random.randint(10000, 99999)
    prefix = f"Ambig{base}"
    ids = [str(base + i) for i in range(12)]

    asg = (await db_session.execute(select(Club).where(Club.code == "ASG"))).scalar_one()
    conflicting = Swimmer(
        firstName=f"{prefix}First0",
        lastName=f"{prefix}Last0",
        gender=Gender.M,
        dateOfBirth=date(2000, 1, 1),
        clubId=asg.id,
    )
    db_session.add(conflicting)
    await db_session.commit()
    await db_session.refresh(conflicting)

    # Swimmer name/DOB always come from list *position* (index 0 -> the
    # first "{prefix}Last0"/"{prefix}First0"), not from the id value, so
    # this exactly matches the conflicting swimmer above. meet_name is
    # also unique per invocation - reusing the default would upsert
    # relay results into seeded_meets's shared relay meet instead of a
    # meet of this test's own (promote.py upserts meets on (name,
    # startDate) and relay results on (eventId, clubId, relayTeamId,
    # round), so that would silently steal seeded_meets's relay legs).
    raw = build_synthetic_relay_hy3(
        ids[0:4], ids[4:8], ids[8:12], name_prefix=prefix, meet_name=f"Match Review Test Meet {base}"
    )
    create_resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("ambiguous.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert create_resp.status_code == 201, create_resp.text
    upload_id = create_resp.json()["id"]

    detail = await api_client.get(f"/api/v1/uploads/{upload_id}", headers=auth_headers(uploader["access_token"]))
    assert detail.json()["status"] == "needs_review", detail.json()

    reviews_resp = await api_client.get(
        f"/api/v1/uploads/{upload_id}/match-reviews", headers=auth_headers(uploader["access_token"])
    )
    reviews = reviews_resp.json()
    assert len(reviews) == 1
    review = reviews[0]
    assert review["sourceData"]["firstName"] == f"{prefix}First0"
    assert review["sourceData"]["lastName"] == f"{prefix}Last0"
    assert review["resolvedAt"] is None

    return {"upload_id": upload_id, "review_id": review["id"], "conflicting_swimmer_id": conflicting.id}


async def test_ambiguous_swimmer_excluded_pending_review(ambiguous_upload):
    # All assertions live in the fixture itself - this test just exercises
    # the fixture's setup path and documents what it's asserting on.
    assert ambiguous_upload["review_id"]


async def test_non_owner_cannot_resolve_match_review(api_client, ambiguous_upload, db_session):
    other = await register_verified_user(api_client)
    await grant_roles(db_session, other["id"], isUploader=True)

    resp = await api_client.post(
        f"/api/v1/match-reviews/{ambiguous_upload['review_id']}/resolve",
        json={"swimmerId": ambiguous_upload["conflicting_swimmer_id"]},
        headers=auth_headers(other["access_token"]),
    )
    assert resp.status_code == 403


async def test_resolve_unknown_swimmer_is_404(api_client, uploader, ambiguous_upload):
    resp = await api_client.post(
        f"/api/v1/match-reviews/{ambiguous_upload['review_id']}/resolve",
        json={"swimmerId": "does-not-exist"},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp.status_code == 404


async def test_resolve_review_reprocesses_upload_to_promoted(api_client, uploader, ambiguous_upload):
    resolve_resp = await api_client.post(
        f"/api/v1/match-reviews/{ambiguous_upload['review_id']}/resolve",
        json={"swimmerId": ambiguous_upload["conflicting_swimmer_id"]},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resolve_resp.status_code == 200, resolve_resp.text
    body = resolve_resp.json()
    assert body["review"]["resolvedSwimmerId"] == ambiguous_upload["conflicting_swimmer_id"]
    assert body["review"]["resolvedAt"] is not None
    assert body["uploadStatus"] == "promoted"

    detail = await api_client.get(
        f"/api/v1/uploads/{ambiguous_upload['upload_id']}", headers=auth_headers(uploader["access_token"])
    )
    assert detail.json()["status"] == "promoted"


async def test_resolve_already_resolved_review_is_409(api_client, uploader, ambiguous_upload):
    first = await api_client.post(
        f"/api/v1/match-reviews/{ambiguous_upload['review_id']}/resolve",
        json={"swimmerId": ambiguous_upload["conflicting_swimmer_id"]},
        headers=auth_headers(uploader["access_token"]),
    )
    assert first.status_code == 200

    second = await api_client.post(
        f"/api/v1/match-reviews/{ambiguous_upload['review_id']}/resolve",
        json={"swimmerId": ambiguous_upload["conflicting_swimmer_id"]},
        headers=auth_headers(uploader["access_token"]),
    )
    assert second.status_code == 409


async def test_resolve_unknown_review_is_404(api_client, uploader):
    resp = await api_client.post(
        "/api/v1/match-reviews/does-not-exist/resolve",
        json={"swimmerId": "also-does-not-exist"},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp.status_code == 404
