"""Self-service /api/v1/claims + /api/v1/coach-affiliations, and the admin
decision/role-management endpoints layered on top of them.

No delegated club-level approvers for MVP (BRD, explicitly out of scope) -
every decision endpoint here is require_admin.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, pagination
from app.audit import write_audit_log
from app.auth.deps import get_current_user, require_admin
from app.db import get_db
from app.email import send_email
from app.models import Club, CoachAffiliation, Swimmer, SwimmerClaim, User
from app.models.enums import ClaimStatus
from app.schemas.claims import (
    AffiliationCreateRequest,
    AffiliationResponse,
    AffiliationsPage,
    ClaimCreateRequest,
    ClaimResponse,
    ClaimsPage,
    DecisionRequest,
    UserAdminView,
    UserRolesUpdateRequest,
    UsersPage,
)

router = APIRouter(prefix="/api/v1", tags=["claims"])

MIN_SELF_CLAIM_AGE = 16


def _age_on(dob: date, on: date) -> int:
    years = on.year - dob.year
    if (on.month, on.day) < (dob.month, dob.day):
        years -= 1
    return years


# --- self-service: swimmer claims ----------------------------------------


@router.post("/claims", response_model=ClaimResponse, status_code=status.HTTP_201_CREATED)
async def create_claim(
    body: ClaimCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> SwimmerClaim:
    swimmer = await session.get(Swimmer, body.swimmerId)
    if swimmer is None:
        raise HTTPException(status_code=404, detail="Swimmer not found")

    if body.relationship.strip().lower() == "self" and swimmer.dateOfBirth is not None:
        if _age_on(swimmer.dateOfBirth, date.today()) < MIN_SELF_CLAIM_AGE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Swimmers under {MIN_SELF_CLAIM_AGE} cannot be claimed as 'self' - "
                    "a parent/guardian should submit the claim instead."
                ),
            )

    existing = (
        await session.execute(
            select(SwimmerClaim).where(SwimmerClaim.userId == user.id, SwimmerClaim.swimmerId == swimmer.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Claim already exists for this swimmer")

    claim = SwimmerClaim(userId=user.id, swimmerId=swimmer.id, relationship_=body.relationship)
    session.add(claim)
    await session.flush()  # claim.id is only populated after this - needed for the audit entity string
    await write_audit_log(session, "claim.create", user.id, entity=f"swimmer_claims:{claim.id}")
    await session.commit()
    await session.refresh(claim)
    return claim


@router.get("/claims", response_model=ClaimsPage)
async def list_claims(
    status_filter: str | None = None,
    page_params: PageParams = Depends(pagination),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> ClaimsPage:
    query = select(SwimmerClaim)
    count_query = select(func.count()).select_from(SwimmerClaim)
    if status_filter:
        try:
            wanted = ClaimStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Unknown status {status_filter!r}") from exc
        query = query.where(SwimmerClaim.status == wanted)
        count_query = count_query.where(SwimmerClaim.status == wanted)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(SwimmerClaim.createdAt).offset(page_params.offset).limit(page_params.pageSize)
            )
        )
        .scalars()
        .all()
    )
    return ClaimsPage(page=page_params.page, pageSize=page_params.pageSize, total=total, items=rows)


async def _decide_claim(
    claim_id: str, approve: bool, reason: str | None, admin: User, session: AsyncSession
) -> SwimmerClaim:
    claim = await session.get(SwimmerClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.status != ClaimStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Claim is already {claim.status.value}")

    claim.status = ClaimStatus.APPROVED if approve else ClaimStatus.REJECTED
    claim.decidedBy = admin.id
    claim.decidedAt = datetime.now(timezone.utc)
    claim.reason = reason

    claimant = await session.get(User, claim.userId)
    if approve and claimant is not None:
        claimant.isSwimmer = True

    await write_audit_log(
        session,
        "claim.approve" if approve else "claim.reject",
        admin.id,
        entity=f"swimmer_claims:{claim.id}",
        detail={"reason": reason} if reason else None,
    )
    await session.commit()
    await session.refresh(claim)

    if claimant is not None:
        swimmer = await session.get(Swimmer, claim.swimmerId)
        send_email(
            claimant.email,
            "claim_approved" if approve else "claim_rejected",
            displayName=claimant.displayName or claimant.email,
            swimmerName=f"{swimmer.firstName} {swimmer.lastName}" if swimmer else "the swimmer",
            reason=reason or "",
        )
    return claim


@router.post("/claims/{claim_id}/approve", response_model=ClaimResponse)
async def approve_claim(
    claim_id: str,
    body: DecisionRequest = DecisionRequest(),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> SwimmerClaim:
    return await _decide_claim(claim_id, True, body.reason, admin, session)


@router.post("/claims/{claim_id}/reject", response_model=ClaimResponse)
async def reject_claim(
    claim_id: str,
    body: DecisionRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> SwimmerClaim:
    if not body.reason:
        raise HTTPException(status_code=422, detail="reason is required to reject a claim")
    return await _decide_claim(claim_id, False, body.reason, admin, session)


# --- self-service: coach affiliations ------------------------------------


@router.post(
    "/coach-affiliations", response_model=AffiliationResponse, status_code=status.HTTP_201_CREATED
)
async def create_affiliation(
    body: AffiliationCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> CoachAffiliation:
    club = await session.get(Club, body.clubId)
    if club is None:
        raise HTTPException(status_code=404, detail="Club not found")

    existing = (
        await session.execute(
            select(CoachAffiliation).where(
                CoachAffiliation.userId == user.id, CoachAffiliation.clubId == club.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Affiliation already exists for this club")

    affiliation = CoachAffiliation(userId=user.id, clubId=club.id)
    session.add(affiliation)
    await session.flush()  # affiliation.id is only populated after this - needed for the audit entity string
    await write_audit_log(session, "affiliation.create", user.id, entity=f"coach_affiliations:{affiliation.id}")
    await session.commit()
    await session.refresh(affiliation)
    return affiliation


@router.get("/coach-affiliations", response_model=AffiliationsPage)
async def list_affiliations(
    status_filter: str | None = None,
    page_params: PageParams = Depends(pagination),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> AffiliationsPage:
    query = select(CoachAffiliation)
    count_query = select(func.count()).select_from(CoachAffiliation)
    if status_filter:
        try:
            wanted = ClaimStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Unknown status {status_filter!r}") from exc
        query = query.where(CoachAffiliation.status == wanted)
        count_query = count_query.where(CoachAffiliation.status == wanted)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(CoachAffiliation.createdAt)
                .offset(page_params.offset)
                .limit(page_params.pageSize)
            )
        )
        .scalars()
        .all()
    )
    return AffiliationsPage(page=page_params.page, pageSize=page_params.pageSize, total=total, items=rows)


async def _decide_affiliation(
    affiliation_id: str, approve: bool, reason: str | None, admin: User, session: AsyncSession
) -> CoachAffiliation:
    affiliation = await session.get(CoachAffiliation, affiliation_id)
    if affiliation is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    if affiliation.status != ClaimStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Affiliation is already {affiliation.status.value}")

    affiliation.status = ClaimStatus.APPROVED if approve else ClaimStatus.REJECTED
    affiliation.decidedBy = admin.id
    affiliation.decidedAt = datetime.now(timezone.utc)

    coach = await session.get(User, affiliation.userId)
    if approve and coach is not None:
        coach.isCoach = True

    await write_audit_log(
        session,
        "affiliation.approve" if approve else "affiliation.reject",
        admin.id,
        entity=f"coach_affiliations:{affiliation.id}",
        detail={"reason": reason} if reason else None,
    )
    await session.commit()
    await session.refresh(affiliation)

    if coach is not None:
        club = await session.get(Club, affiliation.clubId)
        send_email(
            coach.email,
            "affiliation_approved" if approve else "affiliation_rejected",
            displayName=coach.displayName or coach.email,
            clubName=club.name if club else "the club",
            reason=reason or "",
        )
    return affiliation


@router.post("/coach-affiliations/{affiliation_id}/approve", response_model=AffiliationResponse)
async def approve_affiliation(
    affiliation_id: str,
    body: DecisionRequest = DecisionRequest(),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> CoachAffiliation:
    return await _decide_affiliation(affiliation_id, True, body.reason, admin, session)


@router.post("/coach-affiliations/{affiliation_id}/reject", response_model=AffiliationResponse)
async def reject_affiliation(
    affiliation_id: str,
    body: DecisionRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> CoachAffiliation:
    if not body.reason:
        raise HTTPException(status_code=422, detail="reason is required to reject an affiliation")
    return await _decide_affiliation(affiliation_id, False, body.reason, admin, session)


# --- admin: users + roles -------------------------------------------------


@router.get("/users", response_model=UsersPage)
async def list_users(
    page_params: PageParams = Depends(pagination),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> UsersPage:
    total = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    rows = (
        (
            await session.execute(
                select(User).order_by(User.createdAt).offset(page_params.offset).limit(page_params.pageSize)
            )
        )
        .scalars()
        .all()
    )
    return UsersPage(page=page_params.page, pageSize=page_params.pageSize, total=total, items=rows)


@router.patch("/users/{user_id}/roles", response_model=UserAdminView)
async def update_user_roles(
    user_id: str,
    body: UserRolesUpdateRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> User:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    changes = body.model_dump(exclude_unset=True)
    for field_name, value in changes.items():
        setattr(target, field_name, value)

    await write_audit_log(session, "user.roles_update", admin.id, entity=f"users:{target.id}", detail=changes)
    await session.commit()
    await session.refresh(target)
    return target
