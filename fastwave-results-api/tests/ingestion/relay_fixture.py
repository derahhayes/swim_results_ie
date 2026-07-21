"""Builds a minimal synthetic HY3 file exercising relay results.

Programmatically constructed (field-by-column, using the exact offsets
hytek_parser's line_parsers read) rather than hand-typed fixed-width text,
with checksums computed via our own compute_checksum - both to avoid
transcription errors and to double as another exercise of the checksum
algorithm against freshly-generated data.

Layout: one 200 free relay event ("9"), two clubs - AAAA fields both an
"A" and a "B" team, BBBB fields an "A" team - so the relay identity fix
(eventId, clubId, relayTeamId, round) is actually exercised: three teams
share the same event, two of them share both event *and* club, and two
share event *and* relayTeamId letter ("A") but not club.

F1/F2/F3 ordering (F1 entry+seed, F2 result, F3 swimmer list - in that
order) is verified against hytek-parser's own
tests/hy3/fixtures/mm2_2_0fg_relay_multi_team.hy3 fixture.
"""

from app.ingestion.checksums import compute_checksum

LINE_LEN = 130


def _blank(code: str) -> list[str]:
    buf = [" "] * LINE_LEN
    buf[0:2] = list(code)
    return buf


def _set(buf: list[str], start_1based: int, length: int, value, right: bool = True) -> None:
    text = str(value)[:length]
    text = text.rjust(length) if right else text.ljust(length)
    buf[start_1based - 1 : start_1based - 1 + length] = list(text)


def _finish(buf: list[str]) -> str:
    line = "".join(buf)
    return line[:128] + compute_checksum(line)


def a1_line() -> str:
    buf = _blank("A1")
    _set(buf, 5, 25, "Synthetic Relay Test", right=False)
    _set(buf, 30, 15, "Hy-Tek, Ltd", right=False)
    _set(buf, 45, 10, "MM8 1.0", right=False)
    _set(buf, 59, 17, "06012026 10:00 AM", right=False)
    _set(buf, 76, 53, "Test Licensee", right=False)
    return _finish(buf)


def b1_line(name: str, facility: str, start_date: str, end_date: str) -> str:
    buf = _blank("B1")
    _set(buf, 3, 45, name, right=False)
    _set(buf, 48, 45, facility, right=False)
    _set(buf, 93, 8, start_date, right=False)
    _set(buf, 101, 8, end_date, right=False)
    return _finish(buf)


def b2_line(course: str) -> str:
    buf = _blank("B2")
    _set(buf, 94, 2, "00", right=False)
    _set(buf, 97, 2, "01", right=False)
    _set(buf, 99, 1, course, right=False)
    return _finish(buf)


def c1_line(code: str, name: str, short_name: str, region: str) -> str:
    buf = _blank("C1")
    _set(buf, 3, 5, code, right=False)
    _set(buf, 8, 30, name, right=False)
    _set(buf, 38, 16, short_name, right=False)
    _set(buf, 54, 2, region, right=False)
    return _finish(buf)


def c2_line(country: str = "IRL") -> str:
    buf = _blank("C2")
    _set(buf, 105, 3, country, right=False)
    return _finish(buf)


def c3_line(email: str = "") -> str:
    buf = _blank("C3")
    _set(buf, 93, 36, email, right=False)
    return _finish(buf)


def d1_line(gender: str, meet_id: str, last_name: str, first_name: str, dob: str, age: str) -> str:
    buf = _blank("D1")
    _set(buf, 3, 1, gender, right=False)
    _set(buf, 4, 5, meet_id)
    _set(buf, 9, 20, last_name, right=False)
    _set(buf, 29, 20, first_name, right=False)
    _set(buf, 89, 8, dob, right=False)
    _set(buf, 97, 3, age)
    return _finish(buf)


def f1_line(
    team_code: str,
    relay_letter: str,
    gender: str,
    gender_age: str,
    distance: int,
    stroke: str,
    event_number: str,
    course: str,
) -> str:
    buf = _blank("F1")
    _set(buf, 3, 5, team_code, right=False)
    _set(buf, 8, 1, relay_letter, right=False)
    _set(buf, 14, 1, gender, right=False)
    _set(buf, 15, 1, gender_age, right=False)
    _set(buf, 16, 6, distance)
    _set(buf, 22, 1, stroke, right=False)
    _set(buf, 23, 3, 0)
    _set(buf, 26, 3, 109)
    _set(buf, 39, 4, event_number, right=False)
    _set(buf, 51, 1, course, right=False)
    return _finish(buf)


def f2_line(round_code: str, time_str: str, course: str, heat: int, lane: int, date_str: str) -> str:
    buf = _blank("F2")
    _set(buf, 3, 1, round_code, right=False)
    _set(buf, 4, 8, time_str)
    _set(buf, 12, 1, course, right=False)
    _set(buf, 13, 1, " ", right=False)  # WithTimeTimeCode.NORMAL
    _set(buf, 21, 3, heat)
    _set(buf, 24, 3, lane)
    _set(buf, 27, 3, 1)
    _set(buf, 30, 4, 1)
    _set(buf, 103, 8, date_str, right=False)
    return _finish(buf)


def f3_line(swimmer_meet_ids: list[str]) -> str:
    buf = _blank("F3")
    for i, meet_id in enumerate(swimmer_meet_ids):
        offset = i * 13
        _set(buf, 4 + offset, 5, meet_id)
        _set(buf, 15 + offset, 1, i + 1, right=False)
    return _finish(buf)


def build_synthetic_relay_hy3(
    swimmer_ids_a: list[str],
    swimmer_ids_b: list[str],
    swimmer_ids_bbbb_a: list[str],
    *,
    time_a: str = "120.45",
    time_b: str = "125.10",
    time_bbbb_a: str = "121.99",
) -> bytes:
    """4x50 free relay, event "9": club AAAA fields A+B teams, BBBB fields an A team."""
    lines: list[str] = [
        a1_line(),
        b1_line("Synthetic Relay Meet", "Test Pool", "06012026", "06012026"),
        b2_line("L"),
        c1_line("AAAA", "Aaaa Swim Club", "Aaaa SC", "MU"),
        c2_line(),
        c3_line(),
    ]
    for meet_id, (last, first) in zip(
        swimmer_ids_a + swimmer_ids_b,
        [(f"Last{i}", f"First{i}") for i in range(len(swimmer_ids_a) + len(swimmer_ids_b))],
    ):
        lines.append(d1_line("M", meet_id, last, first, "01012000", "26"))

    lines += [
        c1_line("BBBB", "Bbbb Swim Club", "Bbbb SC", "MU"),
        c2_line(),
        c3_line(),
    ]
    for i, meet_id in enumerate(swimmer_ids_bbbb_a):
        lines.append(d1_line("M", meet_id, f"BLast{i}", f"BFirst{i}", "01012000", "26"))

    def relay_block(team_code: str, letter: str, swimmer_ids: list[str], time_str: str) -> list[str]:
        return [
            f1_line(team_code, letter, "M", "M", 200, "A", "9", "L"),
            f2_line("F", time_str, "L", 1, 1, "06012026"),
            f3_line(swimmer_ids),
        ]

    lines += relay_block("AAAA", "A", swimmer_ids_a, time_a)
    lines += relay_block("AAAA", "B", swimmer_ids_b, time_b)
    lines += relay_block("BBBB", "A", swimmer_ids_bbbb_a, time_bbbb_a)

    lines.append("Z0")

    return ("\n".join(lines) + "\n").encode("cp1252")
