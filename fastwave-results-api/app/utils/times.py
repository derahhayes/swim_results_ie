"""Formatting for times stored as integer hundredths of a second."""

from typing import Optional

HS_PER_MINUTE = 6000  # 60 seconds * 100 hundredths


def format_time_hs(hs: Optional[int]) -> Optional[str]:
    """Format hundredths-of-a-second as Hy-Tek-style "M:SS.hh" or "SS.hh".

    None in, None out - callers use this directly on nullable timeHs/
    seedTimeHs/cumulativeTimeHs columns (NULL means NS/DNF: no time to show).
    """
    if hs is None:
        return None

    minutes, remainder = divmod(hs, HS_PER_MINUTE)
    seconds, hundredths = divmod(remainder, 100)

    if minutes:
        return f"{minutes}:{seconds:02d}.{hundredths:02d}"
    return f"{seconds}.{hundredths:02d}"
