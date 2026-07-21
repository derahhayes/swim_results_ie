import enum


class Gender(enum.Enum):
    M = "M"
    F = "F"


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
