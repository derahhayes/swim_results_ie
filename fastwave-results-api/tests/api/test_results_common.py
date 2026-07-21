"""Unit tests for the ranking/round-ordering rule.

The Michael Bowles 2026 fixture is finals-only (no prelims/swim-offs), so
the "FINAL first, then SWIMOFF, then PRELIM" ordering can't be exercised
against real seeded data - tested directly against result_sort_key here
instead. Within-round place/DQ/NS ordering *is* exercised against real
data, in test_events.py.
"""

from types import SimpleNamespace

from app.api.v1.results_common import result_sort_key
from app.models.enums import ResultStatus, Round


def _result(round_, status=ResultStatus.OK, overallPlace=None, timeHs=None):
    return SimpleNamespace(round=round_, status=status, overallPlace=overallPlace, timeHs=timeHs)


def test_final_sorts_before_swimoff_and_prelim():
    results = [
        _result(Round.PRELIM, overallPlace=1),
        _result(Round.FINAL, overallPlace=1),
        _result(Round.SWIMOFF, overallPlace=1),
    ]
    ordered = sorted(results, key=result_sort_key)
    assert [r.round for r in ordered] == [Round.FINAL, Round.SWIMOFF, Round.PRELIM]


def test_ranked_swims_before_dq_before_ns():
    results = [
        _result(Round.FINAL, status=ResultStatus.NS),
        _result(Round.FINAL, status=ResultStatus.DQ),
        _result(Round.FINAL, status=ResultStatus.OK, overallPlace=1),
    ]
    ordered = sorted(results, key=result_sort_key)
    assert [r.status for r in ordered] == [ResultStatus.OK, ResultStatus.DQ, ResultStatus.NS]


def test_dnf_buckets_with_dq_scr_buckets_with_ns():
    results = [
        _result(Round.FINAL, status=ResultStatus.SCR),
        _result(Round.FINAL, status=ResultStatus.DNF),
        _result(Round.FINAL, status=ResultStatus.OK, overallPlace=2),
    ]
    ordered = sorted(results, key=result_sort_key)
    assert ordered[0].status == ResultStatus.OK
    assert ordered[1].status == ResultStatus.DNF
    assert ordered[2].status == ResultStatus.SCR


def test_ranked_swims_ordered_by_overall_place_ascending():
    results = [
        _result(Round.FINAL, overallPlace=3),
        _result(Round.FINAL, overallPlace=1),
        _result(Round.FINAL, overallPlace=2),
    ]
    ordered = sorted(results, key=result_sort_key)
    assert [r.overallPlace for r in ordered] == [1, 2, 3]


def test_falls_back_to_time_when_no_overall_place():
    results = [
        _result(Round.FINAL, timeHs=3000),
        _result(Round.FINAL, timeHs=2500),
    ]
    ordered = sorted(results, key=result_sort_key)
    assert [r.timeHs for r in ordered] == [2500, 3000]
