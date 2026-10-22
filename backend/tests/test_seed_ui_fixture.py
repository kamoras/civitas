"""The UI-audit seed (scripts/seed_ui_fixture.py) keeps working as models change."""

import importlib.util
import json
import pathlib
from datetime import datetime

from app.forecast import build
from app.models import Candidate, ExploreDocument, Representative, Senator


def test_seed_populates_every_audited_page(db_session):
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "seed_ui_fixture.py"
    spec = importlib.util.spec_from_file_location("seed_ui_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.seed(db_session)

    assert db_session.get(Senator, "S000") is not None           # /politicians/S000
    assert db_session.get(Representative, "R-CT1") is not None   # /politicians/R-CT1
    assert db_session.get(ExploreDocument, 1) is not None        # /explore/1
    assert db_session.query(Candidate).filter(Candidate.race_id.like("2026-%-CT%")).count() == 17  # /elections/states/CT
    # A state on new lines: /elections/states/UT, /elections/redistricting/UT
    assert db_session.query(Candidate).filter(Candidate.race_id.like("2026-H-UT-%")).count() == 8
    assert db_session.get(Representative, "R-UT1") is not None   # /politicians/R-UT1 (redrawn seat)


def test_seed_builds_the_election_map_with_both_chambers(db_session, freeze_utcnow, tmp_path, monkeypatch):
    """/elections/map is audited with tiles: the seed builds the map from its
    races, offline, while the map is shown."""
    freeze_utcnow(datetime(2026, 10, 1, 15))
    monkeypatch.setattr(build, "map_path", lambda: str(tmp_path / "election_map.json"))
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "seed_ui_fixture.py"
    spec = importlib.util.spec_from_file_location("seed_ui_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.seed(db_session)
    module.seed_election_map(db_session)

    data = json.loads((tmp_path / "election_map.json").read_text())
    assert {c: len(v["seats"]) for c, v in data["chambers"].items()} == {"senate": 1, "house": 9}
