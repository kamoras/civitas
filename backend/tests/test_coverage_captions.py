"""The nightly re-read of stored race coverage whose summary may open with a
photo caption (app/pipeline/fetch/coverage_captions.py)."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.models import Race, RaceCoverageItem
from app.pipeline.fetch import coverage_captions as cc

# The shape of a States Newsroom article page on 2026-10-21 (names changed):
# the featured image's caption only in Yoast's JSON-LD, the excerpt in
# og:description, no <figcaption>.
STATES_NEWSROOM_PAGE = """<html><head>
<meta property="og:description" content="Jane Roe&#8217;s quest to win a U.S. Senate seat as a newcomer doesn&#8217;t sound impossible to a former governor." />
<script type="application/ld+json" class="yoast-schema-graph">{"@context":"https://schema.org","@graph":[
 {"@type":"NewsArticle","headline":"Jane Roe wants to be the fighter in D.C."},
 {"@type":"ImageObject","url":"https://example.com/roe.jpg","caption":"Democratic candidate Jane Roe speaks to Montana Democrats during a gathering in Billings during the summer of 2026 (Photo by A. Photographer for the Example Ledger)"}
]}</script></head><body><p>Jane Roe's quest ...</p></body></html>"""

FIGCAPTION_PAGE = """<html><head><meta name="description" content="The candidates met for their only debate Tuesday."></head>
<body><figure><img src="x.jpg"><figcaption><p>Rep. Pat Doe responds to questions. (Photo by B. Photographer)</p></figcaption></figure>
<p>The candidates met for their only debate Tuesday.</p></body></html>"""

CAPTION_LED = (
    "Democratic candidate Jane Roe speaks to Montana Democrats during a gathering in Billings during "
    "the summer of 2026. She is running for the U.S. Senate (Photo by A. Photographer for the Example "
    "Ledger). Jane Roe's quest to win a U.S. Senate seat as a newcomer doesn't sound impossible."
)


def _item(db, item_id, url, summary, fetched=datetime(2026, 10, 1), checked=None):
    if db.get(Race, "2026-SEN-MT") is None:
        db.add(Race(id="2026-SEN-MT", cycle_year=2026, office="S", state="MT"))
    item = RaceCoverageItem(
        id=item_id, race_id="2026-SEN-MT", source_type="news", source_name="Example Ledger",
        title="t", url=url, summary=summary, fetched_at=fetched, relevance=0.9,
        caption_checked_at=checked,
    )
    db.add(item)
    db.commit()
    return item


def _pages(pages: dict):
    """fetch_with_retry answering from `pages` (url -> html, or None for a
    page that couldn't be read)."""
    calls = []

    async def fake(client, limiter, method, url, **kw):
        calls.append(url)
        html = pages.get(url)
        return None if html is None else SimpleNamespace(text=html)

    return patch.object(cc, "fetch_with_retry", fake), calls


async def test_a_caption_led_summary_is_replaced_by_the_pages_own_description(db_session):
    item = _item(db_session, 1, "https://example.com/roe", CAPTION_LED)
    p, _ = _pages({"https://example.com/roe": STATES_NEWSROOM_PAGE})

    def rescore(db, rows):
        for row in rows:
            row.relevance = 0.7
        db.commit()
        return len(rows)

    with p, patch.object(cc, "score_items", rescore):
        counts = await cc.recheck_stored_captions(db_session)
    db_session.refresh(item)
    assert item.summary == (
        "Jane Roe’s quest to win a U.S. Senate seat as a newcomer doesn’t sound impossible to a former governor."
    )
    assert item.relevance == 0.7  # rescored in the same pass, on the new text
    assert item.caption_checked_at is not None
    assert counts["fixed"] == 1 and counts["rescored"] == 1 and counts["remaining"] == 0


async def test_a_fixed_item_keeps_its_old_score_when_scoring_fails(db_session):
    """Never off its race page in between: no model here, so scoring fails
    (conftest refuses to build one) and the old relevance stands."""
    item = _item(db_session, 1, "https://example.com/roe", CAPTION_LED)
    p, _ = _pages({"https://example.com/roe": STATES_NEWSROOM_PAGE})
    with p:
        counts = await cc.recheck_stored_captions(db_session)
    db_session.refresh(item)
    assert item.summary.startswith("Jane Roe")
    assert (item.relevance, counts["rescored"]) == (0.9, 0)


async def test_a_figcaption_is_recognised_too(db_session):
    item = _item(db_session, 1, "https://example.com/debate",
                 "Rep. Pat Doe responds to questions. The candidates met for their only debate Tuesday.")
    p, _ = _pages({"https://example.com/debate": FIGCAPTION_PAGE})
    with p:
        await cc.recheck_stored_captions(db_session)
    db_session.refresh(item)
    assert item.summary == "The candidates met for their only debate Tuesday."


async def test_a_summary_that_opens_with_the_lede_is_verified_as_it_stands(db_session):
    lede = "Jane Roe's quest to win a U.S. Senate seat as a newcomer doesn't sound impossible."
    item = _item(db_session, 1, "https://example.com/roe", lede)
    p, _ = _pages({"https://example.com/roe": STATES_NEWSROOM_PAGE})
    with p:
        counts = await cc.recheck_stored_captions(db_session)
    db_session.refresh(item)
    assert (item.summary, item.relevance) == (lede, 0.9)
    assert item.caption_checked_at is not None and counts["clean"] == 1


async def test_a_page_that_cant_be_read_is_never_clean(db_session):
    item = _item(db_session, 1, "https://example.com/gone", CAPTION_LED)
    p, calls = _pages({})
    with p:
        counts = await cc.recheck_stored_captions(db_session)
    db_session.refresh(item)
    assert (item.summary, item.caption_checked_at) == (CAPTION_LED, None)
    assert counts["unreadable"] == 1 and counts["remaining"] == 1
    # Left alone for RETRY_UNREADABLE_HOURS, so a dead site can't take every
    # night's budget; then tried again, still unverified until it reads.
    with p:
        await cc.recheck_stored_captions(db_session)
    assert calls == ["https://example.com/gone"]
    with p, patch.object(cc, "RETRY_UNREADABLE_HOURS", 0):
        await cc.recheck_stored_captions(db_session)
    assert calls == ["https://example.com/gone"] * 2
    db_session.refresh(item)
    assert item.caption_checked_at is None


async def test_a_caption_with_no_description_to_replace_it_stays_unverified(db_session):
    page = STATES_NEWSROOM_PAGE.replace('<meta property="og:description"', '<meta property="og:title"')
    item = _item(db_session, 1, "https://example.com/roe", CAPTION_LED)
    p, _ = _pages({"https://example.com/roe": page})
    with p:
        counts = await cc.recheck_stored_captions(db_session)
    db_session.refresh(item)
    assert (item.summary, item.caption_checked_at) == (CAPTION_LED, None)
    assert counts["no_description"] == 1


async def test_bounded_newest_first_and_verified_items_are_skipped(db_session):
    _item(db_session, 1, "https://example.com/old", "Old lede.", fetched=datetime(2026, 9, 1))
    _item(db_session, 2, "https://example.com/new", "New lede.", fetched=datetime(2026, 10, 20))
    _item(db_session, 3, "https://example.com/done", "Done.", checked=datetime(2026, 10, 21))
    pages = {u: "<html></html>" for u in ("https://example.com/old", "https://example.com/new")}
    p, calls = _pages(pages)
    with p:
        counts = await cc.recheck_stored_captions(db_session, limit=1)
    assert calls == ["https://example.com/new"]
    assert counts["remaining"] == 1


def test_a_shared_opening_shorter_than_the_rule_is_not_a_caption():
    assert not cc.opens_with_caption("Jane Roe said Tuesday that she would", ["Jane Roe speaks at a rally in Helena."])
    assert cc.opens_with_caption("JD Vance speaks on stage. The rally drew", ["JD Vance speaks on stage."])
