"""HTTP publish/unpublish - supersedes Step 3's CLI-only workflow (app.cli
still works too, untouched). Uses a fresh throwaway meet uploaded via the
Step 5 HTTP upload flow rather than touching seeded_meets's shared,
session-scoped main/relay meets - those are relied on by every other
tests/api file with no per-test reset, so toggling their published state
here would leak into unrelated tests running later in the same session.
"""

import random
import uuid

import pytest_asyncio
from sqlalchemy import select

from app.models import Meet
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user
from tests.ingestion.relay_fixture import build_synthetic_relay_hy3


@pytest_asyncio.fixture
async def admin_user(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isAdmin=True)
    return user


@pytest_asyncio.fixture
async def fresh_unpublished_meet(api_client, db_session, seeded_meets):
    uploader = await register_verified_user(api_client)
    await grant_roles(db_session, uploader["id"], isUploader=True)

    base = random.randint(100000, 999999)
    ids = [str(base + i) for i in range(12)]
    suffix = uuid.uuid4().hex[:8]
    meet_name = f"Throwaway Publish-Test Meet {suffix}"
    raw = build_synthetic_relay_hy3(ids[0:4], ids[4:8], ids[8:12], meet_name=meet_name, name_prefix=f"Pub{suffix}")

    resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("throwaway.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp.status_code == 201, resp.text

    meet = (await db_session.execute(select(Meet).where(Meet.name == meet_name))).scalar_one()
    assert meet.publishedAt is None
    return meet.id


async def test_unpublished_meet_is_404_on_public_api(api_client, fresh_unpublished_meet):
    resp = await api_client.get(f"/api/v1/meets/{fresh_unpublished_meet}")
    assert resp.status_code == 404


async def test_publish_makes_meet_visible_publicly(api_client, admin_user, fresh_unpublished_meet):
    publish_resp = await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/publish", headers=auth_headers(admin_user["access_token"])
    )
    assert publish_resp.status_code == 200
    body = publish_resp.json()
    assert body["id"] == fresh_unpublished_meet
    assert body["publishedAt"] is not None

    public_resp = await api_client.get(f"/api/v1/meets/{fresh_unpublished_meet}")
    assert public_resp.status_code == 200
    assert public_resp.headers.get("etag")


async def test_unpublish_hides_meet_again(api_client, admin_user, fresh_unpublished_meet):
    await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/publish", headers=auth_headers(admin_user["access_token"])
    )
    visible = await api_client.get(f"/api/v1/meets/{fresh_unpublished_meet}")
    assert visible.status_code == 200

    unpublish_resp = await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/unpublish", headers=auth_headers(admin_user["access_token"])
    )
    assert unpublish_resp.status_code == 200
    assert unpublish_resp.json()["publishedAt"] is None

    hidden = await api_client.get(f"/api/v1/meets/{fresh_unpublished_meet}")
    assert hidden.status_code == 404


async def test_publish_etag_changes_on_republish(api_client, admin_user, fresh_unpublished_meet):
    await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/publish", headers=auth_headers(admin_user["access_token"])
    )
    first = await api_client.get(f"/api/v1/meets/{fresh_unpublished_meet}")
    first_etag = first.headers["etag"]

    not_modified = await api_client.get(
        f"/api/v1/meets/{fresh_unpublished_meet}", headers={"If-None-Match": first_etag}
    )
    assert not_modified.status_code == 304

    await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/unpublish", headers=auth_headers(admin_user["access_token"])
    )
    await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/publish", headers=auth_headers(admin_user["access_token"])
    )

    second = await api_client.get(f"/api/v1/meets/{fresh_unpublished_meet}")
    # publishedAt moved forward on republish - the meet-scoped ETag is
    # derived from it, so a stale client's cached ETag no longer matches.
    assert second.headers["etag"] != first_etag


async def test_publish_requires_admin(api_client, fresh_unpublished_meet):
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/publish", headers=auth_headers(user["access_token"])
    )
    assert resp.status_code == 403


async def test_unpublish_requires_admin(api_client, admin_user, fresh_unpublished_meet):
    await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/publish", headers=auth_headers(admin_user["access_token"])
    )
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        f"/api/v1/meets/{fresh_unpublished_meet}/unpublish", headers=auth_headers(user["access_token"])
    )
    assert resp.status_code == 403


async def test_publish_unknown_meet_is_404(api_client, admin_user):
    resp = await api_client.post(
        "/api/v1/meets/does-not-exist/publish", headers=auth_headers(admin_user["access_token"])
    )
    assert resp.status_code == 404
