"""ParseReport: the JSON summary persisted to uploads.parseReport."""

import json
from dataclasses import asdict, dataclass, field


@dataclass
class ParseReport:
    status: str = "received"
    error: str | None = None

    clubs_new: int = 0
    clubs_matched: int = 0

    swimmers_new: int = 0
    swimmers_matched: int = 0
    swimmers_needs_review: int = 0

    events: int = 0
    results_by_round: dict[str, int] = field(default_factory=dict)
    splits: int = 0
    relay_legs: int = 0

    checksum: dict = field(default_factory=dict)
    rejects: list[dict] = field(default_factory=list)

    def add_reject(self, reason: str, **detail: object) -> None:
        self.rejects.append({"reason": reason, **detail})

    def record_result(self, round_code: str) -> None:
        self.results_by_round[round_code] = self.results_by_round.get(round_code, 0) + 1

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)
