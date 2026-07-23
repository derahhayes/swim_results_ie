"""Every privileged action writes exactly one audit_log row (BRD): claim/
affiliation decisions, uploads, publish/unpublish, match-review
resolution, role grants, admin bootstrap, account-deletion requests.
"""

import random
from datetime import date

import pytest_asyncio
from sqlalchemy import func, select

from app.auth.bootstrap import bootstrap_admins
from app.models import AuditLog, Club, Meet, Swimmer, User
from app.models.enums import Gender
from tests.api.auth_helpers import auth_headers, grant_roles, register_verified_user
from tests.ingestion.relay_fixture import build_synthetic_relay_hy3


@pytest_asyncio.fixture
async def admin_user(api_client, db_session):
    user = await register_verified_user(api_client)
    await grant_roles(db_session, user["id"], isAdmin=True)
    return user


async def _audit_rows(db_session, action: str, entity: str) -> list[AuditLog]:
    return (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == action, AuditLog.entity == entity)
            )
        )
        .scalars()
        .all()
    )


async def test_claim_create_and_approve_each_write_one_audit_row(api_client, db_session, admin_user):
    asg = (await db_session.execute(select(Club).where(Club.code == "ASG"))).scalar_one()
    swimmer = Swimmer(firstName="Audit", lastName="Test1", gender=Gender.M, dateOfBirth=date(1990, 1, 1), clubId=asg.id)
    db_session.add(swimmer)
    await db_session.commit()
    await db_session.refresh(swimmer)

    user = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/claims",
        json={"swimmerId": swimmer.id, "relationship": "self"},
        headers=auth_headers(user["access_token"]),
    )
    claim_id = create_resp.json()["id"]

    create_rows = await _audit_rows(db_session, "claim.create", f"swimmer_claims:{claim_id}")
    assert len(create_rows) == 1
    assert create_rows[0].userId == user["id"]

    await api_client.post(
        f"/api/v1/claims/{claim_id}/approve", json={}, headers=auth_headers(admin_user["access_token"])
    )
    approve_rows = await _audit_rows(db_session, "claim.approve", f"swimmer_claims:{claim_id}")
    assert len(approve_rows) == 1
    assert approve_rows[0].userId == admin_user["id"]


async def test_affiliation_reject_writes_one_audit_row_with_reason(api_client, db_session, admin_user):
    asg = (await db_session.execute(select(Club).where(Club.code == "ASG"))).scalar_one()
    user = await register_verified_user(api_client)
    create_resp = await api_client.post(
        "/api/v1/coach-affiliations", json={"clubId": asg.id}, headers=auth_headers(user["access_token"])
    )
    affiliation_id = create_resp.json()["id"]

    await api_client.post(
        f"/api/v1/coach-affiliations/{affiliation_id}/reject",
        json={"reason": "Not recognised"},
        headers=auth_headers(admin_user["access_token"]),
    )
    rows = await _audit_rows(db_session, "affiliation.reject", f"coach_affiliations:{affiliation_id}")
    assert len(rows) == 1
    assert '"Not recognised"' in rows[0].detail


async def test_upload_create_writes_one_audit_row(api_client, db_session, seeded_meets):
    uploader = await register_verified_user(api_client)
    await grant_roles(db_session, uploader["id"], isUploader=True)

    base = random.randint(1000000, 9999999)
    ids = [str(base + i) for i in range(12)]
    raw = build_synthetic_relay_hy3(
        ids[0:4], ids[4:8], ids[8:12], name_prefix=f"Aud{base}", meet_name=f"Audit Upload Meet {base}"
    )
    resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("audit.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    upload_id = resp.json()["id"]

    rows = await _audit_rows(db_session, "upload.create", f"uploads:{upload_id}")
    assert len(rows) == 1
    assert rows[0].userId == uploader["id"]


async def test_publish_and_unpublish_each_write_one_audit_row(api_client, db_session, admin_user, seeded_meets):
    uploader = await register_verified_user(api_client)
    await grant_roles(db_session, uploader["id"], isUploader=True)
    base = random.randint(1000000, 9999999)
    ids = [str(base + i) for i in range(12)]
    raw = build_synthetic_relay_hy3(
        ids[0:4], ids[4:8], ids[8:12], name_prefix=f"AudPub{base}", meet_name=f"Audit Publish Meet {base}"
    )
    upload_resp = await api_client.post(
        "/api/v1/uploads",
        files={"file": ("publish-audit.hy3", raw, "application/octet-stream")},
        headers=auth_headers(uploader["access_token"]),
    )
    assert upload_resp.status_code == 201, upload_resp.text

    meet = (
        await db_session.execute(select(Meet).where(Meet.name == f"Audit Publish Meet {base}"))
    ).scalar_one()

    await api_client.post(f"/api/v1/meets/{meet.id}/publish", headers=auth_headers(admin_user["access_token"]))
    publish_rows = await _audit_rows(db_session, "meet.publish", f"meets:{meet.id}")
    assert len(publish_rows) == 1
    assert publish_rows[0].userId == admin_user["id"]

    await api_client.post(f"/api/v1/meets/{meet.id}/unpublish", headers=auth_headers(admin_user["access_token"]))
    unpublish_rows = await _audit_rows(db_session, "meet.unpublish", f"meets:{meet.id}")
    assert len(unpublish_rows) == 1


async def test_user_roles_update_writes_one_audit_row(api_client, db_session, admin_user):
    target = await register_verified_user(api_client)
    await api_client.patch(
        f"/api/v1/users/{target['id']}/roles",
        json={"isUploader": True},
        headers=auth_headers(admin_user["access_token"]),
    )
    rows = await _audit_rows(db_session, "user.roles_update", f"users:{target['id']}")
    assert len(rows) == 1
    assert rows[0].userId == admin_user["id"]
    assert '"isUploader": true' in rows[0].detail


async def test_deletion_request_writes_one_audit_row(api_client, db_session):
    user = await register_verified_user(api_client)
    await api_client.delete("/api/v1/me", headers=auth_headers(user["access_token"]))
    rows = await _audit_rows(db_session, "user.deletion_requested", f"users:{user['id']}")
    assert len(rows) == 1
    assert rows[0].userId == user["id"]


async def test_admin_bootstrap_writes_one_audit_row_and_is_idempotent(api_client, db_session, monkeypatch):
    user = await register_verified_user(api_client)

    from app.auth import bootstrap as bootstrap_module

    class _FakeSettings:
        admin_emails_list = [user["email"]]

    monkeypatch.setattr(bootstrap_module, "get_settings", lambda: _FakeSettings())

    await bootstrap_admins()
    await bootstrap_admins()  # idempotent - second call is a no-op, no extra row

    rows = await _audit_rows(db_session, "admin.bootstrap", f"users:{user['id']}")
    assert len(rows) == 1

    refreshed = await db_session.get(User, user["id"])
    assert refreshed.isAdmin is True
