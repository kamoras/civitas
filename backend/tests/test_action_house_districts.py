"""Regression test for _house_districts() (2026-07 data-hygiene fix):
api/action.py used to hardcode a second, independent copy of the 50-state
House apportionment table that district_pvi.json already encodes. This
pins the derived version against a few known real values so the two
never silently drift again.
"""

from app.api.action import _house_districts


class TestHouseDistricts:
    def test_known_state_district_counts(self):
        districts = _house_districts()
        assert districts["CA"] == 52
        assert districts["TX"] == 38
        assert districts["WY"] == 1
        assert districts["AK"] == 1

    def test_covers_all_50_states(self):
        assert len(_house_districts()) == 50

    def test_cached_across_calls(self):
        first = _house_districts()
        second = _house_districts()
        assert first is second
