"""The refresh loop tries ranked clusters until MAX_ISSUES publish.

Driven through the real _run_refresh with the network, the model and the
embedding model stubbed out, because the behaviour under test is the
loop's own control flow: a top cluster that yields no claim must fall
through to the next one rather than end the run (the Action Center
published nothing on 2026-09-26 when both of its only two candidates
failed), and the run must still stop at MAX_ISSUES.
"""

import json

import numpy as np
import pytest

from app.models import ActionIssue, ApiCache
from app.pipeline.analyze import action_center
from app.pipeline.fetch.news_feeds import NewsArticle


def _article(cluster: int, i: int) -> NewsArticle:
    return NewsArticle(
        title=f"Story {cluster} headline {i}",
        url=f"https://example.com/{cluster}/{i}",
        source_name=f"Outlet {i}",
        summary=f"Summary {cluster}-{i}",
    )


CLUSTERS = [[_article(c, i) for i in range(3)] for c in range(1, 7)]
# Clusters 1, 2 and 4 yield no attributable claim; 3, 5 and 6 do.
WITH_CLAIMS = {3, 5, 6}


def _cluster_number(cluster) -> int:
    return int(cluster[0].title.split()[1])


@pytest.fixture()
def refresh(monkeypatch):
    ac = action_center
    for name in (
        "check_roll_call_signals", "check_federal_register_signals", "expire_stale_developing_issues",
        "_run_periodic_bluesky_posts", "generate_period_summaries",
        "_update_national_monitors", "_save_timeline_entry",
        "_prune_stale_api_cache", "_generate_full_story", "_cleanup_old_unposted_issues",
        "_record_generation_sample", "_log_summary_source_consistency", "log_intensifier_usage",
    ):
        monkeypatch.setattr(ac, name, lambda *a, **k: None)
    # Imported inside the function, so stubbed where they live.
    from app.pipeline.analyze import bluesky_engagement, bluesky_poster
    monkeypatch.setattr(bluesky_poster, "process_issues_for_bluesky", lambda *a, **k: 0)
    monkeypatch.setattr(bluesky_engagement, "engage_with_news_posts", lambda *a, **k: None)
    articles = [a for c in CLUSTERS for a in c]
    monkeypatch.setattr(ac, "fetch_news_articles", lambda: articles)
    monkeypatch.setattr(ac, "_filter_policy_relevant", lambda arts, db: arts)
    monkeypatch.setattr(ac, "fetch_trending_topics", lambda: [])
    monkeypatch.setattr(ac, "_cluster_articles", lambda arts: CLUSTERS)
    monkeypatch.setattr(ac, "_rank_clusters", lambda cl, tr, db: (cl, [1.0 - i / 10 for i in range(len(cl))]))
    pool = {}

    def dedupe(ranked, scores, n):
        pool["n"] = n
        return ranked[:n]

    monkeypatch.setattr(ac, "_deduplicate_top_clusters", dedupe)
    rng = np.random.default_rng(7)
    monkeypatch.setattr(ac, "_embed_texts", lambda texts: rng.normal(size=(len(texts), 8)))
    # One orthogonal vector per distinct title, so no two issues read as
    # duplicates of each other (hash() is randomised per process).
    title_ids: dict[str, int] = {}
    monkeypatch.setattr(ac, "_embed_texts_sim", lambda texts: [
        np.eye(64)[title_ids.setdefault(t, len(title_ids))] for t in texts
    ])
    monkeypatch.setattr(ac, "_largest_coherent_subgroup", lambda m, t: list(range(len(m))))
    monkeypatch.setattr(ac, "_resolve_url", lambda u: u)

    def extract(cluster, locate):
        return ["claim a", "claim b"] if _cluster_number(cluster) in WITH_CLAIMS else []

    monkeypatch.setattr(ac.claim_layer, "extract_claims", extract)
    monkeypatch.setattr(ac.claim_layer, "on_topic", lambda claims, cluster: claims)
    monkeypatch.setattr(ac.claim_layer, "build_lede", lambda claims: "A lede.")
    monkeypatch.setattr(ac.claim_layer, "build_facts", lambda claims: (["A fact."], ["Outlet 0"]))
    monkeypatch.setattr(ac, "grounding_violations", lambda text, source: [])
    monkeypatch.setattr(ac, "hedge_and_editorializing_violations", lambda text: [])
    monkeypatch.setattr(ac, "_classify_issue_policy_areas", lambda t, s: [])
    monkeypatch.setattr(ac, "_resolve_bills", lambda raw, texts: [{"id": "hr1-119", "title": "A bill"}])
    monkeypatch.setattr(ac, "_find_related_explore_docs", lambda *a: [])
    monkeypatch.setattr(ac, "_find_related_senators", lambda *a: [])
    monkeypatch.setattr(ac, "_find_related_officials", lambda *a: [])
    monkeypatch.setattr(ac, "_build_actions_from_data", lambda *a: [])
    monkeypatch.setattr(ac, "_find_matching_issue", lambda *a, **k: None)
    return pool


def test_a_failed_top_cluster_falls_through_and_the_run_stops_at_max_issues(db_session, refresh):
    created = action_center._run_refresh(db_session)

    assert refresh["n"] == action_center.CANDIDATE_POOL > action_center.MAX_ISSUES
    assert created == action_center.MAX_ISSUES
    issues = db_session.query(ActionIssue).order_by(ActionIssue.rank).all()
    # Clusters 1 and 2 had no claim; 3 and 5 publish, into slots 1 and 2.
    assert [(i.rank, _cluster_number([i])) for i in issues] == [(1, 3), (2, 5)]
    # Cluster 6 was never tried: both slots were filled after five attempts.
    row = (
        db_session.query(ApiCache)
        .filter(ApiCache.tier == "action-metrics")
        .order_by(ApiCache.cached_at.desc())
        .first()
    )
    assert json.loads(row.data_json)["counts"]["clusters_attempted"] == 5
