from sqlalchemy import select

from app.models import MeetEvent
from tests.api.gdpr import assert_no_pii


async def _get_event_id(db_session, event_no: str, meet_id: str) -> str:
    event = (
        await db_session.execute(
            select(MeetEvent).where(MeetEvent.meetId == meet_id, MeetEvent.eventNo == event_no)
        )
    ).scalar_one()
    return event.id


async def test_event_results_shape_and_gdpr(api_client, seeded_meets, db_session):
    event_id = await _get_event_id(db_session, "3A", seeded_meets["main"])  # 100 free, has NS + DQ
    resp = await api_client.get(f"/api/v1/events/{event_id}/results")
    assert resp.status_code == 200
    body = resp.json()
    assert_no_pii(body)

    assert body["event"]["eventNo"] == "3A"
    assert len(body["rounds"]) == 1
    round_ = body["rounds"][0]
    assert round_["round"] == "F"

    results = round_["results"]
    assert len(results) > 0

    # Ranked (OK) swims come first, ordered by overallPlace ascending, then DQ, then NS.
    statuses = [r["status"] for r in results]
    ok_statuses = [s for s in statuses if s == "OK"]
    first_non_ok_index = next(i for i, s in enumerate(statuses) if s != "OK")
    assert statuses[:first_non_ok_index] == ok_statuses

    ok_places = [r["overallPlace"] for r in results if r["status"] == "OK"]
    assert ok_places == sorted(ok_places)

    dq_indices = [i for i, s in enumerate(statuses) if s == "DQ"]
    ns_indices = [i for i, s in enumerate(statuses) if s == "NS"]
    if dq_indices and ns_indices:
        assert max(dq_indices) < min(ns_indices)


async def test_event_results_splits_sum_to_final_time(api_client, seeded_meets, db_session):
    event_id = await _get_event_id(db_session, "3A", seeded_meets["main"])
    resp = await api_client.get(f"/api/v1/events/{event_id}/results")
    body = resp.json()

    checked_any = False
    for row in body["rounds"][0]["results"]:
        if not row["splits"] or row["timeHs"] is None:
            continue
        checked_any = True
        summed = sum(s["deltaHs"] for s in row["splits"])
        last_cumulative = row["splits"][-1]["cumulativeTimeHs"]
        assert summed == last_cumulative  # deltas telescope exactly by construction
        # Allow +/-1 hundredth of rounding per split segment between the last
        # recorded split and the official finish time.
        tolerance = len(row["splits"])
        assert abs(last_cumulative - row["timeHs"]) <= tolerance

    assert checked_any


async def test_event_results_bogus_id_404(api_client):
    resp = await api_client.get("/api/v1/events/does-not-exist/results")
    assert resp.status_code == 404


async def test_event_results_caching_headers_and_etag(api_client, seeded_meets, db_session):
    event_id = await _get_event_id(db_session, "3A", seeded_meets["main"])
    resp = await api_client.get(f"/api/v1/events/{event_id}/results")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=300"
    etag = resp.headers.get("etag")
    assert etag

    resp2 = await api_client.get(f"/api/v1/events/{event_id}/results", headers={"If-None-Match": etag})
    assert resp2.status_code == 304
