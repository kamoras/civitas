"""The daily member spotlight: who is picked, and the fixed text posted."""

import re
from unittest.mock import patch

from app.models import BroadcastPost, BskySenatorSpotlight, Representative, Senator
from app.pipeline.analyze.bluesky_spotlight import (
    _pick_politician,
    _publish_spotlight,
    compose_spotlight,
    distinctive_finding,
    distinctiveness,
    post_daily_spotlight,
    profile_url,
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
    (one cycle, one pool) — confirmed with the user rather than assumed."""

    def test_no_candidates_returns_none(self, db_session):
        assert _pick_politician(db_session) == (None, "")

    def test_a_representative_can_be_picked_from_the_combined_pool(self, db_session):
        db_session.add(_senator("senator-a", score=90.0))
        db_session.add(_representative("rep-a", score=95.0))
        db_session.flush()

        with patch(
            "app.pipeline.analyze.bluesky_spotlight.random.choice",
            side_effect=lambda pool: next(p for p in pool if p[1] == "house"),
        ):
            entity, chamber = _pick_politician(db_session)

        assert (entity.id, chamber) == ("rep-a", "house")

    def test_cycle_resets_once_the_combined_pool_is_exhausted(self, db_session):
        db_session.add(_senator("senator-a"))
        db_session.add(_representative("rep-a"))
        db_session.add(BskySenatorSpotlight(senator_id="senator-a", chamber="senate"))
        db_session.add(BskySenatorSpotlight(senator_id="rep-a", chamber="house"))
        db_session.commit()

        entity, chamber = _pick_politician(db_session)

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
            entity, chamber = _pick_politician(db_session)

        assert chamber == "house"


class TestComposeSpotlight:
    def test_no_score_or_rank_is_posted(self):
        s = _senator("Chuck Grantham", score_funding_independence=43.0,
                     score_constituent_alignment=71.25, score_legislative_effectiveness=94.0)
        text = compose_spotlight(s, "senate", "Sponsored no bills this Congress.")
        assert text == "Chuck Grantham (R-IA): Sponsored no bills this Congress."
        assert not re.search(r"Score|#\d|\b43\b|\b71\b|\b94\b", text)

    def test_a_representative_names_their_district(self):
        text = compose_spotlight(_representative("Jane Doe", district=12), "house")
        assert text.startswith("Jane Doe (D-CA-12): ")


class TestPublishSpotlight:
    """The spotlight post's link previously pointed at the old
    /scorecard?branch=senate&state=..&senator=.. query-param route instead
    of the current /politicians/{id} profile page (reported live via a
    Bluesky post 2026-07-13)."""

    def test_links_to_politicians_profile_not_old_scorecard_route(self, db_session, bluesky_configured):
        senator = Senator(id="chuck-grantham", name="Chuck Grantham", state="IA", party="R")

        _publish_spotlight(db_session, "Some spotlight text.", senator, "senate")

        post = db_session.query(BroadcastPost).one()
        assert post.url == "https://civitas-research.org/politicians/chuck-grantham"
        assert bluesky_configured == [("Some spotlight text.", post.url)]
        # Filed under the member's state, for that state's feed.
        assert (post.kind, post.subject, post.state, post.title) == (
            "spotlight", "member:senate:chuck-grantham", "IA", "Member spotlight: Chuck Grantham (R-IA)")

    def test_published_once_a_day_without_a_bluesky_account(self, db_session, bluesky_outbox):
        db_session.add(_senator("Chuck Grantham"))
        db_session.commit()

        post_daily_spotlight(db_session)
        post_daily_spotlight(db_session)

        assert db_session.query(BroadcastPost).count() == 1
        assert db_session.query(BskySenatorSpotlight).count() == 1
        assert bluesky_outbox == []


class TestDistinctiveFinding:
    """The spotlight leads with the finding of the dimension where the
    member stands furthest from the chamber (largest |z| of the stored
    score), never a hand-picked one."""

    def _chamber(self, db_session):
        # Funding sits far out for "outlier"; the others are near the mean.
        db_session.add_all([
            _senator("a", score_funding_independence=50.0, score_constituent_alignment=60.0,
                     score_legislative_effectiveness=40.0),
            _senator("b", score_funding_independence=52.0, score_constituent_alignment=40.0,
                     score_legislative_effectiveness=60.0),
            _senator("outlier", score_funding_independence=90.0, score_constituent_alignment=52.0,
                     score_legislative_effectiveness=48.0),
        ])
        db_session.flush()
        return db_session.query(Senator).filter_by(id="outlier").one()

    def test_the_largest_standardized_deviation_leads(self, db_session):
        outlier = self._chamber(db_session)
        ranked = distinctiveness(outlier, db_session.query(Senator).all())
        assert ranked[0][0] == "fundingIndependence"
        assert ranked[0][1] > ranked[1][1] > 0

    def test_a_missing_finding_falls_through_to_the_next(self, db_session):
        outlier = self._chamber(db_session)
        breakdown = {
            "fundingIndependence": {"finding": None},
            "constituentAlignment": {"finding": "Breaks with the party about as often as Republicans in states that lean the same way."},
            "legislativeEffectiveness": {"finding": "Sponsored no bills this Congress."},
        }
        with patch("app.pipeline.analyze.bluesky_spotlight.get_senator_score_breakdown", return_value=breakdown):
            found = distinctive_finding(db_session, outlier, "senate")
        second = distinctiveness(outlier, db_session.query(Senator).all())[1][0]
        assert found == breakdown[second]["finding"]

    def test_no_breakdown_means_no_finding(self, db_session):
        outlier = self._chamber(db_session)
        with patch("app.pipeline.analyze.bluesky_spotlight.get_senator_score_breakdown", return_value=None):
            assert distinctive_finding(db_session, outlier, "senate") is None


class TestComposeWithFinding:
    FINDING = "Breaks with the party about twice as often as Democrats in states that lean the same way."

    def test_it_leads_with_the_finding_by_name(self):
        s = _senator("Jane Roe", state="NV", party="D", score=60.0)
        text = compose_spotlight(s, "senate", self.FINDING)
        assert text == f"Jane Roe (D-NV): {self.FINDING}"
        assert not re.search(r"\b(he|she|they|his|her|their)\b", text, re.IGNORECASE)

    def test_the_longest_post_fits_with_its_link(self):
        rep = _representative("Alexandria Q. Longername-Hyphenated", district=14, score=55.0)
        rep.id = "alexandria-q-longername-hyphenated"
        # The longest finding template, with a two-digit rate.
        finding = ("Never broke with the party on a party-line vote, though Republicans in districts "
                   "that lean the same way break on 12%.")
        text = compose_spotlight(rep, "house", finding)
        assert len(text) + 1 + len(profile_url(rep)) <= 300
        assert finding in text

    def test_a_finding_that_does_not_fit_gives_way_whole(self):
        s = _senator("Jane Roe", state="NV", party="D", score=60.0)
        s.id = "x" * 250  # a long link leaves no room for the finding
        text = compose_spotlight(s, "senate", self.FINDING)
        assert self.FINDING not in text
        assert text == "Jane Roe (D-NV): money, votes and bills beside colleagues in the same party."

    def test_without_a_finding_it_says_what_the_page_holds(self):
        s = _senator("Jane Roe", state="NV", party="D", score=60.0)
        assert compose_spotlight(s, "senate").endswith("beside colleagues in the same party.")

    def test_the_daily_post_carries_the_finding(self, db_session, bluesky_configured):
        db_session.add(_senator("jane-roe", name="Jane Roe", state="NV", party="D"))
        db_session.commit()
        with patch("app.pipeline.analyze.bluesky_spotlight.distinctive_finding", return_value=self.FINDING):
            post_daily_spotlight(db_session)
        post = db_session.query(BroadcastPost).one()
        assert post.text.startswith(f"Jane Roe (D-NV): {self.FINDING}")
        assert bluesky_configured == [(post.text, "https://civitas-research.org/politicians/jane-roe")]
