from datetime import date

import pytest

from app.utils.season import season_bounds, season_label


def test_may_date_is_previous_year_season():
    assert season_label(date(2026, 5, 30)) == "2025/26"


def test_september_starts_new_season():
    assert season_label(date(2025, 9, 1)) == "2025/26"


def test_august_31_is_last_day_of_season():
    assert season_label(date(2026, 8, 31)) == "2025/26"


def test_january_is_still_previous_calendar_year_season():
    assert season_label(date(2026, 1, 1)) == "2025/26"


def test_season_bounds_roundtrip():
    start, end = season_bounds("2025/26")
    assert start == date(2025, 9, 1)
    assert end == date(2026, 8, 31)


def test_season_bounds_rejects_malformed_label():
    with pytest.raises(ValueError):
        season_bounds("garbage")


def test_season_bounds_rejects_non_consecutive_years():
    with pytest.raises(ValueError):
        season_bounds("2025/30")
