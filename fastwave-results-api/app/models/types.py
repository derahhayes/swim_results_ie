from sqlalchemy import Enum as SAEnum

from app.models.enums import (
    ClaimStatus,
    Course,
    Gender,
    ResultStatus,
    Round,
    Stroke,
    UploadStatus,
)


def _pg_enum(enum_cls, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda obj: [member.value for member in obj],
    )


# Shared type instances - reused across columns/tables so each Postgres
# enum type (e.g. "course") is only created once, not once per column.
GenderType = _pg_enum(Gender, "gender")
StrokeType = _pg_enum(Stroke, "stroke")
CourseType = _pg_enum(Course, "course")
RoundType = _pg_enum(Round, "round")
ResultStatusType = _pg_enum(ResultStatus, "result_status")
ClaimStatusType = _pg_enum(ClaimStatus, "claim_status")
UploadStatusType = _pg_enum(UploadStatus, "upload_status")
