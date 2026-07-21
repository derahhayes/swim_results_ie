"""Irish swimming season labels: Sep 1 - Aug 31, e.g. "2025/26"."""

import re
from datetime import date

SEASON_LABEL_RE = re.compile(r"^(\d{4})/(\d{2})$")


def season_label(d: date) -> str:
    """The season label a given date falls in."""
    start_year = d.year if d.month >= 9 else d.year - 1
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def season_bounds(label: str) -> tuple[date, date]:
    """The [start, end] dates (inclusive) a season label covers.

    Raises ValueError for a malformed label - callers turn that into a 422.
    """
    match = SEASON_LABEL_RE.match(label)
    if not match:
        raise ValueError(f"Invalid season label: {label!r} (expected e.g. '2025/26')")

    start_year = int(match.group(1))
    end_year_suffix = int(match.group(2))
    if (start_year + 1) % 100 != end_year_suffix:
        raise ValueError(f"Invalid season label: {label!r} (year parts don't match consecutive years)")

    return date(start_year, 9, 1), date(start_year + 1, 8, 31)
