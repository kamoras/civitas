"""Justice scorecard: two measures, no penalty for being outvoted (v6.13).

Judicial Restraint scored dissent frequency, which tracks distance from the
Court's median justice and penalized the smaller bloc for being outvoted;
Bipartisan Agreement measured Independence's construct again. See
justice_analyzer's module docstring and docs/research/justice-scores.md.
"""

import numpy as np
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

import app.database as database
from app.config_definitions import JUSTICE_SCORE_WEIGHTS
from app.pipeline.analyze.justice_analyzer import analyze_justice_votes


def simulate_court(rng, shift, n_cases=40):
    """9 justices, 1-D spatial voting, 6-3 appointing-party split; `shift`
    moves each bloc's ideal points apart (symmetric partisanship)."""
    party = np.array(["R"] * 6 + ["D"] * 3)
    rng.shuffle(party)
    x = rng.normal(0, 1, 9) + np.where(party == "R", shift, -shift)
    ids = [f"j{i}" for i in range(9)]
    party_map = dict(zip(ids, party))
    all_case, per = {}, {i: [] for i in ids}
    for c in range(n_cases):
        cut = rng.normal(0, 1)
        v = (rng.random(9) < 1 / (1 + np.exp(-2 * (x - cut)))).astype(int)
        yes, no = int(v.sum()), 9 - int(v.sum())
        maj = 1 if yes > no else 0
        recs = [dict(case_id=str(c), justice_id=ids[i], vote="majority" if v[i] == maj else "minority",
                     is_unanimous=min(yes, no) == 0, majority_votes=max(yes, no),
                     minority_votes=min(yes, no), opinion_type="", is_close=min(yes, no) == 4)
                for i in range(9)]
        all_case[str(c)] = recs
        for r in recs:
            per[r["justice_id"]].append(r)
    return ids, party_map, per, all_case


def overall(a):
    return sum(a[f"score_{k}"] * w for k, w in JUSTICE_SCORE_WEIGHTS.items())


def test_only_the_two_bloc_measures_are_scored():
    assert set(JUSTICE_SCORE_WEIGHTS) == {"consistency", "independence"}
    assert sum(JUSTICE_SCORE_WEIGHTS.values()) == pytest.approx(1.0)
    # Bipartisan Agreement's weight joined Independence's (same construct).
    assert JUSTICE_SCORE_WEIGHTS["independence"] == pytest.approx(0.45 / 0.80)


def test_the_smaller_bloc_is_not_penalized_for_being_outvoted():
    """Symmetric partisanship on a 6-3 Court: the removed restraint score put
    the 3-member bloc ~21 points behind. The remaining overall must not."""
    rng = np.random.default_rng(1)
    by_bloc = {"R": [], "D": []}
    for _ in range(120):
        ids, party_map, per, all_case = simulate_court(rng, shift=0.75)
        for i in ids:
            a = analyze_justice_votes(i, party_map[i], per[i], all_case, party_map)
            assert "score_judicial_restraint" not in a and "score_bipartisan_agreement" not in a
            by_bloc[party_map[i]].append(overall(a))
    assert abs(np.mean(by_bloc["R"]) - np.mean(by_bloc["D"])) < 3.0


def test_dissent_is_still_reported_as_a_statistic():
    ids, party_map, per, all_case = simulate_court(np.random.default_rng(2), shift=0.0)
    a = analyze_justice_votes(ids[0], party_map[ids[0]], per[ids[0]], all_case, party_map)
    assert 0.0 <= a["dissent_pct"] <= 100.0
    assert set(a["breakdown"]) == {"consistency", "independence"}


def test_migration_drops_the_not_null_columns_so_a_new_justice_can_be_inserted(monkeypatch):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    monkeypatch.setattr(database, "engine", eng)
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE justices (id TEXT PRIMARY KEY, name TEXT,"
            " score_bipartisan_agreement FLOAT NOT NULL, score_judicial_restraint FLOAT NOT NULL)"
        ))
        conn.execute(text("INSERT INTO justices VALUES ('sitting', 'Sitting', 50, 50)"))
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO justices (id, name) VALUES ('new', 'New Justice')"))

    database._migrate_columns()

    cols = {c["name"] for c in inspect(eng).get_columns("justices")}
    assert not cols & {"score_bipartisan_agreement", "score_judicial_restraint"}
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO justices (id, name) VALUES ('new', 'New Justice')"))
        assert conn.execute(text("SELECT COUNT(*) FROM justices")).scalar() == 2
