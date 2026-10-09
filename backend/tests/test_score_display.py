"""Scores as the site shows them (app/score_display.py)."""

from app.score_display import displayed_rank, displayed_score


def test_half_rounds_up_like_the_page():
    # Python's round() gives 56 and 62; the page's Math.round gives 57 and 63.
    assert displayed_score(56.5) == 57
    assert displayed_score(62.5) == 63
    assert displayed_score(62.45) == 62


def test_rank_is_competition_rank_on_shown_scores():
    field = [70.4, 69.6, 60.0, 59.5]
    assert [displayed_rank(s, field) for s in field] == [1, 1, 3, 3]
