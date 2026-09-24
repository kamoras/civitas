"""scripts/benchmark_validation.py checks v6.13's constructs against
Voteview — run end to end here on a synthetic Senate, since Voteview isn't
reachable from CI."""

import importlib.util
import pathlib
import random
import sqlite3

import pytest

from app.pipeline.analyze import score_calculator

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_validation",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "benchmark_validation.py",
)
bv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bv)

STATES = [f"S{i}" for i in range(50)]


@pytest.fixture
def senate(tmp_path, monkeypatch):
    """50 D + 50 R senators; each one's position and break rate rise with how
    far their seat leans toward the other party, plus a personal deviation
    their (stored) CA score tracks."""
    rng = random.Random(3)
    pvi = {st: rng.randint(-20, 20) for st in STATES}
    monkeypatch.setattr(score_calculator, "_state_pvi_cache", pvi)
    monkeypatch.setattr(score_calculator, "_district_pvi_cache", {})
    members, votes, db_rows = [], [], []
    for i in range(100):
        party = "D" if i < 50 else "R"
        st = STATES[i % 50]
        lean_away = -pvi[st] if party == "R" else pvi[st]  # + = seat leans to the other party
        personal = rng.gauss(0, 1)
        # A higher personal deviation moves the member toward the center:
        # rightward for a Democrat, leftward for a Republican.
        toward_center = 0.05 * personal if party == "D" else -0.05 * personal
        position = (-0.4 if party == "D" else 0.4) + 0.005 * pvi[st] + toward_center
        members.append({"chamber": "Senate", "icpsr": str(i), "bioguide_id": f"B{i}",
                        "state_abbrev": st, "district_code": "0",
                        "party_code": "100" if party == "D" else "200",
                        "nominate_dim1": f"{position:.4f}", "nokken_poole_dim1": f"{position:.4f}"})
        rate = min(max(0.05 + 0.004 * lean_away + 0.03 * personal, 0.0), 0.9)
        db_rows.append((f"B{i}", f"Senator {i}", 50 + 15 * personal, 50.0))
        for roll in range(200):
            # every roll call: D majority Yea, R majority Nay; a member breaks with p=rate
            party_yea = party == "D"
            breaks = rng.random() < rate
            yea = party_yea != breaks
            votes.append({"rollnumber": str(roll), "icpsr": str(i), "cast_code": "1" if yea else "6"})
    db = tmp_path / "civitas.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE senators (bioguide_id TEXT, name TEXT, score_independent_voting REAL, "
                 "score_legislative_effectiveness REAL, is_current INTEGER)")
    conn.executemany("INSERT INTO senators VALUES (?, ?, ?, ?, 1)", db_rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(bv, "DB", f"file:{db}?mode=ro")
    monkeypatch.setattr(bv, "fetch_csv", lambda url: members if "/members/" in url else votes)


def test_the_constructs_correlate_in_the_expected_direction(senate, capsys):
    assert bv.run_chamber("senate", 119, None, "icpsr") == []
    out = capsys.readouterr().out
    assert "CA vs seat-relative break deviation: r = +" in out
    assert "CA vs Nokken-Poole seat-relative extremity: r = -" in out


def test_les_is_checked_when_supplied(senate, tmp_path):
    les = tmp_path / "les.csv"
    les.write_text("ICPSR,LES\n" + "\n".join(f"{i},{1.0 + (i % 7) * 0.1}" for i in range(100)))
    problems = bv.run_chamber("senate", 119, bv.load_les(str(les), "icpsr", "les"), "icpsr")
    # Stored LE is flat (50 for everyone), so the correlation is 0 — reported
    # as a wrong-sign problem rather than silently passing.
    assert any("LE vs CEL" in p for p in problems)
