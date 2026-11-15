"""The whole Action Center refresh (action_center._run_refresh), on a test
database, from feed articles to published issues.

Every outside read is stubbed: the feeds, the trending lists, link
resolution and Bluesky. The relevance vote keeps every article (its own
fit is policy_relevance's tests'). The local model is a stand-in that
points at a claim the way the real one is asked to: an actor and a
predicate copied from the source, which compose() then verifies verbatim.
The embedding model is the member pipelines' stand-in (texts that share
words are near). The rows the refresh stores are pinned in
fixtures/action_refresh_golden.json.
"""

import json
import pathlib
import re
from datetime import datetime, timezone

import pytest

from app.models import ActionIssue, ActionIssueStatus, Senator
from app.pipeline.analyze import action_center as ac
from app.pipeline.analyze import early_signal
from app.pipeline.fetch.news_feeds import NewsArticle
from tests.pipeline_run_helpers import matches_golden, run_on_test_database

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "action_refresh_golden.json"
GOLDEN_NEXT_DAY = GOLDEN.with_name("action_refresh_next_day_golden.json")
NOW = datetime(2026, 10, 15, 16, 0)
PUBLISHED = datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc)

# Two stories, each carried by three outlets from the same wire copy (a
# shared opening, each outlet's own headline and second sentence), and a
# few that one outlet each carries and that name no official.
STOCK = ("Senator Jane Doe introduced a bill on Wednesday that would bar members of Congress "
         "from trading individual stocks.")
PARKS = ("Senator Ron Roe proposed new funding on Wednesday to clear the national parks "
         "maintenance backlog.")
STORIES = [
    ("stock", [
        ("NPR", "Senator Jane Doe introduces bill to bar members of Congress from trading stocks",
         STOCK + " The Campaign Legal Center said the bill would close a loophole."),
        ("PBS", "Senator Jane Doe introduces bill barring members of Congress from trading stocks",
         STOCK + " Senator Mark Lee said he would sign on as a cosponsor."),
        ("The Hill", "Jane Doe introduces bill to bar members of Congress from trading stocks",
         STOCK + " House Speaker Tom Fry declined to say whether the House would take it up."),
    ]),
    ("parks", [
        ("NPR", "Senator Ron Roe proposes new funding for national parks maintenance backlog",
         PARKS + " The National Park Service put the backlog at more than 20 billion dollars."),
        ("Roll Call", "Senator Ron Roe proposes funding for the national parks maintenance backlog",
         PARKS + " The National Parks Conservation Association welcomed the proposal."),
        ("Politico", "Ron Roe proposes new funding for national parks maintenance backlog",
         PARKS + " Interior Secretary Ann Park said the department would review the plan."),
    ]),
    ("storm", [
        ("BBC", "Storm brings heavy rain to coastal towns",
         "A storm brought heavy rain to coastal towns overnight. Residents cleared debris on Wednesday."),
    ]),
    ("rates", [
        ("PBS", "Mortgage rates fall for a third straight week",
         "Average mortgage rates fell for a third week as lenders priced in slower growth."),
    ]),
    ("vaccine", [
        ("BBC", "Researchers report progress on a malaria vaccine trial",
         "Researchers reported early results from a malaria vaccine trial in three countries."),
    ]),
]

SENATORS = [
    ("jane-doe", "D000001", "Jane Doe", "D", "OR"),
    ("ron-roe", "R000001", "Ron Roe", "R", "UT"),
]


def _articles():
    out = []
    for story, tellings in STORIES:
        for i, (outlet, title, summary) in enumerate(tellings):
            slug = re.sub(r"[^a-z]+", "-", outlet.lower())
            out.append(NewsArticle(
                title=title, url=f"https://{slug}.example.org/{story}-{i}", source_name=outlet,
                summary=summary, published=PUBLISHED, feed_url=f"https://{slug}.example.org/politics.rss",
            ))
    return out


_FIRST_LOWER = re.compile(r"\b[a-z]")


def model(*, user_prompt, **_):
    """Points at the first sentence of the source that opens with a name:
    the capitalised words before its first lowercase word, and the rest of
    the sentence. Every other prompt (monitor names, period summaries) gets
    nothing, as a model that failed would give."""
    if "NOW DO THIS ONE\nSource:\n" not in user_prompt:
        return None
    source = user_prompt.split("NOW DO THIS ONE\nSource:\n", 1)[1].split("\n\nRules:", 1)[0]
    for sentence in re.split(r"(?<=[.!?])\s+", source):
        m = _FIRST_LOWER.search(sentence)
        if not m or m.start() == 0:
            continue
        actor, predicate = sentence[:m.start()].strip(), sentence[m.start():].rstrip(".").strip()
        if len(actor.split()) >= 2 and actor[0].isupper() and len(predicate.split()) >= 4:
            return {"actor": actor, "predicate": predicate}
    return None


@pytest.fixture()
def refresh(db_session, monkeypatch, freeze_utcnow):
    freeze_utcnow(NOW)
    run_on_test_database(monkeypatch, db_session, ac)
    for sid, bio, name, party, state in SENATORS:
        db_session.add(Senator(id=sid, bioguide_id=bio, name=name, party=party, state=state, is_current=True))
    db_session.commit()

    def keep_all(session, articles):
        vectors = ac._embed_texts_sim([a.title for a in articles])
        return [(a, vectors[i], 1.0) for i, a in enumerate(articles)], {"k": 7, "threshold": 0.5}

    monkeypatch.setattr(early_signal, "_fetch_recent_votes", lambda session: [])
    monkeypatch.setattr(early_signal, "_fetch_recent_rules", lambda session: [])
    monkeypatch.setattr(ac, "fetch_news_articles", _articles)
    monkeypatch.setattr(ac.policy_relevance, "judge", keep_all)
    monkeypatch.setattr(ac, "fetch_trending_topics", lambda: [])
    monkeypatch.setattr(ac, "_resolve_url", lambda url, timeout=6.0: url)
    monkeypatch.setattr(ac, "call_llm", model)
    monkeypatch.setattr(ac, "_run_periodic_bluesky_posts", lambda session: None)
    monkeypatch.setattr("app.pipeline.analyze.bluesky_poster.process_issues_for_bluesky", lambda issues, session: 0)
    monkeypatch.setattr("app.pipeline.analyze.bluesky_engagement.engage_with_news_posts", lambda session: None)
    return db_session


def test_a_refresh_publishes_the_stories_its_sources_tell(refresh):
    """The parks story (three outlets) leads, the stock story (the two whose
    wording joins; The Hill's telling is the same story and makes no second
    issue) follows, and the stories that name no official publish nothing."""
    assert ac._run_refresh(refresh) == 2
    issues = refresh.query(ActionIssue).order_by(ActionIssue.rank).all()
    assert [(json.loads(i.source_names), i.principal_actor) for i in issues] == [
        (["NPR", "Roll Call", "Politico"], "senate:ron-roe"),
        (["NPR", "PBS"], "senate:jane-doe"),
    ]
    assert matches_golden(refresh, GOLDEN)


def test_the_next_refresh_updates_its_story_and_retires_what_nobody_reports(refresh):
    """Yesterday's issue on the parks story (built from NPR's article, the
    first outlet to carry it) is the row today's coverage updates, not a
    second issue; a current issue no source carries any more
    and older than the grace period leaves the page."""
    yesterday = datetime(2026, 10, 14, 15, 0)
    parks = ActionIssue(
        date="2026-10-14", rank=1, title="Senator Ron Roe proposes new funding for national parks maintenance backlog",
        summary="Senator Ron Roe proposes new funding for national parks maintenance backlog.",
        facts=json.dumps([PARKS]),
        source_names=json.dumps(["NPR"]), source_urls=json.dumps(["https://npr.example.org/parks-0"]),
        status=ActionIssueStatus.CONFIRMED, is_current=True, created_at=yesterday,
    )
    stale = ActionIssue(
        date="2026-10-12", rank=2, title="County clerks report record turnout in early voting",
        summary="County clerks reported record early-voting turnout.",
        source_names=json.dumps(["PBS"]), source_urls=json.dumps(["https://pbs.example.org/turnout"]),
        status=ActionIssueStatus.CONFIRMED, is_current=True, created_at=datetime(2026, 10, 12, 15, 0),
    )
    refresh.add_all([parks, stale])
    refresh.commit()

    assert ac._run_refresh(refresh) == 2
    refresh.refresh(parks)
    refresh.refresh(stale)
    assert parks.is_current and parks.date == "2026-10-15"
    assert json.loads(parks.source_names) == ["NPR", "Roll Call", "Politico"]
    assert not stale.is_current
    assert refresh.query(ActionIssue).count() == 3
    assert matches_golden(refresh, GOLDEN_NEXT_DAY)


def test_no_articles_publishes_nothing(refresh, monkeypatch):
    monkeypatch.setattr(ac, "fetch_news_articles", lambda: [])
    assert ac._run_refresh(refresh) == 0
    assert refresh.query(ActionIssue).count() == 0
    assert ac.action_metrics.snapshot()["refresh_aborted_no_articles"] == 1
