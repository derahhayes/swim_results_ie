"""Shared result-row assembly for /events/{id}/results and /meets/{id}/clubs/{code}/results.

Both endpoints return the exact same EventResultRow shape (one result row,
individual or relay), just grouped differently (by round vs. by event) -
this module is the one place that builds those rows, so the GDPR
projection and the ranking/ordering rule only need to be right once.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, RelayLeg, Result, ResultSplit, Swimmer
from app.models.enums import ResultStatus, Round
from app.schemas.public import (
    EventResultRow,
    PublicClubRef,
    PublicSwimmer,
    RelayLegRow,
    RelayTeamRow,
    ResultSwimmer,
    SplitRow,
)
from app.utils.times import format_time_hs

# "ranked swims by overallPlace/time, then DQ, then NS/SCR" - DNF buckets
# with DQ (swam but no valid time counted), NS/SCR never started at all.
ROUND_ORDER = {Round.FINAL: 0, Round.SWIMOFF: 1, Round.PRELIM: 2}
STATUS_TIER = {
    ResultStatus.OK: 0,
    ResultStatus.EXH: 0,
    ResultStatus.DQ: 1,
    ResultStatus.DNF: 1,
    ResultStatus.NS: 2,
    ResultStatus.SCR: 2,
}
_SENTINEL = 10**9


def result_sort_key(r: Result) -> tuple:
    return (
        ROUND_ORDER.get(r.round, 99),
        STATUS_TIER.get(r.status, 9),
        r.overallPlace if r.overallPlace is not None else _SENTINEL,
        r.timeHs if r.timeHs is not None else _SENTINEL,
    )


def display_name(swimmer: Swimmer) -> str:
    if swimmer.isAnonymised:
        return "Name withheld"
    if swimmer.preferredName:
        return swimmer.preferredName
    return f"{swimmer.firstName} {swimmer.lastName}"


def club_ref(club: Club) -> PublicClubRef:
    return PublicClubRef(code=club.code, name=club.name)


def _public_swimmer(swimmer: Swimmer, club: Club) -> PublicSwimmer:
    return PublicSwimmer(id=swimmer.id, displayName=display_name(swimmer), gender=swimmer.gender, club=club_ref(club))


def _result_swimmer(swimmer: Swimmer, club: Club, age_at_meet: int | None) -> ResultSwimmer:
    return ResultSwimmer(
        id=swimmer.id,
        displayName=display_name(swimmer),
        gender=swimmer.gender,
        club=club_ref(club),
        ageAtMeet=age_at_meet,
    )


def build_split_rows(splits: list[ResultSplit]) -> list[SplitRow]:
    rows = []
    prev_cumulative = 0
    for s in sorted(splits, key=lambda s: s.splitNumber):
        delta_hs = s.cumulativeTimeHs - prev_cumulative
        rows.append(
            SplitRow(
                splitNumber=s.splitNumber,
                cumulativeTimeHs=s.cumulativeTimeHs,
                cumulativeTime=format_time_hs(s.cumulativeTimeHs),
                deltaHs=delta_hs,
                delta=format_time_hs(delta_hs),
            )
        )
        prev_cumulative = s.cumulativeTimeHs
    return rows


async def build_result_rows(session: AsyncSession, results: list[Result]) -> dict[str, EventResultRow]:
    """Build EventResultRow objects for the given Result rows, keyed by result id.

    Batches every swimmer/club/split/leg lookup regardless of how many
    results are passed in - no N+1 queries.
    """
    if not results:
        return {}

    result_ids = [r.id for r in results]

    club_ids = {r.clubId for r in results}
    clubs_by_id = {
        c.id: c for c in (await session.execute(select(Club).where(Club.id.in_(club_ids)))).scalars().all()
    }

    individual_swimmer_ids = {r.swimmerId for r in results if r.swimmerId is not None}
    swimmers_by_id: dict[str, Swimmer] = {}
    if individual_swimmer_ids:
        swimmers_by_id = {
            s.id: s
            for s in (
                await session.execute(select(Swimmer).where(Swimmer.id.in_(individual_swimmer_ids)))
            )
            .scalars()
            .all()
        }

    splits_by_result: dict[str, list[ResultSplit]] = defaultdict(list)
    for s in (
        (await session.execute(select(ResultSplit).where(ResultSplit.resultId.in_(result_ids)))).scalars().all()
    ):
        splits_by_result[s.resultId].append(s)

    relay_result_ids = [r.id for r in results if r.swimmerId is None]
    legs_by_result: dict[str, list[RelayLeg]] = defaultdict(list)
    leg_swimmers_by_id: dict[str, Swimmer] = {}
    if relay_result_ids:
        legs = (
            (await session.execute(select(RelayLeg).where(RelayLeg.resultId.in_(relay_result_ids))))
            .scalars()
            .all()
        )
        leg_swimmer_ids = {leg.swimmerId for leg in legs}
        for leg in legs:
            legs_by_result[leg.resultId].append(leg)
        if leg_swimmer_ids:
            leg_swimmers_by_id = {
                s.id: s
                for s in (await session.execute(select(Swimmer).where(Swimmer.id.in_(leg_swimmer_ids))))
                .scalars()
                .all()
            }

    rows: dict[str, EventResultRow] = {}
    for r in results:
        # The result's own clubId - the club represented *at this meet* -
        # not the swimmer's current club, which may have changed since via
        # a transfer. Used for both the individual swimmer and every relay
        # leg, since a relay's legs all swim for the one team regardless of
        # where each swimmer is registered today.
        club = clubs_by_id[r.clubId]
        splits = build_split_rows(splits_by_result.get(r.id, []))

        swimmer_row = None
        relay_row = None
        if r.swimmerId is not None:
            swimmer_row = _result_swimmer(swimmers_by_id[r.swimmerId], club, r.ageAtMeet)
        else:
            legs = sorted(legs_by_result.get(r.id, []), key=lambda leg: leg.legOrder)
            leg_rows = [
                RelayLegRow(
                    swimmer=_public_swimmer(leg_swimmers_by_id[leg.swimmerId], club),
                    legOrder=leg.legOrder,
                    legTimeHs=leg.legTimeHs,
                    legTime=format_time_hs(leg.legTimeHs),
                )
                for leg in legs
            ]
            label_name = club.shortName or club.name
            relay_row = RelayTeamRow(label=f"{label_name} — {r.relayTeamId}", legs=leg_rows)

        rows[r.id] = EventResultRow(
            id=r.id,
            swimmer=swimmer_row,
            relayTeam=relay_row,
            club=club_ref(club),
            round=r.round,
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
            splits=splits,
        )

    return rows
