from pathlib import Path

import pytest_asyncio
from sqlalchemy import func, select

from app.models import Meet, Upload
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user
from tests.ingestion.relay_fixture import build_synthetic_relay_hy3

FIXTURE = Path(__file__).parent.parent / "fixtures" / "michael_bowles_2026.hy3"


@pytest_asyncio.fixture
async def uploader(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isUploader=True)
    return user


@pytest_asyncio.fixture
async def admin_user(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isAdmin=True)
    return user


async def test_upload_requires_uploader_role(api_client):
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("meet.hy3", b"whatever", "application/octet-stream")},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 403


async def test_upload_rejects_non_hy3_extension(api_client, uploader):
    resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("meet.txt", b"whatever", "text/plain")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp.status_code == 422


async def test_upload_new_file_processes_and_promotes(api_client, uploader, seeded_meets, db_session):
    # meet_name/name_prefix both need to be unique per synthetic upload:
    # promote.py upserts meets on (name, startDate) and relay results on
    # (eventId, clubId, relayTeamId, round), so reusing the default meet
    # name here would silently steal seeded_meets's relay legs over to
    # this upload's swimmers instead of creating an independent meet.
    raw = build_synthetic_relay_hy3(
        ["101", "102", "103", "104"],
        ["105", "106", "107", "108"],
        ["109", "110", "111", "112"],
        name_prefix="Up1",
        meet_name="Upload Test Meet 1",
    )

    resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("fresh-relay.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    upload = resp.json()
    upload_id = upload["id"]

    # httpx's ASGITransport runs Starlette BackgroundTasks synchronously,
    # in-process, before the client call returns - so by the time we get
    # the response back, process_upload has already finished.
    detail = await api_client.get(f"/api/v1/uploads/{upload_id}", headers=auth_headers(uploader["access_token"]))
    assert detail.status_code == 200
    assert detail.json()["status"] == "promoted"
    # The only way to discover which meet an upload produced (so an admin
    # can find and publish it) - previously always null, since promote()
    # discarded the meetId it computed instead of returning it.
    meet_id = detail.json()["meetId"]
    assert meet_id

    meet_row = await db_session.get(Meet, meet_id)
    assert meet_row is not None
    assert meet_row.name == "Upload Test Meet 1"


async def test_upload_duplicate_hash_is_idempotent(api_client, uploader, seeded_meets, db_session):
    raw = FIXTURE.read_bytes()

    resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("michael_bowles_2026.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp.status_code == 201
    upload = resp.json()
    assert upload["status"] == "promoted"  # already promoted by seeded_meets at session start

    count = (
        await db_session.execute(select(func.count()).select_from(Upload).where(Upload.fileSha256.isnot(None)))
    ).scalar_one()

    resp2 = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("michael_bowles_2026.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert resp2.status_code == 201
    assert resp2.json()["id"] == upload["id"]

    count_after = (
        await db_session.execute(select(func.count()).select_from(Upload).where(Upload.fileSha256.isnot(None)))
    ).scalar_one()
    assert count_after == count


async def test_get_upload_requires_ownership_or_admin(api_client, uploader, admin_user, db_session):
    raw = build_synthetic_relay_hy3(
        ["201", "202", "203", "204"],
        ["205", "206", "207", "208"],
        ["209", "210", "211", "212"],
        name_prefix="Up2",
        meet_name="Upload Test Meet 2",
    )
    create_resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("owned.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    upload_id = create_resp.json()["id"]

    # Also an uploader (not just any authenticated user) - so this actually
    # exercises the ownership check, not just the require_role("uploader") gate.
    other_uploader = await register_verified_user(api_client)
    await grant_roles(db_session, other_uploader["id"], isUploader=True)

    forbidden = await api_client.get(
        f"/api/v1/uploads/{upload_id}", headers=auth_headers(other_uploader["access_token"])
    )
    assert forbidden.status_code == 403

    allowed_admin = await api_client.get(
        f"/api/v1/uploads/{upload_id}", headers=auth_headers(admin_user["access_token"])
    )
    assert allowed_admin.status_code == 200


async def test_get_unknown_upload_is_404(api_client, uploader):
    resp = await api_client.get("/api/v1/uploads/does-not-exist", headers=auth_headers(uploader["access_token"]))
    assert resp.status_code == 404


async def test_list_uploads_scoped_to_owner_unless_admin(api_client, uploader, admin_user, db_session):
    raw = build_synthetic_relay_hy3(
        ["301", "302", "303", "304"],
        ["305", "306", "307", "308"],
        ["309", "310", "311", "312"],
        name_prefix="Up3",
        meet_name="Upload Test Meet 3",
    )
    create_resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("list-scope.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    upload_id = create_resp.json()["id"]

    other_uploader = await register_verified_user(api_client)
    await grant_roles(db_session, other_uploader["id"], isUploader=True)

    own_list = await api_client.get("/api/v1/uploads", headers=auth_headers(uploader["access_token"]))
    assert own_list.status_code == 200
    assert any(u["id"] == upload_id for u in own_list.json()["items"])

    other_list = await api_client.get("/api/v1/uploads", headers=auth_headers(other_uploader["access_token"]))
    assert all(u["id"] != upload_id for u in other_list.json()["items"])

    admin_list = await api_client.get("/api/v1/uploads", headers=auth_headers(admin_user["access_token"]))
    assert any(u["id"] == upload_id for u in admin_list.json()["items"])
