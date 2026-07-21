"""Enum translation between hytek-parser's HY3 enums and our own.

Kept separate from matching.py/promote.py because both need it and their
value spaces genuinely differ (e.g. hytek's Stroke uses "A".."E" while ours
uses the "FR"/"BK"/... HY3 event codes), so a naive .value passthrough is
wrong for stroke/gender-age and must go through an explicit lookup.
"""

from hytek_parser.hy3.enums import Course as HyCourse
from hytek_parser.hy3.enums import Gender as HyGender
from hytek_parser.hy3.enums import Stroke as HyStroke

from app.models.enums import Course, Gender, Stroke

GENDER_MAP: dict[HyGender, Gender] = {
    HyGender.MALE: Gender.M,
    HyGender.FEMALE: Gender.F,
}

STROKE_MAP: dict[HyStroke, Stroke] = {
    HyStroke.FREESTYLE: Stroke.FREE,
    HyStroke.BACKSTROKE: Stroke.BACK,
    HyStroke.BREASTSTROKE: Stroke.BREAST,
    HyStroke.BUTTERFLY: Stroke.FLY,
    HyStroke.MEDLEY: Stroke.IM,
}

# HyCourse.DQ / HyCourse.UNKNOWN (Hy-Tek's "no course specified" sentinels,
# seen e.g. on IM/medley-adjacent events where MM leaves the E1 course
# column blank) have no equivalent in our Course enum - callers must supply
# a fallback (typically the meet's own course).
COURSE_MAP: dict[HyCourse, Course] = {
    HyCourse.LCM: Course.LCM,
    HyCourse.SCM: Course.SCM,
    HyCourse.SCY: Course.SCY,
}


def map_course(hy_course: HyCourse, fallback: Course) -> Course:
    return COURSE_MAP.get(hy_course, fallback)


def map_course_optional(hy_course: HyCourse | None) -> Course | None:
    if hy_course is None:
        return None
    return COURSE_MAP.get(hy_course)
