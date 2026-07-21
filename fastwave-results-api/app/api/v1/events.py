from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_published_event
from app.api.v1.results_common import build_result_rows, result_sort_key
from app.db import get_db
from app.models import Meet, MeetEvent, Result
from app.models.enums import Round
from app.schemas.public import EventResultsResponse, MeetEventSummary, MeetRef, RoundResults

router = APIRouter(tags=["events"])


@router.get("/events/{eventId}/results", response_model=EventResultsResponse)
async def get_event_results(
    event_and_meet: tuple[MeetEvent, Meet] = Depends(get_published_event),
    session: AsyncSession = Depends(get_db),
) -> EventResultsResponse:
    event, meet = event_and_meet

    results = (await session.execute(select(Result).where(Result.eventId == event.id))).scalars().all()
    results = sorted(results, key=result_sort_key)

    rows_by_id = await build_result_rows(session, results)

    # Dict insertion order follows `results`' order, which is already
    # round-first (see result_sort_key) - grouping this way naturally
    # yields FINAL, then SWIMOFF, then PRELIM without a second sort.
    grouped: dict[Round, list] = {}
    for r in results:
        grouped.setdefault(r.round, []).append(rows_by_id[r.id])

    return EventResultsResponse(
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
            resultCount=len(results),
        ),
        rounds=[RoundResults(round=round_, results=rows) for round_, rows in grouped.items()],
    )
