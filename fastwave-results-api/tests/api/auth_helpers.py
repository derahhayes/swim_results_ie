"""Shared helpers for Step 5 API tests: register/verify/login a throwaway
user, or grant roles directly via the DB (there's no self-service way to
become admin/uploader/coach/swimmer without going through the app's own
approval flows or ADMIN_EMAILS bootstrap - tests set roles directly for
the "arrange" step, then exercise the real endpoints for the "act" step).

No fixture here truncates or resets state between tests - tests/api's
seeded_meets fixture is session-scoped, so every user created in one test
persists for the rest of the session. Every helper takes/generates a
unique email per call so tests never collide with each other.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import create_action_token
from app.models import User


def unique_email(label: str = "user") -> str:
    # NOT .test - email_validator (which pydantic's EmailStr uses) rejects
    # RFC 2606's other reserved TLDs (.test/.invalid/.localhost) as
    # "special-use", but example.com passes syntax validation fine.
    return f"{label}-{uuid.uuid4().hex[:12]}@example.com"


async def register_user(
    client: AsyncClient, *, email: str | None = None, password: str = "hunter2-pass", display_name: str = "Test User"
) -> dict:
    email = email or unique_email()
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password, "displayName": display_name}
    )
    assert resp.status_code == 201, resp.text
    return {"id": resp.json()["id"], "email": email, "password": password}


async def verify_email(client: AsyncClient, user_id: str) -> None:
    token = create_action_token(user_id, "email_verify")
    resp = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 200, resp.text


async def login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def register_verified_user(client: AsyncClient, **kwargs) -> dict:
    """Register + verify + log in; returns id/email/password/access_token/refresh_token."""
    user = await register_user(client, **kwargs)
    await verify_email(client, user["id"])
    tokens = await login(client, user["email"], user["password"])
    return {**user, **tokens}


def auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


async def grant_roles(db_session: AsyncSession, user_id: str, **roles: bool) -> None:
    user = await db_session.get(User, user_id)
    for field_name, value in roles.items():
        setattr(user, field_name, value)
    await db_session.commit()
