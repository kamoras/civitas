"""scripts/rescore.py previews scores the way a pipeline run would: it
measures this population's references in memory (LES with the same-status
benchmark, funding, constituent) instead of falling back to whatever was
last persisted, and never writes /data."""

import importlib.util
import pathlib
import random
import sqlite3

import pytest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "rescore.py"
_spec = importlib.util.spec_from_file_location("rescore", _PATH)
rescore = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rescore)


def _payload(party, rng):
    return {
        "party": party, "state": "VA",
        "funding": {"totalRaised": 1e6, "totalContributions": 1e6, "totalFromPACs": 2e5,
                    "smallDonorPercentage": 20, "topDonors": [], "industryBreakdown": []},
        "votingRecord": {"keyVotes": [], "recentVotes": [], "effectiveParty": party},
        "sponsoredBills": [{"billType": "s", "congress": 119,
                            "stage": "INTRODUCED" if rng.random() > 0.05 else "PASSED_CHAMBER"}
                           for _ in range(rng.randint(8, 20))],
    }


def test_references_are_measured_in_memory(monkeypatch):
    from app.pipeline.analyze.population_reference import ChamberReference

    def no_write(*_a, **_k):
        raise AssertionError("rescore must not persist references")

    monkeypatch.setattr(ChamberReference, "write", no_write)
    monkeypatch.setattr(rescore.settings, "CURRENT_CONGRESS", 119)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE presidents (party TEXT, is_current INTEGER)")
    conn.execute("INSERT INTO presidents VALUES ('R', 1)")
    rng = random.Random(5)
    parties = ["R"] * 53 + ["D"] * 47
    senators = [{"is_current": 1} for _ in parties]
    payloads = [_payload(p, rng) for p in parties]

    rescore.attach_live_references(conn.cursor(), senators, payloads)

    les = payloads[0]["lesReference"]["senate"]
    assert les["majority"] == "R"
    assert set(les["status_median"]) == {"majority", "minority"}
    assert all(p["lesReference"] is payloads[0]["lesReference"] for p in payloads)


if __name__ == "__main__":
    pytest.main([__file__])
