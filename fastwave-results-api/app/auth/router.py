"""POST /api/v1/auth/* + GET /api/v1/users/me."""

from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import RefreshToken
from app.auth.security import (
    create_access_token,
    create_action_token,
    decode_action_token,
    hash_password,
    hash_token,
    new_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.db import get_db
from app.email import send_email
from app.models import CoachAffiliation, SwimmerClaim, User
from app.schemas.auth import (
    AffiliationSummary,
    ClaimSummary,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserMeResponse,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post("/auth/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_db)) -> User:
    email = body.email.lower()
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=email, passwordHash=hash_password(body.password), displayName=body.displayName)
    session.add(user)
    await session.flush()

    token = create_action_token(user.id, "email_verify")
    send_email(
        user.email,
        "verify_email",
        displayName=user.displayName or user.email,
        verify_url=f"/api/v1/auth/verify-email?token={token}",
    )

    await session.commit()
    return user


@router.post("/auth/verify-email", response_model=MessageResponse)
async def verify_email(body: VerifyEmailRequest, session: AsyncSession = Depends(get_db)) -> MessageResponse:
    try:
        user_id = decode_action_token(body.token, "email_verify")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    user.emailVerifiedAt = datetime.now(timezone.utc)
    await session.commit()
    return MessageResponse(message="Email verified")


async def _issue_tokens(session: AsyncSession, user_id: str) -> TokenResponse:
    access_token = create_access_token(user_id)
    raw_refresh = new_refresh_token()
    session.add(RefreshToken(userId=user_id, tokenHash=hash_token(raw_refresh), expiresAt=refresh_token_expiry()))
    await session.commit()
    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    # Plain JSON body, not OAuth2PasswordRequestForm's form-encoded
    # username/password - every other endpoint here is JSON, and a
    # frontend that POSTs JSON everywhere (Lovable's generated client did)
    # would otherwise hit this one endpoint's body as entirely empty.
    user = (
        await session.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.emailVerifiedAt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email not verified",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await _issue_tokens(session, user.id)


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    token_hash = hash_token(body.refresh_token)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.tokenHash == token_hash))
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if row is None or row.revokedAt is not None or row.expiresAt < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    row.revokedAt = now  # rotate: the presented token is single-use from here
    return await _issue_tokens(session, row.userId)


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(body: LogoutRequest, session: AsyncSession = Depends(get_db)) -> MessageResponse:
    token_hash = hash_token(body.refresh_token)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.tokenHash == token_hash))
    ).scalar_one_or_none()
    if row is not None and row.revokedAt is None:
        row.revokedAt = datetime.now(timezone.utc)
        await session.commit()
    return MessageResponse(message="Logged out")


@router.post("/auth/password-reset/request", response_model=MessageResponse)
async def password_reset_request(
    body: PasswordResetRequestRequest, session: AsyncSession = Depends(get_db)
) -> MessageResponse:
    user = (
        await session.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if user is not None:
        token = create_action_token(user.id, "password_reset")
        send_email(
            user.email,
            "password_reset",
            displayName=user.displayName or user.email,
            reset_url=f"/api/v1/auth/password-reset/confirm?token={token}",
        )
    # Same response whether or not the email is registered - don't leak existence.
    return MessageResponse(message="If that email is registered, a reset link has been sent")


@router.post("/auth/password-reset/confirm", response_model=MessageResponse)
async def password_reset_confirm(
    body: PasswordResetConfirmRequest, session: AsyncSession = Depends(get_db)
) -> MessageResponse:
    try:
        user_id = decode_action_token(body.token, "password_reset")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.passwordHash = hash_password(body.new_password)

    # A password reset ends every existing session, not just the one that
    # requested it.
    now = datetime.now(timezone.utc)
    outstanding = (
        (
            await session.execute(
                select(RefreshToken).where(RefreshToken.userId == user.id, RefreshToken.revokedAt.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for token_row in outstanding:
        token_row.revokedAt = now

    await session.commit()
    return MessageResponse(message="Password updated")


@router.get("/users/me", response_model=UserMeResponse)
async def get_me(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> UserMeResponse:
    claims = (await session.execute(select(SwimmerClaim).where(SwimmerClaim.userId == user.id))).scalars().all()
    affiliations = (
        (await session.execute(select(CoachAffiliation).where(CoachAffiliation.userId == user.id)))
        .scalars()
        .all()
    )

    return UserMeResponse(
        id=user.id,
        email=user.email,
        displayName=user.displayName,
        isSwimmer=user.isSwimmer,
        isCoach=user.isCoach,
        isUploader=user.isUploader,
        isAdmin=user.isAdmin,
        emailVerifiedAt=user.emailVerifiedAt,
        claims=[
            ClaimSummary(id=c.id, swimmerId=c.swimmerId, status=c.status.value, createdAt=c.createdAt)
            for c in claims
        ],
        affiliations=[
            AffiliationSummary(id=a.id, clubId=a.clubId, status=a.status.value, createdAt=a.createdAt)
            for a in affiliations
        ],
    )
