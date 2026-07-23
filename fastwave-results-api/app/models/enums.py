import enum


class Gender(enum.Enum):
    M = "M"
    F = "F"
    # Mixed-gender relay events (e.g. mixed medley/freestyle relays at
    # development/community meets) - HY3's E1/F1 gender field uses "X" for
    # these, confirmed against hytek_parser's own g/f event line parsers
    # (see app/ingestion/_mappings.py's GENDER_MAP and vendor/hytek-parser's
    # Gender enum, which previously had no member for it and fell back to
    # UNKNOWN, making every mixed-relay event unmappable). Never applies to
    # an individual swimmer's own gender, only to an event's.
    MIXED = "X"


class Stroke(enum.Enum):
    FREE = "FR"
    BACK = "BK"
    BREAST = "BR"
    FLY = "FL"
    IM = "IM"


class Course(enum.Enum):
    LCM = "L"
    SCM = "S"
    SCY = "Y"


class Round(enum.Enum):
    PRELIM = "P"
    SWIMOFF = "S"
    FINAL = "F"


class ResultStatus(enum.Enum):
    OK = "OK"
    DQ = "DQ"
    DNF = "DNF"
    NS = "NS"
    SCR = "SCR"
    EXH = "EXH"


class ClaimStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class UploadStatus(enum.Enum):
    RECEIVED = "received"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    PROMOTED = "promoted"
    FAILED = "failed"
