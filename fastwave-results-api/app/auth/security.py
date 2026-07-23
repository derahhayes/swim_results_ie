"""Password hashing (Argon2id) + JWT access/refresh/action tokens.

Two different kinds of token, deliberately:

- Access tokens are short-lived, self-contained JWTs (no DB lookup needed
  to validate one - just signature + expiry).
- Refresh tokens are opaque random strings; the raw value is only ever
  handed to the client, and only its SHA-256 hash is stored (in
  refresh_tokens.tokenHash) - that's what makes them individually
  revocable/rotatable, which a stateless JWT alone can't do without a
  denylist table.

Action tokens (email verification, password reset) are a third, smaller
case: signed JWTs with a `purpose` claim and short expiry, self-contained
for the same reason refresh tokens *aren't* - this step's schema is
frozen except for refresh_tokens, so there's no table to track "already
used" action tokens in. Expiry is the only safety net; see the docstring
on create_action_token.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.config import get_settings

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    now = _now()
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Returns the user id (sub claim). Raises jwt.PyJWTError on any problem."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload["sub"]


def new_refresh_token() -> str:
    """Raw opaque refresh token - callers store hash_token(this), never this itself."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    return _now() + timedelta(days=get_settings().jwt_refresh_token_expire_days)


_ACTION_TOKEN_TTL = {
    "email_verify": timedelta(hours=24),
    "password_reset": timedelta(hours=1),
}


def create_action_token(user_id: str, purpose: str) -> str:
    """Stateless signed token for email verification / password reset.

    Not single-use-enforced - there's no table to record "this one's been
    consumed" without violating this step's schema constraint (only
    refresh_tokens is a new table). Expiry (24h / 1h) is the only
    protection. Acceptable for MVP; worth a denylist table if this ever
    matters more precisely.
    """
    if purpose not in _ACTION_TOKEN_TTL:
        raise ValueError(f"Unknown action token purpose: {purpose!r}")
    settings = get_settings()
    now = _now()
    payload = {
        "sub": user_id,
        "type": "action",
        "purpose": purpose,
        "iat": now,
        "exp": now + _ACTION_TOKEN_TTL[purpose],
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_action_token(token: str, expected_purpose: str) -> str:
    """Returns the user id (sub claim). Raises jwt.PyJWTError on any problem."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "action" or payload.get("purpose") != expected_purpose:
        raise jwt.InvalidTokenError("Token purpose mismatch")
    return payload["sub"]
