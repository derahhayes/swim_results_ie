from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, get_published_meet, pagination
from app.api.v1.results_common import build_result_rows, result_sort_key
from app.db import get_db
from app.models import Club, Meet, MeetEvent, RelayLeg, Result
from app.schemas.public import (
    ClubEventGroup,
    ClubResultsResponse,
    MeetClubSummary,
    MeetCounts,
    MeetDetail,
    MeetEventSummary,
    MeetListPage,
    MeetRef,
    MeetSummary,
)

router = APIRouter(tags=["meets"])


async def _meet_counts(session: AsyncSession, meet_ids: list[str]) -> dict[str, MeetCounts]:
    if not meet_ids:
        return {}

    event_counts = dict(
        (
            await session.execute(
                select(MeetEvent.meetId, func.count())
                .where(MeetEvent.meetId.in_(meet_ids))
                .group_by(MeetEvent.meetId)
            )
        ).all()
    )

    club_counts = dict(
        (
            await session.execute(
                select(Result.meetId, func.count(func.distinct(Result.clubId)))
                .where(Result.meetId.in_(meet_ids))
                .group_by(Result.meetId)
            )
        ).all()
    )

    # A swimmer "at the meet" is anyone with an individual result OR a relay
    # leg there - counted once each, hence the UNION + outer distinct rather
    # than two separately-counted queries.
    individual = select(Result.meetId.label("meetId"), Result.swimmerId.label("swimmerId")).where(
        Result.meetId.in_(meet_ids), Result.swimmerId.isnot(None)
    )
    relay = (
        select(Result.meetId.label("meetId"), RelayLeg.swimmerId.label("swimmerId"))
        .select_from(RelayLeg)
        .join(Result, RelayLeg.resultId == Result.id)
        .where(Result.meetId.in_(meet_ids))
    )
    swimmers_union = individual.union(relay).subquery()
    swimmer_counts = dict(
        (
            await session.execute(
                select(swimmers_union.c.meetId, func.count(func.distinct(swimmers_union.c.swimmerId))).group_by(
                    swimmers_union.c.meetId
                )
            )
        ).all()
    )

    return {
        mid: MeetCounts(
            eventCount=event_counts.get(mid, 0),
            swimmerCount=swimmer_counts.get(mid, 0),
            clubCount=club_counts.get(mid, 0),
        )
        for mid in meet_ids
    }


@router.get("/meets", response_model=MeetListPage)
async def list_meets(
    page_params: PageParams = Depends(pagination),
    session: AsyncSession = Depends(get_db),
) -> MeetListPage:
    published = select(Meet).where(Meet.publishedAt.isnot(None))

    total = (await session.execute(select(func.count()).select_from(published.subquery()))).scalar_one()

    meets = (
        (
            await session.execute(
                published.order_by(Meet.startDate.desc()).offset(page_params.offset).limit(page_params.pageSize)
            )
        )
        .scalars()
        .all()
    )

    counts = await _meet_counts(session, [m.id for m in meets])
    empty_counts = MeetCounts(eventCount=0, swimmerCount=0, clubCount=0)

    items = [
        MeetSummary(
            id=m.id,
            name=m.name,
            venue=m.venue,
            startDate=m.startDate,
            endDate=m.endDate,
            course=m.course,
            counts=counts.get(m.id, empty_counts),
        )
        for m in meets
    ]

    return MeetListPage(items=items, total=total, page=page_params.page, pageSize=page_params.pageSize)


@router.get("/meets/{meetId}", response_model=MeetDetail)
async def get_meet(
    meet: Meet = Depends(get_published_meet),
    session: AsyncSession = Depends(get_db),
) -> MeetDetail:
    events = (
        (await session.execute(select(MeetEvent).where(MeetEvent.meetId == meet.id).order_by(MeetEvent.eventNo)))
        .scalars()
        .all()
    )

    result_counts: dict[str, int] = {}
    if events:
        event_ids = [e.id for e in events]
        result_counts = dict(
            (
                await session.execute(
                    select(Result.eventId, func.count()).where(Result.eventId.in_(event_ids)).group_by(Result.eventId)
                )
            ).all()
        )

    return MeetDetail(
        id=meet.id,
        name=meet.name,
        venue=meet.venue,
        startDate=meet.startDate,
        endDate=meet.endDate,
        course=meet.course,
        events=[
            MeetEventSummary(
                id=e.id,
                eventNo=e.eventNo,
                distance=e.distance,
                stroke=e.stroke,
                course=e.course,
                gender=e.gender,
                ageMin=e.ageMin,
                ageMax=e.ageMax,
                isRelay=e.isRelay,
                resultCount=result_counts.get(e.id, 0),
            )
            for e in events
        ],
    )


@router.get("/meets/{meetId}/clubs", response_model=list[MeetClubSummary])
async def list_meet_clubs(
    meet: Meet = Depends(get_published_meet),
    session: AsyncSession = Depends(get_db),
) -> list[MeetClubSummary]:
    rows = (
        await session.execute(
            select(Club.code, Club.name, func.count().label("resultCount"))
            .join(Result, Result.clubId == Club.id)
            .where(Result.meetId == meet.id)
            .group_by(Club.id, Club.code, Club.name)
            .order_by(Club.name)
        )
    ).all()

    return [MeetClubSummary(code=r.code, name=r.name, resultCount=r.resultCount) for r in rows]


@router.get("/meets/{meetId}/clubs/{clubCode}/results", response_model=ClubResultsResponse)
async def get_club_results(
    clubCode: str,
    meet: Meet = Depends(get_published_meet),
    session: AsyncSession = Depends(get_db),
) -> ClubResultsResponse:
    club = (await session.execute(select(Club).where(Club.code == clubCode))).scalar_one_or_none()
    if club is None:
        raise HTTPException(status_code=404, detail="Club not found")

    results = (
        (await session.execute(select(Result).where(Result.meetId == meet.id, Result.clubId == club.id)))
        .scalars()
        .all()
    )
    if not results:
        raise HTTPException(status_code=404, detail="Club not found in this meet")

    results = sorted(results, key=result_sort_key)
    rows_by_id = await build_result_rows(session, results)

    event_ids = {r.eventId for r in results}
    events_by_id = {
        e.id: e for e in (await session.execute(select(MeetEvent).where(MeetEvent.id.in_(event_ids)))).scalars().all()
    }

    grouped: dict[str, list] = {}
    for r in results:
        grouped.setdefault(r.eventId, []).append(rows_by_id[r.id])

    event_groups = [
        ClubEventGroup(
            event=MeetEventSummary(
                id=e.id,
                eventNo=e.eventNo,
                distance=e.distance,
                stroke=e.stroke,
                course=e.course,
                gender=e.gender,
                ageMin=e.ageMin,
                ageMax=e.ageMax,
                isRelay=e.isRelay,
                resultCount=len(grouped[e.id]),
            ),
            results=grouped[e.id],
        )
        for e in sorted(events_by_id.values(), key=lambda e: e.eventNo)
    ]

    return ClubResultsResponse(
        meet=MeetRef(id=meet.id, name=meet.name, startDate=meet.startDate, course=meet.course),
        club=MeetClubSummary(code=club.code, name=club.name, resultCount=len(results)),
        events=event_groups,
    )
