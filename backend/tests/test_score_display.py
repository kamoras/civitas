"""Scores as the site shows them (app/score_display.py)."""

from app.score_display import displayed_rank, displayed_score, score_spread


def test_half_rounds_up_like_the_page():
    # Python's round() gives 56 and 62; the page's Math.round gives 57 and 63.
    assert displayed_score(56.5) == 57
    assert displayed_score(62.5) == 63
    assert displayed_score(62.45) == 62


def test_rank_is_competition_rank_on_shown_scores():
    field = [70.4, 69.6, 60.0, 59.5]
    assert [displayed_rank(s, field) for s in field] == [1, 1, 3, 3]


class TestScoreSpread:
    def test_counts_and_quartiles_as_displayed(self):
        spread = score_spread([49.5, 50.4, 61.0, 70.2, 70.4])
        assert spread["total"] == 5
        assert spread["counts"] == [{"score": 50, "members": 2}, {"score": 61, "members": 1},
                                    {"score": 70, "members": 2}]
        assert (spread["p25"], spread["median"], spread["p75"]) == (50, 61, 70)

    def test_one_member_and_none(self):
        assert score_spread([64.6])["median"] == 65
        empty = score_spread([])
        assert empty["counts"] == [] and empty["median"] is None


class TestOneRoundingForShares:
    """A share reads the same on every surface: the findings sentence, the
    peer strip and compare, the race money bars and the scorecard's own
    figures all take their words from shown_share. 2026-10: one small-donor
    share was stored as a whole percent before the display rule ran, and
    shares under 0.5% read "0%" of the money."""

    CASES = {0.195: "20%", 0.125: "13%", 0.004: "under 1%", 0.0: "0%", 0.38: "38%"}

    def test_half_up_and_never_a_false_zero(self):
        from app.score_display import shown_share

        assert {s: shown_share(s) for s in self.CASES} == self.CASES

    def test_every_surface_says_the_same(self):
        from types import SimpleNamespace

        from app import peer_comparison as pc
        from app.race_context import funding_sources
        from app.services.score_findings import add_findings, funding_finding

        for share, words in self.CASES.items():
            facts = {"contributions": 1000, "pacShare": share, "smallDonorShare": share,
                     "smallDonorExpectedShare": None, "smallDonorComparison": "house-median"}
            assert funding_finding(facts) == f"Gifts of $200 or less made up {words} of the money."
            shown = add_findings({"fundingIndependence": {"facts": dict(facts)}}, "house")
            assert shown["fundingIndependence"]["facts"]["shown"]["smallDonorShare"] == words
            assert pc._shown(pc.BY_KEY["smallGifts"], share, facts) == words
            cand = SimpleNamespace(
                contributions=1000.0, individual_unitemized_contributions=share * 1000,
                individual_itemized_contributions=1000 - share * 1000, other_committee_contributions=0.0,
                party_committee_contributions=0.0, candidate_contribution=0.0,
            )
            assert funding_sources(cand)["parts"][0]["shown"] == words
