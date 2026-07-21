"""Club + swimmer identity resolution against the DB.

Runs as bulk pre-fetches (one query per lookup key across the whole meet)
rather than per-swimmer round trips, since a meet can have hundreds of
swimmers and this all happens inside a single ingestion transaction.
"""

import json
from collections import defaultdict
from dataclasses import dataclass

from hytek_parser.hy3.schemas import Swimmer as HySwimmer
from hytek_parser.hy3.schemas import Team as HyTeam
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion._mappings import GENDER_MAP
from app.ingestion.report import ParseReport
from app.models import Club, MatchReview
from app.models import Swimmer as DbSwimmer


def _clean(value: str | None) -> str | None:
    if not value or value == "N/A":
        return None
    return value


async def resolve_clubs(
    session: AsyncSession, teams: dict[str, HyTeam], report: ParseReport
) -> dict[str, Club]:
    """Upsert clubs by code. Blank DB fields get backfilled; non-blank ones are left alone."""
    codes = list(teams.keys())
    existing = (
        (await session.execute(select(Club).where(Club.code.in_(codes)))).scalars().all()
        if codes
        else []
    )
    by_code = {c.code: c for c in existing}

    resolved: dict[str, Club] = {}
    for code, team in teams.items():
        club = by_code.get(code)
        if club is None:
            club = Club(
                code=code,
                name=team.name,
                shortName=_clean(team.short_name),
                region=_clean(team.region),
                address1=_clean(team.address_1),
                address2=_clean(team.address_2),
                town=_clean(team.city),
                country=_clean(team.country),
                email=_clean(team.email),
            )
            session.add(club)
            report.clubs_new += 1
        else:
            _backfill_club(club, team)
            report.clubs_matched += 1
        resolved[code] = club

    await session.flush()
    return resolved


def _backfill_club(club: Club, team: HyTeam) -> None:
    updates = {
        "name": team.name,
        "shortName": _clean(team.short_name),
        "region": _clean(team.region),
        "address1": _clean(team.address_1),
        "address2": _clean(team.address_2),
        "town": _clean(team.city),
        "country": _clean(team.country),
        "email": _clean(team.email),
    }
    for field_name, value in updates.items():
        if value and not getattr(club, field_name):
            setattr(club, field_name, value)


@dataclass
class SwimmerResolution:
    swimmer: DbSwimmer | None  # None means excluded pending match review
    created: bool
    needs_review: bool


async def resolve_swimmers(
    session: AsyncSession,
    hy_swimmers: dict[int, HySwimmer],
    clubs_by_code: dict[str, Club],
    upload_id: str,
    report: ParseReport,
) -> dict[int, SwimmerResolution]:
    """Resolve every swimmer in the meet to a DB row (or flag for review).

    Order: (a) exact registrationNo match; (b) exact
    (lastName, firstName, dateOfBirth) match in the same club; (c) multiple
    name/DOB candidates, or a single one in a different club, is ambiguous -
    a match_reviews row is created and that swimmer is excluded from
    promotion; (d) no match at all creates a new swimmer.
    """
    reg_nos = {
        s.usa_swimming_id.strip()
        for s in hy_swimmers.values()
        if s.usa_swimming_id and s.usa_swimming_id.strip()
    }
    name_dob_keys = {
        (s.last_name, s.first_name, s.date_of_birth)
        for s in hy_swimmers.values()
        if s.date_of_birth is not None
    }

    existing_by_regno: dict[str, DbSwimmer] = {}
    if reg_nos:
        rows = (
            (
                await session.execute(
                    select(DbSwimmer).where(DbSwimmer.registrationNo.in_(reg_nos))
                )
            )
            .scalars()
            .all()
        )
        existing_by_regno = {r.registrationNo: r for r in rows}

    existing_by_name_dob: dict[tuple, list[DbSwimmer]] = defaultdict(list)
    if name_dob_keys:
        rows = (
            (
                await session.execute(
                    select(DbSwimmer).where(
                        tuple_(
                            DbSwimmer.lastName, DbSwimmer.firstName, DbSwimmer.dateOfBirth
                        ).in_(list(name_dob_keys))
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            existing_by_name_dob[(r.lastName, r.firstName, r.dateOfBirth)].append(r)

    resolutions: dict[int, SwimmerResolution] = {}

    for meet_id, hy in hy_swimmers.items():
        club = clubs_by_code[hy.team_code]
        reg_no = (hy.usa_swimming_id or "").strip() or None
        gender = GENDER_MAP.get(hy.gender)

        if gender is None:
            report.add_reject(
                "unknown_gender",
                meetSwimmerId=meet_id,
                name=f"{hy.first_name} {hy.last_name}",
            )
            resolutions[meet_id] = SwimmerResolution(None, created=False, needs_review=False)
            continue

        existing = existing_by_regno.get(reg_no) if reg_no else None
        if existing is not None:
            _backfill_swimmer(existing, hy, reg_no=None)
            if existing.clubId != club.id:
                existing.clubId = club.id
            resolutions[meet_id] = SwimmerResolution(existing, created=False, needs_review=False)
            report.swimmers_matched += 1
            continue

        candidates = existing_by_name_dob.get((hy.last_name, hy.first_name, hy.date_of_birth), [])

        if not candidates:
            new_swimmer = DbSwimmer(
                registrationNo=reg_no,
                firstName=hy.first_name,
                lastName=hy.last_name,
                preferredName=_clean(hy.nick_name),
                middleInitial=_clean(hy.middle_initial),
                gender=gender,
                dateOfBirth=hy.date_of_birth,
                citizenship=_clean(hy.citizenship),
                clubId=club.id,
            )
            session.add(new_swimmer)
            resolutions[meet_id] = SwimmerResolution(new_swimmer, created=True, needs_review=False)
            report.swimmers_new += 1
            continue

        if len(candidates) == 1 and candidates[0].clubId == club.id:
            existing = candidates[0]
            _backfill_swimmer(existing, hy, reg_no=reg_no)
            resolutions[meet_id] = SwimmerResolution(existing, created=False, needs_review=False)
            report.swimmers_matched += 1
            continue

        # Ambiguous: multiple name+DOB candidates, or one in a different club.
        review = MatchReview(
            uploadId=upload_id,
            sourceData=json.dumps(
                {
                    "meetSwimmerId": meet_id,
                    "firstName": hy.first_name,
                    "lastName": hy.last_name,
                    "nickName": hy.nick_name,
                    "middleInitial": hy.middle_initial,
                    "registrationNo": reg_no,
                    "dateOfBirth": hy.date_of_birth.isoformat() if hy.date_of_birth else None,
                    "gender": hy.gender.value,
                    "age": hy.age,
                    "citizenship": hy.citizenship,
                    "teamCode": hy.team_code,
                }
            ),
            candidateIds=json.dumps([c.id for c in candidates]),
        )
        session.add(review)
        resolutions[meet_id] = SwimmerResolution(None, created=False, needs_review=True)
        report.swimmers_needs_review += 1
        report.add_reject(
            "swimmer_needs_review",
            meetSwimmerId=meet_id,
            name=f"{hy.first_name} {hy.last_name}",
            candidateCount=len(candidates),
        )

    await session.flush()
    return resolutions


def _backfill_swimmer(swimmer: DbSwimmer, hy: HySwimmer, reg_no: str | None) -> None:
    if reg_no and not swimmer.registrationNo:
        swimmer.registrationNo = reg_no
    if not swimmer.preferredName and _clean(hy.nick_name):
        swimmer.preferredName = _clean(hy.nick_name)
    if not swimmer.middleInitial and _clean(hy.middle_initial):
        swimmer.middleInitial = _clean(hy.middle_initial)
    if not swimmer.citizenship and _clean(hy.citizenship):
        swimmer.citizenship = _clean(hy.citizenship)
