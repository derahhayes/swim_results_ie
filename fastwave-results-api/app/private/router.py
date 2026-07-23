"""GET /api/v1/me/swimmer-results, GET/CSV /api/v1/clubs/{id}/coach-view, DELETE /api/v1/me.

DOB/registrationNo are PII that never appear in app.schemas.public - these
routes are the explicit, gated exception: swimmer-results requires an
APPROVED swimmer_claims row for that swimmer; coach-view requires an
APPROVED coach_affiliations row for that club.
"""

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.results_common import build_result_rows, club_ref, display_name, result_sort_key
from app.audit import write_audit_log
from app.auth.deps import get_current_user
from app.auth.models import RefreshToken
from app.db import get_db
from app.models import Club, CoachAffiliation, RelayLeg, Result, Swimmer, SwimmerClaim, User
from app.models.enums import ClaimStatus
from app.schemas.auth import MessageResponse
from app.schemas.private import CoachViewResponse, CoachViewSwimmer, PrivateSwimmerDetail, SwimmerResultsBundle

router = APIRouter(prefix="/api/v1", tags=["private"])


async def _swimmer_results(session: AsyncSession, swimmer: Swimmer) -> list[Result]:
    individual = (
        (await session.execute(select(Result).where(Result.swimmerId == swimmer.id))).scalars().all()
    )
    relay = (
        (
            await session.execute(
                select(Result)
                .join(RelayLeg, RelayLeg.resultId == Result.id)
                .where(RelayLeg.swimmerId == swimmer.id)
            )
        )
        .scalars()
        .all()
    )
    return sorted([*individual, *relay], key=result_sort_key)


@router.get("/me/swimmer-results", response_model=list[SwimmerResultsBundle])
async def get_my_swimmer_results(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[SwimmerResultsBundle]:
    claims = (
        (
            await session.execute(
                select(SwimmerClaim).where(
                    SwimmerClaim.userId == user.id, SwimmerClaim.status == ClaimStatus.APPROVED
                )
            )
        )
        .scalars()
        .all()
    )
    if not claims:
        return []

    swimmer_ids = [c.swimmerId for c in claims]
    swimmers = (await session.execute(select(Swimmer).where(Swimmer.id.in_(swimmer_ids)))).scalars().all()
    clubs_by_id = {
        c.id: c
        for c in (
            await session.execute(select(Club).where(Club.id.in_({s.clubId for s in swimmers if s.clubId})))
        )
        .scalars()
        .all()
    }

    bundles = []
    for swimmer in swimmers:
        club = clubs_by_id[swimmer.clubId]
        results = await _swimmer_results(session, swimmer)
        rows_by_id = await build_result_rows(session, results)
        bundles.append(
            SwimmerResultsBundle(
                swimmer=PrivateSwimmerDetail(
                    id=swimmer.id,
                    displayName=display_name(swimmer),
                    gender=swimmer.gender,
                    dateOfBirth=swimmer.dateOfBirth,
                    registrationNo=swimmer.registrationNo,
                    club=club_ref(club),
                ),
                results=[rows_by_id[r.id] for r in results],
            )
        )
    return bundles


@router.get("/clubs/{clubId}/coach-view", response_model=CoachViewResponse)
async def get_coach_view(
    clubId: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> CoachViewResponse:
    club = await session.get(Club, clubId)
    if club is None:
        raise HTTPException(status_code=404, detail="Club not found")

    if not user.isAdmin:
        affiliation = (
            await session.execute(
                select(CoachAffiliation).where(
                    CoachAffiliation.userId == user.id,
                    CoachAffiliation.clubId == club.id,
                    CoachAffiliation.status == ClaimStatus.APPROVED,
                )
            )
        ).scalar_one_or_none()
        if affiliation is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="No approved coach affiliation for this club"
            )

    swimmers = (
        (
            await session.execute(
                select(Swimmer).where(Swimmer.clubId == club.id).order_by(Swimmer.lastName, Swimmer.firstName)
            )
        )
        .scalars()
        .all()
    )

    return CoachViewResponse(
        club=club_ref(club),
        swimmers=[
            CoachViewSwimmer(
                id=s.id,
                displayName=display_name(s),
                gender=s.gender,
                dateOfBirth=s.dateOfBirth,
                registrationNo=s.registrationNo,
            )
            for s in swimmers
        ],
    )


@router.get(
    "/clubs/{clubId}/coach-view/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def export_coach_view_csv(
    clubId: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    view = await get_coach_view(clubId, user, session)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "displayName", "gender", "dateOfBirth", "registrationNo"])
    for s in view.swimmers:
        writer.writerow(
            [s.id, s.displayName, s.gender.value, s.dateOfBirth.isoformat() if s.dateOfBirth else "", s.registrationNo or ""]
        )

    filename = f"{view.club.code}-coach-view.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/me", response_model=MessageResponse)
async def delete_my_account(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Soft account-deletion request (GDPR).

    No schema change is allowed here (Step 5 constraint) so there's no
    deletionRequestedAt column - the audit_log row *is* the durable record.
    An admin must action this manually for now; see README's account
    deletion section for the follow-up steps until an admin tool exists.
    """
    await write_audit_log(session, "user.deletion_requested", user.id, entity=f"users:{user.id}")

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
    return MessageResponse(message="Account deletion requested. An administrator will process this request.")
