from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, pagination
from app.api.v1.results_common import build_split_rows, display_name, result_sort_key
from app.db import get_db
from app.models import Club, Meet, MeetEvent, RelayLeg, Result, ResultSplit, Swimmer
from app.models.enums import Course, Stroke
from app.schemas.public import (
    MeetEventSummary,
    MeetRef,
    PublicClubRef,
    SwimmerDetail,
    SwimmerResultPage,
    SwimmerResultRow,
    SwimmerSearchResponse,
    SwimmerSearchResult,
)
from app.utils.season import season_bounds, season_label
from app.utils.times import format_time_hs

router = APIRouter(tags=["swimmers"])

_NAME_EXPR = Swimmer.lastName + " " + Swimmer.firstName  # matches ix_swimmers_name_trgm exactly


@router.get("/swimmers/search", response_model=SwimmerSearchResponse)
async def search_swimmers(
    q: str = Query(..., min_length=3, max_length=100),
    session: AsyncSession = Depends(get_db),
) -> SwimmerSearchResponse:
    # Session-local GUC: makes the word_similarity threshold explicit rather
    # than relying on the server default (which happens to also be 0.3).
    #
    # word_similarity (not plain similarity/`%`) is what actually makes a
    # short typo'd query like "barri" find "Barry Benjamin": plain
    # similarity() compares against the *whole* "lastName firstName"
    # string, so a short query gets diluted by the extra name component
    # (similarity('Barry Benjamin', 'barri') = 0.25, under threshold).
    # word_similarity finds the best-matching substring instead
    # (word_similarity('barri', 'Barry Benjamin') = 0.67) - and its `<%`
    # operator still uses the same trgm GIN index (verified with EXPLAIN).
    await session.execute(text("SET pg_trgm.word_similarity_threshold = 0.3"))

    has_published_individual_result = (
        select(Result.id)
        .join(Meet, Result.meetId == Meet.id)
        .where(Result.swimmerId == Swimmer.id, Meet.publishedAt.isnot(None))
        .exists()
    )
    has_published_relay_leg = (
        select(RelayLeg.id)
        .join(Result, RelayLeg.resultId == Result.id)
        .join(Meet, Result.meetId == Meet.id)
        .where(RelayLeg.swimmerId == Swimmer.id, Meet.publishedAt.isnot(None))
        .exists()
    )

    word_similarity_expr = func.word_similarity(q, _NAME_EXPR)

    stmt = (
        select(Swimmer, Club, word_similarity_expr.label("sim"))
        .join(Club, Swimmer.clubId == Club.id)
        .where(
            Swimmer.isAnonymised.is_(False),
            or_(has_published_individual_result, has_published_relay_leg),
            or_(_NAME_EXPR.ilike(f"%{q}%"), literal(q).op("<%")(_NAME_EXPR.self_group())),
        )
        .order_by(word_similarity_expr.desc())
        .limit(25)
    )

    rows = (await session.execute(stmt)).all()
    items = [
        SwimmerSearchResult(
            id=s.id, displayName=display_name(s), gender=s.gender, club=PublicClubRef(code=c.code, name=c.name)
        )
        for s, c, _sim in rows
    ]
    return SwimmerSearchResponse(items=items)


@router.get("/swimmers/{swimmerId}", response_model=SwimmerDetail)
async def get_swimmer(swimmerId: str, session: AsyncSession = Depends(get_db)) -> SwimmerDetail:
    swimmer = await session.get(Swimmer, swimmerId)
    if swimmer is None:
        raise HTTPException(status_code=404, detail="Swimmer not found")

    # Result.id is included (and not just swimDate/meetId) so that UNION's
    # implicit DISTINCT doesn't collapse multiple same-day results at the
    # same meet into one row - a one-day meet is the common case, and
    # swimDate+meetId alone can't tell two of that swimmer's results apart.
    individual = (
        select(Result.id, Result.swimDate, Result.meetId)
        .join(Meet, Result.meetId == Meet.id)
        .where(Result.swimmerId == swimmerId, Meet.publishedAt.isnot(None))
    )
    relay = (
        select(Result.id, Result.swimDate, Result.meetId)
        .select_from(RelayLeg)
        .join(Result, RelayLeg.resultId == Result.id)
        .join(Meet, Result.meetId == Meet.id)
        .where(RelayLeg.swimmerId == swimmerId, Meet.publishedAt.isnot(None))
    )
    rows = (await session.execute(individual.union(relay))).all()

    if not rows:
        # No published-meet participation at all - don't distinguish this
        # from "swimmerId doesn't exist".
        raise HTTPException(status_code=404, detail="Swimmer not found")

    seasons = sorted({season_label(d) for _, d, _ in rows if d is not None}, reverse=True)
    meet_ids = {mid for _, _, mid in rows}

    club_ref = None
    if swimmer.clubId:
        club = await session.get(Club, swimmer.clubId)
        if club is not None:
            club_ref = PublicClubRef(code=club.code, name=club.name)

    return SwimmerDetail(
        id=swimmer.id,
        displayName=display_name(swimmer),
        gender=swimmer.gender,
        club=club_ref,
        seasonsActive=seasons,
        resultCount=len(rows),
        meetCount=len(meet_ids),
    )


@router.get("/swimmers/{swimmerId}/results", response_model=SwimmerResultPage)
async def get_swimmer_results(
    swimmerId: str,
    stroke: Optional[Stroke] = Query(None),
    distance: Optional[int] = Query(None),
    course: Optional[Course] = Query(None),
    season: Optional[str] = Query(None, description='Irish season label, e.g. "2025/26"'),
    page_params: PageParams = Depends(pagination),
    session: AsyncSession = Depends(get_db),
) -> SwimmerResultPage:
    swimmer = await session.get(Swimmer, swimmerId)
    if swimmer is None:
        raise HTTPException(status_code=404, detail="Swimmer not found")

    season_range: Optional[tuple] = None
    if season is not None:
        try:
            season_range = season_bounds(season)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _apply_filters(stmt):
        if stroke is not None:
            stmt = stmt.where(MeetEvent.stroke == stroke)
        if distance is not None:
            stmt = stmt.where(MeetEvent.distance == distance)
        if course is not None:
            stmt = stmt.where(MeetEvent.course == course)
        if season_range is not None:
            stmt = stmt.where(Result.swimDate >= season_range[0], Result.swimDate <= season_range[1])
        return stmt

    individual_ids = (
        select(Result.id)
        .join(MeetEvent, Result.eventId == MeetEvent.id)
        .join(Meet, Result.meetId == Meet.id)
        .where(Result.swimmerId == swimmerId, Meet.publishedAt.isnot(None))
    )
    individual_ids = _apply_filters(individual_ids)

    relay_ids = (
        select(Result.id)
        .select_from(RelayLeg)
        .join(Result, RelayLeg.resultId == Result.id)
        .join(MeetEvent, Result.eventId == MeetEvent.id)
        .join(Meet, Result.meetId == Meet.id)
        .where(RelayLeg.swimmerId == swimmerId, Meet.publishedAt.isnot(None))
    )
    relay_ids = _apply_filters(relay_ids)

    combined = individual_ids.union(relay_ids).subquery()

    total = (await session.execute(select(func.count()).select_from(combined))).scalar_one()

    page_ids = (
        (
            await session.execute(
                select(Result.id, Result.swimDate)
                .join(combined, Result.id == combined.c.id)
                .order_by(Result.swimDate.desc().nulls_last(), Result.id)
                .offset(page_params.offset)
                .limit(page_params.pageSize)
            )
        )
        .scalars()
        .all()
    )

    if not page_ids:
        return SwimmerResultPage(items=[], total=total, page=page_params.page, pageSize=page_params.pageSize)

    results = (await session.execute(select(Result).where(Result.id.in_(page_ids)))).scalars().all()
    results_by_id = {r.id: r for r in results}
    results_ordered = [results_by_id[rid] for rid in page_ids]

    event_ids = {r.eventId for r in results}
    events_by_id = {
        e.id: e for e in (await session.execute(select(MeetEvent).where(MeetEvent.id.in_(event_ids)))).scalars().all()
    }
    meet_ids = {r.meetId for r in results}
    meets_by_id = {
        m.id: m for m in (await session.execute(select(Meet).where(Meet.id.in_(meet_ids)))).scalars().all()
    }

    splits_by_result: dict[str, list[ResultSplit]] = {}
    for s in (
        (await session.execute(select(ResultSplit).where(ResultSplit.resultId.in_(page_ids)))).scalars().all()
    ):
        splits_by_result.setdefault(s.resultId, []).append(s)

    relay_result_ids = [r.id for r in results if r.swimmerId is None]
    leg_order_by_result: dict[str, int] = {}
    if relay_result_ids:
        legs = (
            await session.execute(
                select(RelayLeg).where(RelayLeg.resultId.in_(relay_result_ids), RelayLeg.swimmerId == swimmerId)
            )
        ).scalars().all()
        leg_order_by_result = {leg.resultId: leg.legOrder for leg in legs}

    items = []
    for r in results_ordered:
        event = events_by_id[r.eventId]
        meet = meets_by_id[r.meetId]
        items.append(
            SwimmerResultRow(
                id=r.id,
                meet=MeetRef(id=meet.id, name=meet.name, startDate=meet.startDate, course=meet.course),
                event=MeetEventSummary(
                    id=event.id,
                    eventNo=event.eventNo,
                    distance=event.distance,
                    stroke=event.stroke,
                    course=event.course,
                    gender=event.gender,
                    ageMin=event.ageMin,
                    ageMax=event.ageMax,
                    isRelay=event.isRelay,
                    resultCount=0,  # not meaningful in a single-swimmer listing
                ),
                round=r.round,
                isRelay=r.swimmerId is None,
                relayTeamId=r.relayTeamId,
                legOrder=leg_order_by_result.get(r.id),
                heat=r.heat,
                lane=r.lane,
                heatPlace=r.heatPlace,
                overallPlace=r.overallPlace,
                seedTimeHs=r.seedTimeHs,
                seedTime=format_time_hs(r.seedTimeHs),
                seedCourse=r.seedCourse,
                timeHs=r.timeHs,
                time=format_time_hs(r.timeHs),
                status=r.status,
                isExhibition=r.isExhibition,
                dqCode=r.dqCode,
                dqDescription=r.dqDescription,
                points=r.points,
                splits=build_split_rows(splits_by_result.get(r.id, [])),
            )
        )

    return SwimmerResultPage(items=items, total=total, page=page_params.page, pageSize=page_params.pageSize)
