from app.auth.security import create_action_token
from tests.api.auth_helpers import (
    auth_headers,
    login,
    register_user,
    register_verified_user,
    unique_email,
    verify_email,
)


async def test_register_then_duplicate_email_is_409(api_client):
    email = unique_email("dup")
    first = await api_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "hunter2-pass", "displayName": "First"}
    )
    assert first.status_code == 201
    assert first.json()["email"] == email
    assert "password" not in first.json() and "passwordHash" not in first.json()

    second = await api_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "another-pass", "displayName": "Second"}
    )
    assert second.status_code == 409


async def test_register_accepts_name_as_alias_for_display_name(api_client):
    # Lovable-generated forms send "name", not "displayName" - the API
    # accepts either rather than depending on the frontend matching our
    # internal column name exactly (see the 422 this caused in practice).
    email = unique_email("namealias")
    resp = await api_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "hunter2-pass", "name": "Dermot Hayes"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["displayName"] == "Dermot Hayes"


async def test_login_before_verification_is_401(api_client):
    user = await register_user(api_client)
    resp = await api_client.post("/api/v1/auth/login", data={"username": user["email"], "password": user["password"]})
    assert resp.status_code == 401
    assert "not verified" in resp.json()["detail"].lower()


async def test_verify_email_with_wrong_purpose_token_is_400(api_client):
    user = await register_user(api_client)
    reset_token = create_action_token(user["id"], "password_reset")
    resp = await api_client.post("/api/v1/auth/verify-email", json={"token": reset_token})
    assert resp.status_code == 400


async def test_full_register_verify_login_me_flow(api_client):
    user = await register_verified_user(api_client)

    me = await api_client.get("/api/v1/users/me", headers=auth_headers(user["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == user["email"]
    assert body["isAdmin"] is False
    assert body["isSwimmer"] is False
    assert body["claims"] == []
    assert body["affiliations"] == []


async def test_login_is_case_insensitive_on_email(api_client):
    mixed_case_email = unique_email("MixedCase")
    user = await register_verified_user(api_client, email=mixed_case_email)
    resp = await login(api_client, mixed_case_email.upper(), user["password"])
    assert resp["access_token"]


async def test_login_wrong_password_is_401(api_client):
    user = await register_verified_user(api_client)
    resp = await api_client.post(
        "/api/v1/auth/login", data={"username": user["email"], "password": "totally-wrong"}
    )
    assert resp.status_code == 401


async def test_me_without_bearer_token_is_401(api_client):
    resp = await api_client.get("/api/v1/users/me")
    assert resp.status_code == 401


async def test_me_with_garbage_token_is_401(api_client):
    resp = await api_client.get("/api/v1/users/me", headers=auth_headers("not-a-real-jwt"))
    assert resp.status_code == 401


async def test_refresh_rotates_and_old_refresh_token_becomes_invalid(api_client):
    user = await register_verified_user(api_client)
    old_refresh = user["refresh_token"]

    refreshed = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    # Not asserting access_token != the original here - create_access_token
    # has no jti, so two tokens for the same user issued within the same
    # wall-clock second are byte-identical (same sub/type/iat/exp). Harmless
    # (equally valid either way) but not a meaningful thing to assert on.
    assert new_tokens["refresh_token"] != old_refresh

    reuse = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401

    still_good = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
    assert still_good.status_code == 200


async def test_refresh_with_unknown_token_is_401(api_client):
    resp = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": "does-not-exist"})
    assert resp.status_code == 401


async def test_logout_revokes_refresh_token(api_client):
    user = await register_verified_user(api_client)

    logout_resp = await api_client.post("/api/v1/auth/logout", json={"refresh_token": user["refresh_token"]})
    assert logout_resp.status_code == 200

    reuse = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert reuse.status_code == 401

    # Idempotent - logging out an already-revoked token is still a 200, not an error.
    again = await api_client.post("/api/v1/auth/logout", json={"refresh_token": user["refresh_token"]})
    assert again.status_code == 200


async def test_password_reset_request_does_not_leak_whether_email_exists(api_client):
    registered = await register_verified_user(api_client)
    resp_known = await api_client.post("/api/v1/auth/password-reset/request", json={"email": registered["email"]})
    resp_unknown = await api_client.post(
        "/api/v1/auth/password-reset/request", json={"email": unique_email("ghost")}
    )
    assert resp_known.status_code == resp_unknown.status_code == 200
    assert resp_known.json() == resp_unknown.json()


async def test_password_reset_confirm_changes_password_and_revokes_sessions(api_client):
    user = await register_verified_user(api_client)

    reset_token = create_action_token(user["id"], "password_reset")
    confirm = await api_client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": reset_token, "new_password": "brand-new-pass"}
    )
    assert confirm.status_code == 200

    # Every refresh token issued before the reset is now dead.
    stale_refresh = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert stale_refresh.status_code == 401

    old_password_login = await api_client.post(
        "/api/v1/auth/login", data={"username": user["email"], "password": user["password"]}
    )
    assert old_password_login.status_code == 401

    new_password_login = await api_client.post(
        "/api/v1/auth/login", data={"username": user["email"], "password": "brand-new-pass"}
    )
    assert new_password_login.status_code == 200


async def test_password_reset_confirm_with_expired_or_bad_token_is_400(api_client):
    resp = await api_client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": "garbage", "new_password": "whatever123"}
    )
    assert resp.status_code == 400


async def test_verify_email_endpoint_is_idempotent_safe_for_unknown_user(api_client):
    token = create_action_token("does-not-exist", "email_verify")
    resp = await api_client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 400
