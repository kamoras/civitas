"""The Action Center's relevance gate: publisher-filed labels, masking, the
daily refit and the stored reference (app/policy_relevance.py)."""

import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from app import policy_relevance as pr
from app.models import FeedArticle, President, Representative, Senator
from app.pipeline.fetch import news_feeds
from app.pipeline.fetch.news_feeds import OTHER_SCOPE, POLITICS_SCOPE, NewsArticle, publisher_scope

POLITICS_FEED = next(f["url"] for f in news_feeds.NEWS_FEEDS if f.get("scope") == POLITICS_SCOPE)
WORLD_FEED = next(f["url"] for f in news_feeds.NEWS_FEEDS if f.get("scope") == OTHER_SCOPE)
SECTIONED = next(f for f in news_feeds.NEWS_FEEDS if f.get("sections"))


def _article(title, url, feed_url, summary="A description of what happened."):
    return NewsArticle(title=title, url=url, source_name="Outlet", summary=summary, feed_url=feed_url)


class TestPublisherScope:
    def test_a_feed_scope_files_every_item(self):
        assert publisher_scope(_article("x", "https://example.org/a", POLITICS_FEED)) == POLITICS_SCOPE
        assert publisher_scope(_article("x", "https://example.org/b", WORLD_FEED)) == OTHER_SCOPE

    def test_a_section_prefix_files_an_item_of_a_mixed_feed(self):
        for prefix, scope in SECTIONED["sections"].items():
            url = "https://example.org" + prefix + "a-story"
            assert publisher_scope(_article("x", url, SECTIONED["url"])) == scope

    def test_an_unfiled_item_has_no_scope(self):
        assert publisher_scope(_article("x", "https://example.org/video/a", SECTIONED["url"])) is None
        assert publisher_scope(_article("x", "https://example.org/a", "https://unknown.example/feed")) is None


def test_labels_come_only_from_filing_and_named_officials():
    assert pr.publisher_label(POLITICS_SCOPE, False) == 1
    assert pr.publisher_label(OTHER_SCOPE, True) == 1  # a foreign-desk story about a U.S. official
    assert pr.publisher_label(OTHER_SCOPE, False) == 0
    assert pr.publisher_label(None, True) == 1
    assert pr.publisher_label(None, False) is None


class TestOfficialNames:
    names = pr.OfficialNames(["Jane Q. Doe", "Carl Weatherby"], president="Pat R. Example",
                             agencies=[("Example Customs Enforcement", "ECE"), ("ACTION", "ACTION"),
                                       ("Board of Things", "board")])

    def test_full_and_first_last_forms_are_masked(self):
        assert self.names.mask("Jane Q. Doe spoke") == ("[name] spoke", True)
        assert self.names.mask("Jane Doe spoke") == ("[name] spoke", True)

    def test_a_bare_member_surname_is_not_but_the_presidents_is(self):
        assert self.names.mask("Weatherby objected") == ("Weatherby objected", False)
        assert self.names.mask("Example signs an order") == ("[name] signs an order", True)

    def test_an_agency_is_named_but_not_masked(self):
        assert self.names.mask("example customs enforcement raids a plant") == (
            "example customs enforcement raids a plant", True)
        assert self.names.mask("ECE agents arrive") == ("ECE agents arrive", True)
        assert self.names.mask("Ece is a name") == ("Ece is a name", False)
        assert self.names.mask("A call to ACTION") == ("A call to ACTION", False)  # short name = its name
        assert self.names.mask("Weatherbys") == ("Weatherbys", False)

    def test_no_names_masks_nothing(self):
        assert pr.OfficialNames([]).mask("Anyone") == ("Anyone", False)


def test_the_seed_carries_no_hand_label_and_is_labelled_from_outlet_and_names():
    examples = json.loads(pr.SEED_PATH.read_text())["examples"]
    assert all("relevant" not in e and "ambiguous" not in e for e in examples)
    rows = pr.seed_rows()
    assert {lab for _, lab in rows} == {0, 1}
    for text, lab in rows:
        if lab == 0:
            assert pr.NAME_MASK not in text


def test_relevance_vote_is_similarity_weighted():
    reference = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    labels = np.array([1.0, 0.0, 1.0])
    article = np.array([[0.8, 0.6]])
    # k=2: neighbours at cosine 0.8 (positive) and 0.6 (not).
    assert pr.relevance_votes(article, reference, labels, 2)[0] == pytest.approx(0.8 / 1.4)
    # k=3 adds the opposite example, which weighs nothing.
    assert pr.relevance_votes(article, reference, labels, 3)[0] == pytest.approx(0.8 / 1.4)
    assert pr.relevance_votes(article, reference, labels, 50)[0] == pytest.approx(0.8 / 1.4)


def _clusters(n_each, rng, spread=0.15):
    """Unit vectors around two directions: positives near x, negatives near y."""
    pos = np.array([1.0, 0.0, 0.0]) + spread * rng.standard_normal((n_each, 3))
    neg = np.array([0.0, 1.0, 0.0]) + spread * rng.standard_normal((n_each, 3))
    vectors = np.vstack([pos, neg]).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors, np.array([1.0] * n_each + [0.0] * n_each)


def test_fit_separates_separable_labels_and_reports_leave_one_out():
    vectors, labels = _clusters(60, np.random.default_rng(0))
    fitted = pr.fit(vectors, labels)
    assert fitted["k"] in pr.K_GRID and fitted["threshold"] in pr.THRESHOLD_GRID
    assert fitted["precision"] == 1.0 and fitted["recall"] == 1.0
    # Every setting scores the same here, so the lowest threshold is kept.
    assert fitted["threshold"] == pr.THRESHOLD_GRID[0]


def test_a_near_duplicate_does_not_vote_for_its_copy():
    # One positive whose only near neighbour is its own copy among negatives:
    # left out with its copy, it is voted on by the negatives.
    rng = np.random.default_rng(1)
    vectors, labels = _clusters(20, rng)
    odd = np.array([[0.0, 0.2, 1.0]], dtype=np.float32)
    odd /= np.linalg.norm(odd)
    vectors = np.vstack([vectors, odd, odd]).astype(np.float32)
    labels = np.concatenate([labels, [1.0, 1.0]])
    nearest, _ = pr._leave_one_out_votes(vectors, labels, 5)
    assert len(vectors) - 1 not in nearest[len(vectors) - 2]


@pytest.fixture
def fake_embed(monkeypatch):
    """Embeds by keyword: 'Senate' (or a masked name) along x, else along y."""

    def embed(texts):
        out = []
        for t in texts:
            v = np.array([1.0, 0.1, 0.0]) if ("Senate" in t or pr.NAME_MASK in t) else np.array([0.1, 1.0, 0.0])
            out.append(v / np.linalg.norm(v))
        return np.array(out, dtype=np.float32)

    monkeypatch.setattr(pr, "embed", embed)
    seed = np.array([[1.0, 0.3, 0.0], [1.0, 0.0, 0.3], [1.0, -0.3, 0.0],
                     [0.3, 1.0, 0.0], [0.0, 1.0, 0.3], [-0.3, 1.0, 0.0]], dtype=np.float32)
    seed /= np.linalg.norm(seed, axis=1, keepdims=True)
    monkeypatch.setattr(pr, "_seed_reference", lambda: (seed, np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])))
    monkeypatch.setattr(pr, "seed_rows", lambda: [("seed", 1)] * 3 + [("seed", 0)] * 3)
    return embed


def test_judge_keeps_by_vote_and_stores_every_article_with_its_label(db_session, fake_embed, freeze_utcnow):
    freeze_utcnow(datetime(2028, 3, 1, 12))
    db_session.add(Senator(id="jane-doe", name="Jane Doe", state="ZZ", party="I", is_current=True))
    db_session.commit()
    politics = _article("Senate passes a budget", "https://example.org/p1", POLITICS_FEED)
    world = _article("Cup final ends level", "https://example.org/w1", WORLD_FEED)
    named = _article("Jane Doe visits abroad", "https://example.org/w2", WORLD_FEED)
    unfiled = _article("Storm reaches the coast", "https://example.org/u1", "https://unknown.example/feed")

    kept, fitted = pr.judge(db_session, [politics, world, named, unfiled])

    assert {a.url for a, _, _ in kept} == {politics.url, named.url}
    assert fitted["seed_rows"] == 6  # the store is empty: the seed is the reference
    rows = {r.url: r for r in db_session.query(FeedArticle)}
    assert {u: r.label for u, r in rows.items()} == {politics.url: 1, world.url: 0, named.url: 1, unfiled.url: None}
    assert rows[named.url].text.startswith("[name] visits")
    assert rows[named.url].title == "Jane Doe visits abroad"  # the unmasked record, for clustering and replays
    assert rows[named.url].names_official and rows[politics.url].kept and not rows[world.url].kept


def test_the_store_replaces_the_seed_once_it_holds_as_many_of_each_label(db_session, fake_embed, freeze_utcnow):
    """No count is typed: the store takes over once it holds at least the
    seed's number of articles of each label, and not before."""
    freeze_utcnow(datetime(2028, 3, 1, 12))
    seed = [label for _, label in pr.seed_rows()]
    pos, neg = sum(seed), len(seed) - sum(seed)
    vec = fake_embed(["Senate", "Football"])
    for i, label in enumerate([1] * pos + [0] * neg):
        db_session.add(FeedArticle(url=f"https://example.org/s{i}", source_name="O", text="t", names_official=False,
                                   label=label, kept=False, embedding=vec[0 if label else 1].tobytes(),
                                   model=pr._MODEL, seen_at=datetime(2028, 2, 28)))
    db_session.commit()
    assert pr.reference(db_session)[2] == 0
    # One positive short (the one being judged is not its own reference): the seed joins.
    vectors, labels, seeded = pr.reference(db_session, exclude_urls=["https://example.org/s0"])
    assert seeded == len(seed) and len(labels) == pos + neg - 1 + len(seed)


def test_old_and_other_model_rows_leave_the_reference(db_session, fake_embed, freeze_utcnow):
    freeze_utcnow(datetime(2028, 3, 1, 12))
    vec = fake_embed(["Senate"])[0].tobytes()
    db_session.add_all([
        FeedArticle(url="https://example.org/old", source_name="O", text="t", names_official=False, label=1,
                    kept=False, embedding=vec, model=pr._MODEL,
                    seen_at=datetime(2028, 3, 1) - timedelta(days=pr.REFERENCE_DAYS + 1)),
        FeedArticle(url="https://example.org/other", source_name="O", text="t", names_official=False, label=0,
                    kept=False, embedding=vec, model="another-model", seen_at=datetime(2028, 2, 28)),
    ])
    db_session.commit()
    _, labels, seeded = pr.reference(db_session)
    assert seeded == 6 and len(labels) == 6


def test_the_fit_is_refit_once_a_day(db_session, fake_embed, freeze_utcnow, monkeypatch):
    calls = []
    real_fit = pr.fit
    monkeypatch.setattr(pr, "fit", lambda v, y: calls.append(1) or real_fit(v, y))
    vectors, labels = _clusters(20, np.random.default_rng(2))
    freeze_utcnow(datetime(2028, 3, 1, 9))
    pr.calibration(db_session, vectors, labels, 0)
    freeze_utcnow(datetime(2028, 3, 1, 20))
    pr.calibration(db_session, vectors, labels, 0)
    assert len(calls) == 1
    freeze_utcnow(datetime(2028, 3, 2, 1))
    assert pr.calibration(db_session, vectors, labels, 0)["date"] == "2028-03-02"
    assert len(calls) == 2


def test_officials_come_from_the_sitting_roster(db_session):
    db_session.add_all([
        Senator(id="ann-roe", name="Ann Roe", state="ZZ", party="D", is_current=True),
        Senator(id="old-hand", name="Old Hand", state="ZZ", party="R", is_current=False),
        Representative(id="bo-pike", name="Bo Pike", state="ZZ", district=1, party="R", is_current=True),
        President(id=99, name="Pat Example", party="I", number=99, term_start="2029-01-20", is_current=True),
    ])
    db_session.commit()
    names = pr.OfficialNames.from_db(db_session)
    assert names.mask("Ann Roe and Bo Pike met Pat Example")[0] == "[name] and [name] met [name]"
    assert names.mask("Example spoke")[0] == "[name] spoke"
    assert names.mask("Old Hand")[1] is False


def test_a_failed_store_keeps_the_refreshs_own_pending_work(db_session, fake_embed, freeze_utcnow, monkeypatch):
    """The store runs in a savepoint of the refresh's session: when it fails,
    only its own rows go, and nothing the refresh has pending is rolled back
    (or committed) by it."""
    freeze_utcnow(datetime(2028, 3, 1, 12))
    db_session.add(Senator(id="pending-one", name="Pending One", state="ZZ", party="I", is_current=True))
    db_session.flush()  # the refresh's own work, not yet committed

    calls = {"n": 0}

    def scope_then_fail(article):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("store broke mid-batch")
        return POLITICS_SCOPE

    monkeypatch.setattr(pr, "publisher_scope", scope_then_fail)
    articles = [_article("Senate passes a budget", "https://example.org/p1", POLITICS_FEED),
                _article("Senate passes a bill", "https://example.org/p2", POLITICS_FEED)]
    kept, _ = pr.judge(db_session, articles)

    assert len(kept) == 2  # the gate still decided
    assert db_session.query(FeedArticle).count() == 0  # the store's rows rolled back together
    assert db_session.get(Senator, "pending-one") is not None  # the refresh's work survived
    db_session.commit()
    assert db_session.query(Senator).filter_by(id="pending-one").count() == 1


def test_judge_never_commits_the_refreshs_pending_work(db_session, fake_embed, freeze_utcnow):
    """The refresh owns its transaction: a successful judge neither commits
    the refresh's pending work nor its own rows inside it."""
    freeze_utcnow(datetime(2028, 3, 1, 12))
    db_session.add(Senator(id="pending-two", name="Pending Two", state="ZZ", party="I", is_current=True))
    db_session.flush()
    pr.judge(db_session, [_article("Senate passes a budget", "https://example.org/p1", POLITICS_FEED)])
    assert db_session.query(FeedArticle).count() == 1
    db_session.rollback()
    assert db_session.query(Senator).filter_by(id="pending-two").count() == 0
    assert db_session.query(FeedArticle).count() == 0


def _coverage(db, url, title, source, published=datetime(2028, 2, 20)):
    from app.models import Race, RaceCoverageItem

    if db.get(Race, "ZZ-S-2028") is None:
        db.add(Race(id="ZZ-S-2028", cycle_year=2028, office="S", state="ZZ", district=None))
        db.flush()
    db.add(RaceCoverageItem(race_id="ZZ-S-2028", source_type="news", source_name=source, title=title, url=url,
                            summary="", published_at=published))


def test_a_first_run_backfills_the_store_from_stored_articles(db_session, fake_embed, freeze_utcnow):
    """An empty store is filled from the national-feed articles the database
    already holds, labelled by the same rules, before the first vote."""
    freeze_utcnow(datetime(2028, 3, 1, 12))
    politico = next(f["name"] for f in news_feeds.NEWS_FEEDS if f.get("scope") == POLITICS_SCOPE
                    and sum(g["name"] == f["name"] for g in news_feeds.NEWS_FEEDS) == 1)
    world = next(f["name"] for f in news_feeds.NEWS_FEEDS if f.get("scope") == OTHER_SCOPE
                 and sum(g["name"] == f["name"] for g in news_feeds.NEWS_FEEDS) == 1)
    _coverage(db_session, "https://example.org/h1", "Senate committee advances bill", politico)
    _coverage(db_session, "https://example.org/h2", "Cup final ends level", world)
    _coverage(db_session, "https://example.org/h3", "A state outlet's story", "Example State Outlet")
    _coverage(db_session, "https://example.org/old", "Senate old story", politico,
              published=datetime(2028, 3, 1) - timedelta(days=pr.REFERENCE_DAYS + 1))
    db_session.commit()

    pr.judge(db_session, [_article("Senate passes a budget", "https://example.org/p1", POLITICS_FEED)])

    rows = {r.url: r for r in db_session.query(FeedArticle)}
    assert rows["https://example.org/h1"].label == 1 and rows["https://example.org/h2"].label == 0
    assert "https://example.org/h3" not in rows and "https://example.org/old" not in rows  # not a national feed; too old
    assert rows["https://example.org/h1"].vote is None and rows["https://example.org/h1"].kept is False
    assert rows["https://example.org/h1"].seen_at == datetime(2028, 2, 20)  # ages out on its own date


def test_the_backfill_runs_only_on_an_empty_store(db_session, fake_embed, freeze_utcnow):
    freeze_utcnow(datetime(2028, 3, 1, 12))
    pr.judge(db_session, [_article("Senate passes a budget", "https://example.org/p1", POLITICS_FEED)])
    _coverage(db_session, "https://example.org/late", "Senate later story",
              next(f["name"] for f in news_feeds.NEWS_FEEDS if f.get("scope") == POLITICS_SCOPE))
    db_session.commit()
    assert pr.backfill_from_history(db_session, pr.OfficialNames([])) == 0
    assert db_session.query(FeedArticle).filter_by(url="https://example.org/late").count() == 0
