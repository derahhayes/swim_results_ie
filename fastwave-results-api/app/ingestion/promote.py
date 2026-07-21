"""ParsedHytekFile -> DB upserts, all within the caller's transaction.

Individual results are bulk-upserted via ON CONFLICT on the
ux_result_individual partial unique index (eventId, swimmerId, round WHERE
swimmerId IS NOT NULL). Relay results are upserted via ON CONFLICT on the
ux_result_relay partial unique index (eventId, clubId, relayTeamId, round
WHERE swimmerId IS NULL) - relayTeamId (the F1 relay team letter, e.g.
"A"/"B") is what distinguishes multiple relay teams from the same club in
one event/round and lets Postgres recognize a re-ingested relay result as
the same row rather than a duplicate. Splits and relay_legs are replace-all
per result on re-import, same as before.
"""

import json
from dataclasses import dataclass
from typing import Optional, Union

from hytek_parser.hy3.enums import ReplacedTimeTimeCode, WithTimeTimeCode
from hytek_parser.hy3.schemas import Event as HyEvent
from hytek_parser.hy3.schemas import Meet as HyMeet
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion._mappings import GENDER_MAP, STROKE_MAP, map_course, map_course_optional
from app.ingestion.matching import SwimmerResolution
from app.ingestion.report import ParseReport
from app.models.base import new_id, utcnow
from app.models.clubs import Club
from app.models.enums import Course, ResultStatus, Round
from app.models.meets import Meet, MeetEvent
from app.models.results import RelayLeg, Result, ResultSplit

ROUND_PREFIXES = (("prelim", Round.PRELIM, "P"), ("swimoff", Round.SWIMOFF, "S"), ("finals", Round.FINAL, "F"))

# asyncpg's wire protocol caps a single query at 32767 bound parameters. A
# batch of 500 rows stays comfortably under that even for the widest table
# here (results, ~23 columns/row -> 11500 params/batch).
CHUNK_SIZE = 500


def _chunks(rows: list[dict]) -> list[list[dict]]:
    return [rows[i : i + CHUNK_SIZE] for i in range(0, len(rows), CHUNK_SIZE)] or []

_STATUS_MAP = {
    WithTimeTimeCode.NORMAL: ResultStatus.OK,
    WithTimeTimeCode.NO_SHOW: ResultStatus.NS,
    WithTimeTimeCode.SCRATCH: ResultStatus.SCR,
    WithTimeTimeCode.DISQUALIFICATION: ResultStatus.DQ,
    WithTimeTimeCode.FALSE_START: ResultStatus.DQ,
    WithTimeTimeCode.DID_NOT_FINISH: ResultStatus.DNF,
}


def status_from_time_code(code: WithTimeTimeCode) -> ResultStatus:
    return _STATUS_MAP.get(code, ResultStatus.OK)


def place_or_none(value: Optional[int]) -> Optional[int]:
    """heatPlace/overallPlace as hytek-parser gives them, minus the 0-artifact.

    hytek_parser's safe_cast(int, ...) defaults to 0 (not None) when the
    HY3 field is blank - which it always is for NS/DQ/SCR rows, and a
    swim never actually finishes "in 0th place" on an OK row either. 0 is
    never a meaningful place, so it always means "no place", regardless
    of status.
    """
    if value is None or value <= 0:
        return None
    return value


def time_to_hundredths(value: Union[float, ReplacedTimeTimeCode, None]) -> Optional[int]:
    """Convert a hytek-parser time value to integer hundredths of a second.

    Non-numeric time codes (NT/NS/DNF/DQ/SCR/UNKNOWN) and missing values
    convert to None. So does 0.0: Hy-Tek writes "0.00" for unused timing
    slots (mirroring parse_time_or_none's treatment of backup timing
    fields) rather than leaving them blank, and a genuine zero swim time is
    not physically possible.
    """
    if not isinstance(value, float):
        return None
    if value <= 0.0:
        return None
    return int(round(value * 100))


def build_raw_blocks(lines: list[str]) -> dict[tuple, list[str]]:
    """Correlate raw E1/E2/F1/F2/F3/G1/H1/H2 lines to (kind, event, id, round).

    Individual key: ("IND", event_number, str(swimmer_meet_id), round_char).
    Relay key: ("RELAY", event_number, relay_team_id, round_char).
    """
    from hytek_parser._utils import extract

    blocks: dict[tuple, list[str]] = {}
    pending: list[str] = []
    open_key: Optional[tuple] = None
    current_kind: Optional[str] = None
    current_event_no: Optional[str] = None
    current_swimmer_id: Optional[str] = None
    current_relay_team_id: Optional[str] = None

    for line in lines:
        code = line[0:2]
        if code == "E1":
            current_kind = "IND"
            current_event_no = extract(line, 39, 4)
            current_swimmer_id = extract(line, 4, 5)
            pending.append(line)
        elif code == "F1":
            current_kind = "RELAY"
            current_event_no = extract(line, 39, 4)
            current_relay_team_id = extract(line, 8, 1)
            pending.append(line)
        elif code == "F3":
            # Real HY3 files put F3 (relay swimmer list) *after* F2, not
            # before (verified against hytek-parser's own relay fixtures) -
            # so it belongs to the block F2 just closed, not to `pending`
            # for the next one. Falls back to `pending` if F3 shows up
            # before F2 for some reason, rather than dropping it.
            if open_key is not None:
                blocks[open_key].append(line)
            else:
                pending.append(line)
        elif code in ("E2", "F2"):
            round_char = extract(line, 3, 1)
            key = (
                current_kind,
                current_event_no,
                current_swimmer_id if current_kind == "IND" else current_relay_team_id,
                round_char,
            )
            blocks[key] = pending + [line]
            pending = []
            open_key = key
        elif code in ("G1", "H1", "H2"):
            if open_key is not None:
                blocks[open_key].append(line)

    return blocks


def _raw_source_json(block_lines: Optional[list[str]], backup: dict) -> str:
    return json.dumps({"lines": block_lines, "backupTiming": backup})


def _backup_timing(entry, prefix: str) -> dict:
    return {
        "padTime": getattr(entry, f"{prefix}_pad_time"),
        "button1Time": getattr(entry, f"{prefix}_button_1_time"),
        "button2Time": getattr(entry, f"{prefix}_button_2_time"),
        "button3Time": getattr(entry, f"{prefix}_button_3_time"),
        "backup4Time": getattr(entry, f"{prefix}_backup_4_time"),
        "altTimeCode": getattr(entry, f"{prefix}_alt_time_code"),
    }


@dataclass
class _PendingResult:
    key: tuple
    row: dict
    splits: dict[int, float]
    relay_legs: Optional[list[tuple[int, str]]] = None


async def _upsert_meet(session: AsyncSession, hy_meet: HyMeet, report: ParseReport) -> tuple[str, Course]:
    meet_course = map_course(hy_meet.course, fallback=Course.LCM)
    if hy_meet.course.name in ("DQ", "UNKNOWN"):
        report.add_reject("meet_course_unmapped", raw=hy_meet.course.name)

    venue = hy_meet.facility.strip() or None
    stmt = pg_insert(Meet.__table__).values(
        id=new_id(),
        name=hy_meet.name,
        venue=venue,
        startDate=hy_meet.start_date,
        endDate=hy_meet.end_date,
        course=meet_course,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_meets_name_startDate",
        set_={
            "venue": stmt.excluded.venue,
            "endDate": stmt.excluded.endDate,
            "course": stmt.excluded.course,
            "updatedAt": utcnow(),
        },
    ).returning(Meet.__table__.c.id)
    meet_id = (await session.execute(stmt)).scalar_one()
    return meet_id, meet_course


async def _upsert_events(
    session: AsyncSession, meet_id: str, events: dict[str, HyEvent], meet_course: Course, report: ParseReport
) -> dict[str, str]:
    rows = []
    for number, ev in events.items():
        stroke = STROKE_MAP.get(ev.stroke)
        gender = GENDER_MAP.get(ev.gender)
        if stroke is None or gender is None:
            report.add_reject("event_unmapped", event=number, stroke=ev.stroke.name, gender=ev.gender.name)
            continue
        rows.append(
            dict(
                id=new_id(),
                meetId=meet_id,
                eventNo=number,
                distance=ev.distance,
                stroke=stroke,
                course=map_course(ev.course, fallback=meet_course),
                gender=gender,
                ageMin=ev.age_min,
                ageMax=ev.age_max,
                isRelay=ev.relay,
            )
        )
        report.events += 1

    if not rows:
        return {}

    stmt = pg_insert(MeetEvent.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_meet_events_meetId_eventNo",
        set_={
            "distance": stmt.excluded.distance,
            "stroke": stmt.excluded.stroke,
            "course": stmt.excluded.course,
            "gender": stmt.excluded.gender,
            "ageMin": stmt.excluded.ageMin,
            "ageMax": stmt.excluded.ageMax,
            "isRelay": stmt.excluded.isRelay,
            "updatedAt": utcnow(),
        },
    ).returning(MeetEvent.__table__.c.id, MeetEvent.__table__.c.eventNo)
    result = await session.execute(stmt)
    return {r.eventNo: r.id for r in result.all()}


def _build_individual_results(
    meet: HyMeet,
    meet_id: str,
    meet_course: Course,
    event_ids: dict[str, str],
    swimmer_resolutions: dict[int, SwimmerResolution],
    clubs_by_code: dict[str, Club],
    raw_blocks: dict[tuple, list[str]],
    report: ParseReport,
) -> list[_PendingResult]:
    pending: list[_PendingResult] = []

    for number, ev in meet.events.items():
        event_id = event_ids.get(number)
        if event_id is None:
            continue
        event_course = map_course(ev.course, fallback=meet_course)

        for entry in ev.entries:
            if entry.relay:
                continue

            hy_swimmer = entry.swimmers[1]
            resolution = swimmer_resolutions.get(hy_swimmer.meet_id)
            if resolution is None or resolution.swimmer is None:
                report.add_reject(
                    "result_excluded_pending_review", event=number, meetSwimmerId=hy_swimmer.meet_id
                )
                continue

            club = clubs_by_code[hy_swimmer.team_code]
            seed_hs = time_to_hundredths(entry.seed_time)
            seed_course = map_course_optional(entry.seed_course)

            for prefix, round_enum, round_char in ROUND_PREFIXES:
                time_val = getattr(entry, f"{prefix}_time")
                if time_val is None:
                    continue

                time_code = getattr(entry, f"{prefix}_time_code")
                status = status_from_time_code(time_code)
                time_hs = time_to_hundredths(time_val)
                if status in (ResultStatus.NS, ResultStatus.DNF):
                    time_hs = None

                dq_info = getattr(entry, f"{prefix}_dq_info")
                result_course = map_course_optional(getattr(entry, f"{prefix}_course")) or event_course

                block_key = ("IND", number, str(hy_swimmer.meet_id), round_char)
                block_lines = raw_blocks.get(block_key)

                key = (event_id, resolution.swimmer.id, round_enum)
                row = dict(
                    id=new_id(),
                    meetId=meet_id,
                    eventId=event_id,
                    swimmerId=resolution.swimmer.id,
                    clubId=club.id,
                    relayTeamId=None,
                    round=round_enum,
                    ageAtMeet=hy_swimmer.age,
                    seedTimeHs=seed_hs,
                    seedCourse=seed_course,
                    timeHs=time_hs,
                    course=result_course,
                    status=status,
                    isExhibition=entry.exhibition,
                    dqCode=dq_info.code.value if dq_info else None,
                    dqDescription=dq_info.info_str if dq_info else None,
                    dqDetail=dq_info.info_str_detail if dq_info else None,
                    heat=getattr(entry, f"{prefix}_heat"),
                    lane=getattr(entry, f"{prefix}_lane"),
                    heatPlace=place_or_none(getattr(entry, f"{prefix}_heat_place")),
                    overallPlace=place_or_none(getattr(entry, f"{prefix}_overall_place")),
                    points=None,
                    swimDate=getattr(entry, f"{prefix}_date"),
                    rawSource=_raw_source_json(block_lines, _backup_timing(entry, prefix)),
                )
                pending.append(_PendingResult(key=key, row=row, splits=getattr(entry, f"{prefix}_splits")))
                report.record_result(round_char)

    return pending


_RESULT_UPDATE_COLUMNS = [
    "ageAtMeet",
    "seedTimeHs",
    "seedCourse",
    "timeHs",
    "course",
    "status",
    "isExhibition",
    "dqCode",
    "dqDescription",
    "dqDetail",
    "heat",
    "lane",
    "heatPlace",
    "overallPlace",
    "points",
    "swimDate",
    "rawSource",
]


async def _upsert_individual_results(
    session: AsyncSession, pending: list[_PendingResult]
) -> dict[tuple, str]:
    if not pending:
        return {}

    id_by_key: dict[tuple, str] = {}
    for batch in _chunks([p.row for p in pending]):
        stmt = pg_insert(Result.__table__).values(batch)
        set_ = {col: getattr(stmt.excluded, col) for col in _RESULT_UPDATE_COLUMNS}
        set_["updatedAt"] = utcnow()
        stmt = stmt.on_conflict_do_update(
            # ux_result_individual is a partial unique *index*, not a named
            # constraint, so ON CONFLICT ON CONSTRAINT can't target it -
            # Postgres requires column + matching WHERE inference instead.
            index_elements=["eventId", "swimmerId", "round"],
            index_where=text('"swimmerId" IS NOT NULL'),
            set_=set_,
        ).returning(
            Result.__table__.c.id, Result.__table__.c.eventId, Result.__table__.c.swimmerId, Result.__table__.c.round
        )
        result = await session.execute(stmt)
        id_by_key.update({(r.eventId, r.swimmerId, r.round): r.id for r in result.all()})
    return id_by_key


async def _replace_splits(session: AsyncSession, result_id: str, splits: dict[int, float]) -> int:
    await session.execute(delete(ResultSplit.__table__).where(ResultSplit.__table__.c.resultId == result_id))
    if not splits:
        return 0
    rows = [
        dict(id=new_id(), resultId=result_id, splitNumber=num, cumulativeTimeHs=hs)
        for num, t in splits.items()
        if (hs := time_to_hundredths(t)) is not None
    ]
    await session.execute(pg_insert(ResultSplit.__table__).values(rows))
    return len(rows)


async def _bulk_replace_splits(
    session: AsyncSession, id_by_key: dict[tuple, str], pending: list[_PendingResult]
) -> int:
    """Clear + reinsert splits for every touched result in two round trips total.

    Doing this per-result (as _replace_splits does, used only on the small
    relay path) would mean thousands of round trips for a normal-sized meet.
    """
    touched_ids = [id_by_key[p.key] for p in pending if p.key in id_by_key]
    if touched_ids:
        await session.execute(
            delete(ResultSplit.__table__).where(ResultSplit.__table__.c.resultId.in_(touched_ids))
        )

    rows = []
    for p in pending:
        result_id = id_by_key.get(p.key)
        if result_id is None or not p.splits:
            continue
        for num, t in p.splits.items():
            hs = time_to_hundredths(t)
            if hs is None:
                # Same "0.00 means unrecorded" convention as everywhere else
                # in this file format - some G1 lines carry a real split
                # number with a zero/unrecorded time; cumulativeTimeHs is
                # NOT NULL, so there's nothing meaningful to store for it.
                continue
            rows.append(dict(id=new_id(), resultId=result_id, splitNumber=num, cumulativeTimeHs=hs))
    for batch in _chunks(rows):
        await session.execute(pg_insert(ResultSplit.__table__).values(batch))
    return len(rows)


async def _promote_relays(
    session: AsyncSession,
    meet: HyMeet,
    meet_id: str,
    meet_course: Course,
    event_ids: dict[str, str],
    swimmer_resolutions: dict[int, SwimmerResolution],
    clubs_by_code: dict[str, Club],
    raw_blocks: dict[tuple, list[str]],
    report: ParseReport,
) -> None:
    for number, ev in meet.events.items():
        event_id = event_ids.get(number)
        if event_id is None:
            continue
        event_course = map_course(ev.course, fallback=meet_course)

        for entry in ev.entries:
            if not entry.relay:
                continue

            club = clubs_by_code.get(entry.relay_swim_team_code)
            if club is None:
                report.add_reject("relay_team_unknown_club", event=number, team=entry.relay_swim_team_code)
                continue

            for prefix, round_enum, round_char in ROUND_PREFIXES:
                time_val = getattr(entry, f"{prefix}_time")
                if time_val is None:
                    continue

                time_code = getattr(entry, f"{prefix}_time_code")
                status = status_from_time_code(time_code)
                time_hs = time_to_hundredths(time_val)
                if status in (ResultStatus.NS, ResultStatus.DNF):
                    time_hs = None

                dq_info = getattr(entry, f"{prefix}_dq_info")
                result_course = map_course_optional(getattr(entry, f"{prefix}_course")) or event_course
                block_key = ("RELAY", number, entry.relay_team_id, round_char)
                block_lines = raw_blocks.get(block_key)

                row = dict(
                    id=new_id(),
                    meetId=meet_id,
                    eventId=event_id,
                    swimmerId=None,
                    clubId=club.id,
                    relayTeamId=entry.relay_team_id,
                    round=round_enum,
                    ageAtMeet=None,
                    seedTimeHs=time_to_hundredths(entry.seed_time),
                    seedCourse=map_course_optional(entry.seed_course),
                    timeHs=time_hs,
                    course=result_course,
                    status=status,
                    isExhibition=entry.exhibition,
                    dqCode=dq_info.code.value if dq_info else None,
                    dqDescription=dq_info.info_str if dq_info else None,
                    dqDetail=dq_info.info_str_detail if dq_info else None,
                    heat=getattr(entry, f"{prefix}_heat"),
                    lane=getattr(entry, f"{prefix}_lane"),
                    heatPlace=place_or_none(getattr(entry, f"{prefix}_heat_place")),
                    overallPlace=place_or_none(getattr(entry, f"{prefix}_overall_place")),
                    points=None,
                    swimDate=getattr(entry, f"{prefix}_date"),
                    rawSource=_raw_source_json(block_lines, _backup_timing(entry, prefix)),
                )

                stmt = pg_insert(Result.__table__).values(row)
                set_ = {col: getattr(stmt.excluded, col) for col in _RESULT_UPDATE_COLUMNS}
                set_["updatedAt"] = utcnow()
                stmt = stmt.on_conflict_do_update(
                    # ux_result_relay is a partial unique index, same
                    # index_elements/index_where inference reasoning as
                    # ux_result_individual above.
                    index_elements=["eventId", "clubId", "relayTeamId", "round"],
                    index_where=text('"swimmerId" IS NULL'),
                    set_=set_,
                ).returning(Result.__table__.c.id)
                result_id = (await session.execute(stmt)).scalar_one()
                report.record_result(round_char)

                await _replace_splits(session, result_id, getattr(entry, f"{prefix}_splits"))

                # Same resultId is reused across a re-ingest (upsert above),
                # so existing legs must be cleared first - otherwise a
                # changed relay lineup would leave stale leg rows behind
                # alongside the new ones instead of replacing them.
                await session.execute(delete(RelayLeg.__table__).where(RelayLeg.__table__.c.resultId == result_id))

                leg_rows = []
                for leg_num, hy_sw in entry.swimmers.items():
                    leg_resolution = swimmer_resolutions.get(hy_sw.meet_id)
                    if leg_resolution is None or leg_resolution.swimmer is None:
                        report.add_reject(
                            "relay_leg_excluded_pending_review", event=number, leg=leg_num
                        )
                        continue
                    leg_rows.append(
                        dict(
                            id=new_id(),
                            resultId=result_id,
                            swimmerId=leg_resolution.swimmer.id,
                            legOrder=leg_num,
                            legTimeHs=None,  # not reliably derivable from available HY3 fields; see KNOWN_ISSUES.md
                        )
                    )
                if leg_rows:
                    await session.execute(pg_insert(RelayLeg.__table__).values(leg_rows))
                    report.relay_legs += len(leg_rows)


async def promote(
    session: AsyncSession,
    meet: HyMeet,
    raw_lines: list[str],
    clubs_by_code: dict[str, Club],
    swimmer_resolutions: dict[int, SwimmerResolution],
    report: ParseReport,
) -> None:
    """Promote a fully-parsed meet into the DB. Caller owns the transaction."""
    meet_id, meet_course = await _upsert_meet(session, meet, report)
    event_ids = await _upsert_events(session, meet_id, meet.events, meet_course, report)
    raw_blocks = build_raw_blocks(raw_lines)

    pending = _build_individual_results(
        meet, meet_id, meet_course, event_ids, swimmer_resolutions, clubs_by_code, raw_blocks, report
    )
    id_by_key = await _upsert_individual_results(session, pending)
    report.splits += await _bulk_replace_splits(session, id_by_key, pending)

    await _promote_relays(
        session, meet, meet_id, meet_course, event_ids, swimmer_resolutions, clubs_by_code, raw_blocks, report
    )
