"""State lists a page shows by name are ordered by name (app/state_names.py)."""

from app.state_names import STATE_NAMES, by_state_name


def test_codes_sort_by_the_names_a_reader_sees():
    ordered = sorted(STATE_NAMES, key=by_state_name)
    assert ordered[:2] == ["AL", "AK"]  # Alabama, then Alaska
    assert ordered.index("DE") < ordered.index("DC") < ordered.index("FL")
    assert [STATE_NAMES[c] for c in ordered] == sorted(STATE_NAMES.values())
