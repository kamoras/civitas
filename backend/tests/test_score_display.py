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
