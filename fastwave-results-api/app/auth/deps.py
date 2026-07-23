"""FastAPI auth dependencies: get_current_user, require_role, require_admin.

Roles are additive booleans on `users` (isAdmin/isCoach/isUploader/isSwimmer),
not a single role column - require_role checks the relevant flag. An admin
passes every require_role check too (allow_admin=True, the default):
admins can do everything a specific role can, without needing every flag
individually set.
"""

from typing import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.db import get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

ROLE_ATTRS = {
    "admin": "isAdmin",
    "coach": "isCoach",
    "uploader": "isUploader",
    "swimmer": "isSwimmer",
}

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_db)
) -> User:
    try:
        user_id = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise _CREDENTIALS_ERROR from exc

    user = await session.get(User, user_id)
    if user is None:
        raise _CREDENTIALS_ERROR
    return user


def require_role(role: str, allow_admin: bool = True) -> Callable:
    if role not in ROLE_ATTRS:
        raise ValueError(f"Unknown role: {role!r}")
    attr = ROLE_ATTRS[role]

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if getattr(user, attr, False):
            return user
        if allow_admin and user.isAdmin:
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires the '{role}' role")

    return dependency


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.isAdmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user
