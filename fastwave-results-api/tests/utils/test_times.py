from app.utils.times import format_time_hs


def test_none_is_none():
    assert format_time_hs(None) is None


def test_sub_minute():
    assert format_time_hs(2745) == "27.45"


def test_over_a_minute():
    assert format_time_hs(6312) == "1:03.12"


def test_multi_minute():
    assert format_time_hs(102250) == "17:02.50"


def test_exact_minute_boundary():
    assert format_time_hs(6000) == "1:00.00"


def test_zero():
    assert format_time_hs(0) == "0.00"


def test_single_hundredth():
    assert format_time_hs(1) == "0.01"
