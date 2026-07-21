from pathlib import Path

from app.ingestion.checksums import compute_checksum, validate_lines

FIXTURE = Path(__file__).parent.parent / "fixtures" / "michael_bowles_2026.hy3"


def _fixture_lines() -> list[str]:
    return FIXTURE.read_bytes().decode("cp1252").splitlines()


def test_compute_checksum_matches_known_fixture_lines():
    lines = _fixture_lines()
    b1_line = next(line for line in lines if line[0:2] == "B1")
    assert compute_checksum(b1_line) == b1_line[128:130]


def test_validate_lines_passes_the_full_fixture():
    report = validate_lines(_fixture_lines())
    assert report.checked > 4000
    assert report.failed == 0
    assert report.failure_rate == 0.0
    assert not report.should_abort


def test_validate_lines_flags_a_corrupted_line():
    lines = _fixture_lines()
    corrupted = lines.copy()
    # Flip a character in the data region of one line without touching its checksum.
    line = corrupted[5]
    corrupted[5] = "X" + line[1:]

    report = validate_lines(corrupted)
    assert report.failed == 1
    assert report.failures[0][0] == 6  # 1-based line number


def test_should_abort_above_five_percent_failure_threshold():
    lines = _fixture_lines()
    corrupted = lines.copy()
    # Corrupt >5% of lines.
    n_to_corrupt = int(len(corrupted) * 0.10)
    for i in range(n_to_corrupt):
        corrupted[i] = "X" + corrupted[i][1:]

    report = validate_lines(corrupted)
    assert report.should_abort


def test_short_lines_are_skipped_not_failed():
    report = validate_lines(["Z0", ""])
    assert report.checked == 0
    assert report.failed == 0
    assert report.skipped == 2
