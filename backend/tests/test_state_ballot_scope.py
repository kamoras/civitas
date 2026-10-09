"""app/data/state_ballot_scope.json: every jurisdiction the state ballot
page serves is listed, each kind well formed."""

import json
from pathlib import Path

from app.pipeline.election_pipeline import federal_states
from app.state_ballot_scope import KINDS, _states, on_november_ballot


def test_every_state_and_dc_is_listed_with_every_kind():
    states = _states()
    assert set(states) == set(federal_states()) | {"DC"}
    for state, entry in states.items():
        for kind in KINDS:
            cycle = entry[kind]
            assert cycle is None or (set(cycle) == {"every", "from"} and cycle["every"] in (1, 2, 4)), (state, kind)


def test_the_file_states_its_source():
    raw = json.loads((Path(__file__).parents[1] / "app" / "data" / "state_ballot_scope.json").read_text())
    assert raw["_source"] and raw["_method"]


def test_cycles():
    assert on_november_ballot("VA", "legislature", 2027) and not on_november_ballot("VA", "legislature", 2026)
    assert on_november_ballot("MD", "legislature", 2026) and not on_november_ballot("MD", "legislature", 2028)
    assert not on_november_ballot("MA", "judicial_contests", 2026)
    assert on_november_ballot("NY", "judicial_contests", 2027)
    # A state the file doesn't know is a gap still named.
    assert on_november_ballot("ZZ", "judicial_retention", 2026)
