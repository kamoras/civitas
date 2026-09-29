"""A justice's voting record and agreement with the sitting justices.

Unscored since justice v2: the score is loyalty (test_justice_scores.py).
Fictional justice ids throughout, so nothing depends on real names.
"""

from app.pipeline.analyze.justice_analyzer import analyze_justice_votes


def _records(case_id, sides):
    n_maj = sum(1 for s in sides.values() if s == "majority")
    n_min = sum(1 for s in sides.values() if s == "minority")
    return [{
        "case_id": case_id, "justice_id": jid, "vote": side, "opinion_type": "none",
        "is_unanimous": n_min == 0, "is_close": abs(n_maj - n_min) <= 1,
        "majority_votes": n_maj, "minority_votes": n_min,
    } for jid, side in sides.items()]


def test_record_shares_and_agreement():
    cases = {
        "c0": _records("c0", {"a": "majority", "b": "majority", "c": "minority"}),
        "c1": _records("c1", {"a": "minority", "b": "majority", "c": "majority"}),
    }
    mine = [r for recs in cases.values() for r in recs if r["justice_id"] == "a"]
    result = analyze_justice_votes("a", mine, cases, {"a", "b", "c"})
    assert result["cases_decided"] == 2
    assert result["majority_pct"] == 50.0 and result["dissent_pct"] == 50.0
    assert result["agreement_matrix"] == {"b": 50.0, "c": 0.0}
    assert "breakdown" not in result and not any(k.startswith("score_") for k in result)


def test_a_recusal_is_not_agreement():
    # d recused: a shared non-vote must not read as agreeing, nor sit in a
    # denominator.
    cases = {"c0": _records("c0", {"a": "majority", "b": "majority", "d": "recused"})}
    result = analyze_justice_votes("a", [cases["c0"][0]], cases, {"a", "b", "d"})
    assert "d" not in result["agreement_matrix"]
    assert result["agreement_matrix"]["b"] == 100.0


def test_only_sitting_justices_are_in_the_matrix():
    cases = {"c0": _records("c0", {"a": "majority", "retired": "majority"})}
    result = analyze_justice_votes("a", [cases["c0"][0]], cases, {"a"})
    assert result["agreement_matrix"] == {}


def test_no_votes():
    assert analyze_justice_votes("a", [], {}, {"a"})["cases_decided"] == 0
