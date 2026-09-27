"""The UI-audit seed (scripts/seed_ui_fixture.py) keeps working as models change."""

import importlib.util
import pathlib

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
    assert db_session.query(Candidate).count() == 17             # /elections/states/CT
