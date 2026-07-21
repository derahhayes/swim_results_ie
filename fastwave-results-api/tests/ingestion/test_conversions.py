from hytek_parser.hy3.enums import ReplacedTimeTimeCode, WithTimeTimeCode

from app.ingestion.promote import place_or_none, status_from_time_code, time_to_hundredths
from app.models.enums import ResultStatus


def test_none_converts_to_none():
    assert time_to_hundredths(None) is None


def test_replaced_time_time_code_converts_to_none():
    assert time_to_hundredths(ReplacedTimeTimeCode.NO_SHOW) is None
    assert time_to_hundredths(ReplacedTimeTimeCode.DISQUALIFICATION) is None
    assert time_to_hundredths(ReplacedTimeTimeCode.UNKNOWN) is None


def test_ordinary_float_converts_to_hundredths():
    assert time_to_hundredths(63.12) == 6312


def test_zero_converts_to_none():
    # Hy-Tek writes "0.00" for unused timing slots; a genuine zero swim time
    # is not physically possible, so 0.0 means "not recorded" not "instant".
    assert time_to_hundredths(0.0) is None


def test_rounds_to_nearest_hundredth():
    assert time_to_hundredths(167.06) == 16706
    assert time_to_hundredths(29.995) == 3000  # rounds, doesn't truncate


def test_status_from_time_code_mapping():
    assert status_from_time_code(WithTimeTimeCode.NORMAL) == ResultStatus.OK
    assert status_from_time_code(WithTimeTimeCode.NO_SHOW) == ResultStatus.NS
    assert status_from_time_code(WithTimeTimeCode.SCRATCH) == ResultStatus.SCR
    assert status_from_time_code(WithTimeTimeCode.DISQUALIFICATION) == ResultStatus.DQ
    assert status_from_time_code(WithTimeTimeCode.FALSE_START) == ResultStatus.DQ
    assert status_from_time_code(WithTimeTimeCode.DID_NOT_FINISH) == ResultStatus.DNF


def test_status_from_time_code_unknown_falls_back_to_ok():
    assert status_from_time_code(WithTimeTimeCode.UNKNOWN) == ResultStatus.OK


def test_place_or_none_none_stays_none():
    assert place_or_none(None) is None


def test_place_or_none_zero_becomes_none():
    # safe_cast(int, "") defaults to 0 for a blank HY3 place field (always
    # blank on NS/DQ/SCR rows) - 0 is never a meaningful place either way.
    assert place_or_none(0) is None


def test_place_or_none_negative_becomes_none():
    assert place_or_none(-1) is None


def test_place_or_none_positive_passes_through():
    assert place_or_none(1) == 1
    assert place_or_none(28) == 28
