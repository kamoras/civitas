"""Coverage for the /admin/action-metrics readout.

The action pipeline has written per-run validator counters to api_cache
since the 2026-07 audit, but nothing read them back — so "is the platform
quiet because the news is quiet, or because a gate is dropping
everything?" could only be answered by querying the box by hand. Every
gate in the action pipeline fails closed, which makes silence the shared
failure mode of about ten independent checks; these tests pin the
intake/output/suppressed split that tells those cases apart.
"""

import json

import pytest

from app.models import ApiCache
from app.time_utils import utcnow


def _metrics_row(db, key: str, counts: dict, *, minutes_ago: int = 0):
    from datetime import timedelta

    db.add(ApiCache(
        tier="action-metrics",
        cache_key=key,
        data_json=json.dumps({"counts": counts}),
        cached_at=utcnow() - timedelta(minutes=minutes_ago),
    ))
    db.commit()


@pytest.mark.asyncio
async def test_returns_runs_newest_first(db_session):
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-2026-07-27-1000", {"articles_fetched": 40}, minutes_ago=120)
    _metrics_row(db_session, "run-2026-07-27-1100", {"articles_fetched": 55}, minutes_ago=60)

    result = await admin_action_metrics(limit=48, db=db_session)

    assert [r["run"] for r in result["runs"]] == [
        "run-2026-07-27-1100", "run-2026-07-27-1000",
    ]
    assert result["runsReturned"] == 2


@pytest.mark.asyncio
async def test_healthy_intake_with_no_output_reads_as_suppression(db_session):
    # The shape the endpoint exists to make visible: articles arrived and
    # clustered, and every one of them was dropped by a gate.
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-a", {
        "articles_fetched": 61,
        "articles_policy_relevant": 24,
        "clusters_considered": 4,
        "issues_skipped_no_action_surface": 3,
        "bsky_reposts_suppressed_no_new_information": 1,
    })

    totals = (await admin_action_metrics(limit=48, db=db_session))["totals"]

    assert totals["intake"]["articles_fetched"] == 61
    assert totals["intake"]["clusters_considered"] == 4
    assert totals["output"] == {}          # nothing published
    assert totals["suppressedTotal"] == 4  # and four things were dropped
    assert totals["suppressed"]["issues_skipped_no_action_surface"] == 3


@pytest.mark.asyncio
async def test_quiet_intake_is_distinguishable_from_suppression(db_session):
    # The genuinely-slow-news-day shape: nothing came in, so nothing was
    # dropped either. Must not look like the case above.
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-b", {
        "articles_fetched": 12,
        "articles_policy_relevant": 1,
        "clusters_considered": 1,
        "issues_matched_existing": 1,
    })

    totals = (await admin_action_metrics(limit=48, db=db_session))["totals"]

    assert totals["suppressedTotal"] == 0
    assert totals["output"]["issues_matched_existing"] == 1


@pytest.mark.asyncio
async def test_aborted_run_is_recorded_not_absent(db_session):
    # A refresh that fetched nothing used to return before persisting, so
    # a broken feed and a healthy quiet hour both left no row at all.
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-c", {"refresh_aborted_no_articles": 1})

    result = await admin_action_metrics(limit=48, db=db_session)

    assert result["runs"][0]["counts"]["refresh_aborted_no_articles"] == 1


@pytest.mark.asyncio
async def test_zero_and_absent_counters_are_omitted_from_totals(db_session):
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-d", {"articles_fetched": 5, "issues_new_topic": 0})

    totals = (await admin_action_metrics(limit=48, db=db_session))["totals"]

    assert "issues_new_topic" not in totals["output"]  # 0 is noise, not signal
    assert totals["intake"]["articles_fetched"] == 5


@pytest.mark.asyncio
async def test_malformed_row_does_not_blank_the_report(db_session):
    from app.api.admin import admin_action_metrics

    db_session.add(ApiCache(
        tier="action-metrics", cache_key="run-bad", data_json="not json",
        cached_at=utcnow(),
    ))
    db_session.commit()
    _metrics_row(db_session, "run-good", {"articles_fetched": 7}, minutes_ago=30)

    result = await admin_action_metrics(limit=48, db=db_session)

    assert result["runsReturned"] == 1
    assert result["totals"]["intake"]["articles_fetched"] == 7


@pytest.mark.parametrize("payload", [
    {"counts": None},          # valid JSON, unusable shape
    {"counts": ["a", "b"]},    # counts as a list
    {"counts": "12"},          # counts as a string
    ["not", "an", "object"],   # payload itself not an object
    {},                        # no counts key at all
])
@pytest.mark.asyncio
async def test_unusable_count_shapes_are_skipped_not_fatal(db_session, payload):
    # A diagnostic endpoint that 500s on one bad row fails exactly when
    # someone is reaching for it. Invalid JSON was already handled; valid
    # JSON of the wrong shape raised AttributeError.
    from app.api.admin import admin_action_metrics

    db_session.add(ApiCache(
        tier="action-metrics", cache_key="run-odd",
        data_json=json.dumps(payload), cached_at=utcnow(),
    ))
    db_session.commit()
    _metrics_row(db_session, "run-ok", {"articles_fetched": 3}, minutes_ago=10)

    result = await admin_action_metrics(limit=48, db=db_session)

    assert result["runsReturned"] == 1
    assert result["totals"]["intake"]["articles_fetched"] == 3


@pytest.mark.asyncio
async def test_non_integer_counter_values_are_dropped(db_session):
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-mixed", {
        "articles_fetched": 4,
        "clusters_considered": "many",  # corrupt: would break sum()
        "issues_new_topic": True,       # bool is an int subclass; not a count
    })

    result = await admin_action_metrics(limit=48, db=db_session)

    assert result["totals"]["intake"] == {"articles_fetched": 4}
    assert result["totals"]["output"] == {}


@pytest.mark.asyncio
async def test_ungrouped_counters_surface_in_other(db_session):
    # The endpoint must not be able to hide a gate. Counters that predate
    # the grouping (facts_dropped_*), and any a future gate adds without
    # updating the tuples above, have to remain visible in totals — an
    # unrecognised counter is the one most worth seeing.
    from app.api.admin import admin_action_metrics

    _metrics_row(db_session, "run-e", {
        "articles_fetched": 9,
        "facts_dropped_ungrounded_number": 2,
        "refresh_aborted_no_articles": 1,
        "some_gate_invented_next_year": 5,
    })

    totals = (await admin_action_metrics(limit=48, db=db_session))["totals"]

    assert totals["other"] == {
        "facts_dropped_ungrounded_number": 2,
        "refresh_aborted_no_articles": 1,
        "some_gate_invented_next_year": 5,
    }
    # ...and grouped counters must not be double-counted into it.
    assert "articles_fetched" not in totals["other"]


@pytest.mark.asyncio
async def test_other_api_cache_tiers_are_not_included(db_session):
    from app.api.admin import admin_action_metrics

    db_session.add(ApiCache(
        tier="congress-bills", cache_key="hr1",
        data_json=json.dumps({"counts": {"articles_fetched": 999}}),
        cached_at=utcnow(),
    ))
    db_session.commit()

    result = await admin_action_metrics(limit=48, db=db_session)

    assert result["runsReturned"] == 0


@pytest.mark.asyncio
async def test_limit_caps_the_window(db_session):
    from app.api.admin import admin_action_metrics

    for i in range(5):
        _metrics_row(db_session, f"run-{i}", {"articles_fetched": 1}, minutes_ago=i)

    result = await admin_action_metrics(limit=2, db=db_session)

    assert result["runsReturned"] == 2
    assert result["totals"]["intake"]["articles_fetched"] == 2
