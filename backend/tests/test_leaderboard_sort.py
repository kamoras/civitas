"""get_leaderboard / get_rep_leaderboard sort by score_calculator's shared
compute_overall_score (previously each had its own copy-pasted
_FIELD_TO_WEIGHT_KEY dict + _weighted_score closure computing the identical
SCORE_WEIGHTS-weighted sum)."""

from app.models import Representative, Senator
from app.services.representative_service import get_rep_leaderboard
from app.services.senator_service import get_leaderboard


def _senator(id, name, funding_independence):
    return Senator(
        id=id, name=name, state="CA", party="D",
        score_funding_independence=funding_independence,
        score_promise_persistence=50, score_constituent_alignment=50,
        score_funding_diversity=50, score_legislative_effectiveness=50,
    )


def _rep(id, name, funding_independence):
    return Representative(
        id=id, name=name, state="CA", district=1, party="D",
        score_funding_independence=funding_independence,
        score_promise_persistence=50, score_constituent_alignment=50,
        score_funding_diversity=50, score_legislative_effectiveness=50,
    )


def test_senator_leaderboard_ranks_higher_weighted_score_first(db_session):
    db_session.add(_senator("S001", "Low Scorer", funding_independence=10))
    db_session.add(_senator("S002", "High Scorer", funding_independence=90))
    db_session.commit()

    result = get_leaderboard(db_session)

    assert [r.id for r in result] == ["S002", "S001"]


def test_rep_leaderboard_ranks_higher_weighted_score_first(db_session):
    db_session.add(_rep("R001", "Low Scorer", funding_independence=10))
    db_session.add(_rep("R002", "High Scorer", funding_independence=90))
    db_session.commit()

    result = get_rep_leaderboard(db_session)

    assert [r["id"] for r in result["entries"]] == ["R002", "R001"]


# ── Departed members are excluded (see senator_service.get_leaderboard) ──

def test_senator_leaderboard_excludes_departed_members(db_session):
    serving = _senator("S001", "Still Serving", funding_independence=10)
    departed = _senator("S002", "Left Office", funding_independence=90)
    departed.is_current = False
    departed.left_office_date = "2026-06-01"
    db_session.add_all([serving, departed])
    db_session.commit()

    result = get_leaderboard(db_session)

    # Departed ranks first on score alone — exclusion, not sorting, keeps them out.
    assert [r.id for r in result] == ["S001"]


def test_rep_leaderboard_excludes_departed_members(db_session):
    serving = _rep("R001", "Still Serving", funding_independence=10)
    departed = _rep("R002", "Left Office", funding_independence=90)
    departed.is_current = False
    departed.left_office_date = "2026-06-01"
    db_session.add_all([serving, departed])
    db_session.commit()

    result = get_rep_leaderboard(db_session)

    assert [r["id"] for r in result["entries"]] == ["R001"]
    assert result["total"] == 1


def test_president_leaderboard_still_ranks_former_presidents(db_session):
    """The deliberate exception: a president's only meaningful comparison
    is against the historical field, so leaving office is what QUALIFIES
    them for this ranking rather than removing them from it."""
    from app.models import President
    from app.services.president_service import get_president_leaderboard

    db_session.add(President(
        id="obama-44", name="Barack Obama", party="D", number=44,
        term_start="2009-01-20", term_end="2017-01-20", is_current=False,
        score_public_mandate=70.0, score_effectiveness=70.0,
        score_agency_alignment=70.0, score_historical_legacy=70.0,
    ))
    db_session.commit()

    result = get_president_leaderboard(db_session)

    assert [p.id for p in result] == ["obama-44"]


# ── House: sorted and ranked over the whole chamber, then paginated ──

def test_rep_leaderboard_sorts_by_pac_share_across_pages(db_session):
    """Sorting one page of 50 in the browser ranked members only against
    that page. The server sorts the whole chamber before paginating."""
    for i in range(6):
        r = _rep(f"R{i}", f"Rep {i}", funding_independence=90 - i)  # R0 best score
        r.total_contributions = 1_000_000
        r.total_from_pacs = 100_000 * i  # R5 most PAC-reliant
        db_session.add(r)
    db_session.commit()

    page1 = get_rep_leaderboard(db_session, page=1, per_page=2, sort="pac_pct")
    page2 = get_rep_leaderboard(db_session, page=2, per_page=2, sort="pac_pct")
    assert [e["id"] for e in page1["entries"]] == ["R5", "R4"]
    assert [e["rank"] for e in page2["entries"]] == [3, 4]
    asc = get_rep_leaderboard(db_session, page=1, per_page=2, sort="pac_pct", direction="asc")
    assert [e["id"] for e in asc["entries"]] == ["R0", "R1"]


def test_rep_leaderboard_ties_share_a_rank(db_session):
    db_session.add_all([
        _rep("R1", "Adams", 90), _rep("R2", "Baker", 60), _rep("R3", "Clark", 60), _rep("R4", "Diaz", 10),
    ])
    db_session.commit()
    entries = get_rep_leaderboard(db_session)["entries"]
    assert [(e["id"], e["rank"]) for e in entries] == [("R1", 1), ("R2", 2), ("R3", 2), ("R4", 4)]


def test_rep_leaderboard_missing_values_sort_last_both_ways(db_session):
    a, b, c = _rep("R1", "A", 50), _rep("R2", "B", 50), _rep("R3", "C", 50)
    a.ideology_score, b.ideology_score, c.ideology_score = -0.5, 0.5, None
    db_session.add_all([a, b, c])
    db_session.commit()
    for direction, first in (("asc", "R1"), ("desc", "R2")):
        ids = [e["id"] for e in get_rep_leaderboard(db_session, sort="ideology", direction=direction)["entries"]]
        assert ids[0] == first and ids[-1] == "R3"


def test_rep_leaderboard_members_without_a_value_share_the_last_rank(db_session):
    a, b, c = _rep("R1", "A", 50), _rep("R2", "B", 50), _rep("R3", "C", 50)
    a.ideology_score = 0.1
    db_session.add_all([a, b, c])
    db_session.commit()
    ranks = [e["rank"] for e in get_rep_leaderboard(db_session, sort="ideology")["entries"]]
    assert ranks == [1, 2, 2]


def test_rep_score_ties_round_like_the_page():
    """56.5 shows as 57 (Math.round); banker's rounding would rank it 56."""
    from app.services.representative_service import _half_up

    assert _half_up(56.5) == 57.0 and _half_up(57.5) == 58.0 and _half_up(56.49) == 56.0
