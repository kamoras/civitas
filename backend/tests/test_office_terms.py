"""data/office_terms.json: every key must be one the pipeline produces, so a
typo cannot silently leave an office without its term."""

import json
import os

from app.office_terms import _PATH, term_years
from app.pipeline.analyze.election_coverage import STATE_NAMES
from app.pipeline.fetch.state_candidates_common import (
    JUDICIAL_COURT_LABELS,
    STATE_LEG_CHAMBER_LABELS,
    STATEWIDE_OFFICE_LABELS,
)

KNOWN = {
    "statewide": set(STATEWIDE_OFFICE_LABELS),
    "legislature": set(STATE_LEG_CHAMBER_LABELS),
    "judicial": set(JUDICIAL_COURT_LABELS),
}


def test_every_entry_names_a_real_state_and_a_known_office():
    with open(_PATH) as f:
        data = json.load(f)
    for kind, known in KNOWN.items():
        for state, offices in data[kind].items():
            assert state in STATE_NAMES, (kind, state)
            for office, years in offices.items():
                assert office in known, (kind, state, office)
                assert isinstance(years, int) and 1 <= years <= 14, (kind, state, office, years)


def test_a_seat_of_a_district_body_uses_the_bodys_term_and_nothing_is_guessed():
    assert term_years("statewide", "GA", "public_service_commission-3") == 6
    assert term_years("legislature", "MD", "lower") == 4
    assert term_years("judicial", "NC", "district") == 4
    # Not listed: no term, rather than a default -- which would have been
    # wrong for New Hampshire's two-year governors before they were listed.
    assert term_years("statewide", "NH", "attorney_general") is None
    assert term_years("statewide", "NH", "governor") == 2


def test_the_file_is_where_the_loader_looks():
    assert os.path.exists(_PATH)
