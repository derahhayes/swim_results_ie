"""Hy-Tek HY3 line checksum: compute + warn-only validation.

Each HY3 line is fixed-width, 130 characters, with a 2-character checksum
in the final two columns. The checksum covers the first 128 characters
(i.e. everything except itself).

Algorithm (reverse-engineered, verified against the Michael Bowles 2026
fixture at 0% mismatch across all 4,704 lines):

    data = line[0:128]
    odd_sum  = sum(ord(c) for i, c in enumerate(data) if i % 2 == 1)
    even_sum = sum(ord(c) for i, c in enumerate(data) if i % 2 == 0)
    total = odd_sum * 2 + even_sum
    checksum = str((total // 21) + 205)[-2:][::-1]
"""

from dataclasses import dataclass, field

LINE_LENGTH = 130
DATA_LENGTH = 128

# If more than this fraction of checksummed lines fail, the file is treated
# as corrupt/untrustworthy and ingestion aborts rather than promoting
# possibly-garbled data. Below this, mismatches are recorded as warnings only.
FAILURE_ABORT_THRESHOLD = 0.05

# Cap on how many individual failures we keep for the report - a corrupt
# file could otherwise blow up the JSON blob we persist to uploads.parseReport.
MAX_RECORDED_FAILURES = 50


def compute_checksum(line: str) -> str:
    """Compute the expected 2-char Hy-Tek checksum for a line."""
    data = line[:DATA_LENGTH]
    odd_sum = sum(ord(c) for i, c in enumerate(data) if i % 2 == 1)
    even_sum = sum(ord(c) for i, c in enumerate(data) if i % 2 == 0)
    total = odd_sum * 2 + even_sum
    value = (total // 21) + 205
    return str(value)[-2:][::-1]


@dataclass
class ChecksumReport:
    checked: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[tuple[int, str]] = field(default_factory=list)  # (line_no, line_code)

    @property
    def failure_rate(self) -> float:
        return self.failed / self.checked if self.checked else 0.0

    @property
    def should_abort(self) -> bool:
        return self.checked > 0 and self.failure_rate > FAILURE_ABORT_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "failed": self.failed,
            "skipped": self.skipped,
            "failure_rate": round(self.failure_rate, 4),
            "failures": [{"line": n, "code": c} for n, c in self.failures],
        }


def validate_lines(lines: list[str]) -> ChecksumReport:
    """Validate checksums for every line, warn-only (see FAILURE_ABORT_THRESHOLD)."""
    report = ChecksumReport()
    for line_no, line in enumerate(lines, start=1):
        if len(line) < LINE_LENGTH:
            # Terminator ("Z0") and any other short/blank lines have no
            # checksum to validate.
            report.skipped += 1
            continue

        report.checked += 1
        expected = line[DATA_LENGTH:LINE_LENGTH]
        actual = compute_checksum(line)
        if actual != expected:
            report.failed += 1
            if len(report.failures) < MAX_RECORDED_FAILURES:
                report.failures.append((line_no, line[0:2]))

    return report
