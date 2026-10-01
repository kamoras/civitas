"""The daily member spotlight: who is picked, and the fixed text posted."""

from unittest.mock import patch

from app.models import BroadcastPost, BskySenatorSpotlight, Representative, Senator
from app.pipeline.analyze.bluesky_spotlight import (
    _pick_politician,
    _publish_spotlight,
    compose_spotlight,
    post_daily_spotlight,
)


def _senator(id, score=50.0, **overrides):
    kwargs = dict(
        id=id, name=id, state="IA", party="R", is_current=True,
        score_funding_independence=score, score_constituent_alignment=score,
        score_legislative_effectiveness=score,
    )
    kwargs.update(overrides)
    return Senator(**kwargs)


def _representative(id, score=50.0, district=1, **overrides):
    kwargs = dict(
        id=id, name=id, state="CA", district=district, party="D", is_current=True,
        score_funding_independence=score, score_constituent_alignment=score,
        score_legislative_effectiveness=score,
    )
    kwargs.update(overrides)
    return Representative(**kwargs)


class TestPickPolitician:
    """The daily pick is drawn from senators AND representatives combined
    (one cycle, one pool) — confirmed with the user rather than assumed —
    but rank/total must stay per-chamber, matching the site's own
    leaderboard (separate Senate/House tabs), or the post would state a
    number the reader can't find anywhere else on the site."""

    def test_no_candidates_returns_none(self, db_session):
        entity, rank, total, chamber = _pick_politician(db_session)
        assert (entity, rank, total, chamber) == (None, 0, 0, "")

    def test_rank_is_computed_within_the_picked_entitys_own_chamber(self, db_session):
        # The combined pool can pick a representative. Two senators (the
        # rep's raw score would rank #1 among all three combined) — the
        # rep's reported rank must still be "#1 of 1", not
        # "#1 of 3", since House and Senate are ranked separately on the
        # site's own leaderboard.
        db_session.add(_senator("senator-a", score=90.0))
        db_session.add(_senator("senator-b", score=80.0))
        rep = _representative("rep-a", score=95.0)
        db_session.add(rep)
        db_session.flush()

        with patch(
            "app.pipeline.analyze.bluesky_spotlight.random.choice",
            side_effect=lambda pool: next(p for p in pool if p[1] == "house"),
        ):
            entity, rank, total, chamber = _pick_politician(db_session)

        assert (entity.id, rank, total, chamber) == ("rep-a", 1, 1, "house")

    def test_cycle_resets_once_the_combined_pool_is_exhausted(self, db_session):
        db_session.add(_senator("senator-a"))
        db_session.add(_representative("rep-a"))
        db_session.add(BskySenatorSpotlight(senator_id="senator-a", chamber="senate"))
        db_session.add(BskySenatorSpotlight(senator_id="rep-a", chamber="house"))
        db_session.commit()

        entity, rank, total, chamber = _pick_politician(db_session)

        assert entity is not None
        assert db_session.query(BskySenatorSpotlight).count() == 0

    def test_a_senator_and_representative_sharing_an_id_string_are_not_conflated(self, db_session):
        # senator_id + chamber together identify "already spotlighted", not
        # senator_id alone — a representative that happens to share an id
        # with an already-spotlighted senator must still be pickable.
        db_session.add(_senator("j-smith"))
        db_session.add(_representative("j-smith"))
        db_session.add(BskySenatorSpotlight(senator_id="j-smith", chamber="senate"))
        db_session.flush()

        with patch(
            "app.pipeline.analyze.bluesky_spotlight.random.choice",
            side_effect=lambda pool: pool[0],
        ):
            entity, rank, total, chamber = _pick_politician(db_session)

        assert chamber == "house"


class TestComposeSpotlight:
    def test_a_senator_is_their_scores_and_rank_as_numbers(self):
        s = _senator("Chuck Grassley", score_funding_independence=43.0,
                     score_constituent_alignment=71.25, score_legislative_effectiveness=94.0)
        text = compose_spotlight(s, 12, 100, "senate")
        assert text.startswith("Chuck Grassley (R-IA): Representation Score ")
        assert ", #12 of 100 senators. " in text
        assert text.endswith(
            "Funding Independence 43.0, Constituent Alignment 71.2, Legislative Effectiveness 94.0.")

    def test_a_representative_names_their_district(self):
        text = compose_spotlight(_representative("Jane Doe", district=12), 3, 435, "house")
        assert text.startswith("Jane Doe (D-CA-12): ")
        assert "#3 of 435 representatives." in text

    def test_it_fits_a_post_with_its_link(self):
        s = _senator("Elena Ruiz Ortega", state="NV", party="D", score=33.333)
        text = compose_spotlight(s, 100, 100, "senate")
        assert len(text) + 1 + len("https://civitas-research.org/politicians/catherine-cortez-masto") <= 300


class TestPublishSpotlight:
    """The spotlight post's link previously pointed at the old
    /scorecard?branch=senate&state=..&senator=.. query-param route instead
    of the current /politicians/{id} profile page (reported live via a
    Bluesky post 2026-07-13)."""

    def test_links_to_politicians_profile_not_old_scorecard_route(self, db_session, bluesky_configured):
        senator = Senator(id="chuck-grassley", name="Chuck Grassley", state="IA", party="R")

        _publish_spotlight(db_session, "Some spotlight text.", senator, "senate")

        post = db_session.query(BroadcastPost).one()
        assert post.url == "https://civitas-research.org/politicians/chuck-grassley"
        assert bluesky_configured == [("Some spotlight text.", post.url)]
        # Filed under the member's state, for that state's feed.
        assert (post.kind, post.subject, post.state, post.title) == (
            "spotlight", "member:senate:chuck-grassley", "IA", "Member spotlight: Chuck Grassley (R-IA)")

    def test_published_once_a_day_without_a_bluesky_account(self, db_session, bluesky_outbox):
        db_session.add(_senator("Chuck Grassley"))
        db_session.commit()

        post_daily_spotlight(db_session)
        post_daily_spotlight(db_session)

        assert db_session.query(BroadcastPost).count() == 1
        assert db_session.query(BskySenatorSpotlight).count() == 1
        assert bluesky_outbox == []
