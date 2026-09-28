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

    def test_follows_a_district_pvi_refresh(self, tmp_path, monkeypatch):
        # The pipeline process rewrites district_pvi.json; the count here
        # must follow it rather than keep the copy it first derived.
        import json
        import os

        from app.pipeline.analyze import score_calculator

        monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path))
        monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
        path = tmp_path / "district_pvi.json"
        path.write_text(json.dumps({"districts": {"MT-1": 5, "MT-2": 10}}))
        os.utime(path, (1000, 1000))
        assert _house_districts()["MT"] == 2
        path.write_text(json.dumps({"districts": {"MT-1": 5}}))
        os.utime(path, (2000, 2000))
        assert _house_districts()["MT"] == 1



def test_a_count_is_a_copy_the_caller_may_keep():
    # Counted per call from the public copy-returning accessor: nothing a
    # caller does to the result reaches score_calculator's own cache.
    first = _house_districts()
    first["CA"] = 0
    assert _house_districts()["CA"] == 52
