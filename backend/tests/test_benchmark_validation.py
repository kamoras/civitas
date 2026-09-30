"""scripts/benchmark_validation.py checks v6.16's constructs against
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
    their (stored) CA score reflects the way v6.16 scores it: the vote part
    highest at no deviation and falling both ways (faster for breaking
    more), the position part rising toward the seat's center."""
    rng = random.Random(3)
    pvi = {st: rng.randint(-20, 20) for st in STATES}
    monkeypatch.setattr(score_calculator, "_state_pvi_cache", pvi)
    monkeypatch.setattr(score_calculator, "_district_pvi_cache", {})
    monkeypatch.setattr(score_calculator, "_district_pvi_stamp", None)
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
        vote_part = max(0.0, 100 - (40 * personal if personal > 0 else -20 * personal))
        position_part = 50 + 50 * max(-1.0, min(personal / 2, 1.0))
        db_rows.append((f"B{i}", f"Senator {i}", 0.7 * vote_part + 0.3 * position_part, 50.0))
        for roll in range(200):
            # every roll call: D majority Yea, R majority Nay; a member breaks with p=rate
            party_yea = party == "D"
            breaks = rng.random() < rate
            yea = party_yea != breaks
            votes.append({"rollnumber": str(roll), "icpsr": str(i), "cast_code": "1" if yea else "6"})
    db = tmp_path / "civitas.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE senators (bioguide_id TEXT, name TEXT, score_constituent_alignment REAL, "
                 "score_legislative_effectiveness REAL, is_current INTEGER)")
    conn.executemany("INSERT INTO senators VALUES (?, ?, ?, ?, 1)", db_rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(bv, "DB", f"file:{db}?mode=ro")
    monkeypatch.setattr(bv, "fetch_csv", lambda url: members if "/members/" in url else votes)


def test_the_constructs_correlate_in_the_expected_direction(senate, capsys):
    assert bv.run_chamber("senate", 119, None, "icpsr") == []
    out = capsys.readouterr().out
    assert "CA vs seat-relative vote shape (Voteview): r = +" in out
    assert "CA vs Constituent Alignment recomputed from Voteview: r = +" in out


def test_les_is_checked_when_supplied(senate, tmp_path):
    les = tmp_path / "les.csv"
    les.write_text("ICPSR,LES\n" + "\n".join(f"{i},{1.0 + (i % 7) * 0.1}" for i in range(100)))
    problems = bv.run_chamber("senate", 119, bv.load_les(str(les), "icpsr", "les", 119), "icpsr")
    # Stored LE is flat (50 for everyone), so the correlation is 0 — reported
    # as a wrong-sign problem rather than silently passing.
    assert any("LE vs CEL" in p for p in problems)


def test_les_reads_only_the_checked_congress(tmp_path):
    """CEL's file has a row per member-congress. Unfiltered, each member's
    last row would win, so an old congress's score stood in for this one's."""
    les = tmp_path / "cel.csv"
    les.write_text(
        "Congress number,ICPSR number according to Poole and Rosenthal,LES 1.0,LES 2.0\n"
        "117,14226,0.5,0.6\n"
        "118,14226.0,1.5,1.6\n"
        "118,20001,,\n"
        "116,14226,9.0,9.0\n"
    )
    assert bv.load_les(str(les), "icpsr", "les 1.0", 118) == {"14226": 1.5}
    with pytest.raises(SystemExit, match="119th"):
        bv.load_les(str(les), "icpsr", "les 1.0", 119)


def test_les_without_a_congress_column_rejects_duplicate_members(tmp_path):
    les = tmp_path / "les.csv"
    les.write_text("icpsr,les\n1,0.5\n1,0.7\n")
    with pytest.raises(SystemExit, match="more than one row"):
        bv.load_les(str(les), "icpsr", "les", 118)
