"""app/file_cache.py: module caches of files another process rewrites.

The pipeline process rewrites these files; the API processes cache them.
A writer's own cache reset never reaches a reader in another process, so
each reader reloads when the file's mtime moves. Simulated here by writing
the file behind the module's back, as the other process would."""

import json
import os

from app.file_cache import files_stamp


def _write(path, data, mtime):
    path.write_text(json.dumps(data))
    os.utime(path, (mtime, mtime))


def test_stamp_is_none_until_a_file_exists(tmp_path):
    assert files_stamp([tmp_path / "a.json", tmp_path / "b.json"]) is None
    _write(tmp_path / "b.json", {}, 1000)
    assert files_stamp([tmp_path / "a.json", tmp_path / "b.json"]) == (None, 1000)


def test_ballot_lookup_sees_a_rewrite_by_another_process(tmp_path, monkeypatch):
    from app.pipeline.fetch import ballot_lookup

    path = tmp_path / "lookup.json"
    monkeypatch.setattr(ballot_lookup, "_VOLUME_PATH", str(path))
    monkeypatch.setattr(ballot_lookup, "_cache", None)
    _write(path, {"CA": {"url": "old"}}, 1000)
    assert ballot_lookup._load()["CA"]["url"] == "old"
    _write(path, {"CA": {"url": "new"}}, 2000)
    assert ballot_lookup._load()["CA"]["url"] == "new"


def test_discovered_sources_see_a_rewrite_by_another_process(tmp_path, monkeypatch):
    from app.pipeline.fetch import state_candidate_sources as sources

    path = tmp_path / "discovered.json"
    monkeypatch.setattr(sources, "_DISCOVERED_PATH", str(path))
    monkeypatch.setattr(sources, "_discovered_cache", None)
    _write(path, {}, 1000)
    assert sources._load_discovered() == {}
    _write(path, {"OH": {"strategy": "x"}}, 2000)
    assert "OH" in sources._load_discovered()


def test_election_dates_see_a_rewrite_by_another_process(tmp_path, monkeypatch):
    from app.pipeline.fetch import state_election_dates as dates

    path = tmp_path / "dates.json"
    monkeypatch.setattr(dates, "_PATH", str(path))
    monkeypatch.setattr(dates, "_cache", None)
    _write(path, {}, 1000)
    assert dates.primary_date("TX", 2026) is None
    _write(path, {"2026-TX": {"primary": "2026-03-03"}}, 2000)
    assert dates.primary_date("TX", 2026) == "2026-03-03"


def test_a_write_landing_between_a_save_and_its_stat_is_not_masked(tmp_path, monkeypatch):
    # Another writer's change lands after this process's save and before it
    # could stat the file: stamping then would pin this process's older copy
    # under the newer file's stamp until the next change.
    import json

    from app.pipeline.fetch import state_election_dates as dates

    path = tmp_path / "dates.json"
    monkeypatch.setattr(dates, "_PATH", str(path))
    monkeypatch.setattr(dates, "_cache", None)
    real_update = dates.update_json_file

    def update_then_another_writer(*args, **kwargs):
        mine = real_update(*args, **kwargs)
        theirs = json.loads(path.read_text())
        theirs["2026-OH"] = {"primary": "2026-05-05"}
        path.write_text(json.dumps(theirs))
        return mine

    monkeypatch.setattr(dates, "update_json_file", update_then_another_writer)
    dates.save("TX", 2026, {"primary": "2026-03-03"})
    assert dates.primary_date("OH", 2026) == "2026-05-05"
    assert dates.primary_date("TX", 2026) == "2026-03-03"


def test_district_pvi_sees_a_rewrite_by_another_process(tmp_path, monkeypatch):
    from app.pipeline.analyze import score_calculator

    monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path))
    monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
    _write(tmp_path / "district_pvi.json", {"districts": {"AL-7": -13}}, 1000)
    assert score_calculator.get_district_pvi_map()["AL-7"] == -13
    _write(tmp_path / "district_pvi.json", {"districts": {"AL-7": -9}}, 2000)
    assert score_calculator.get_district_pvi_map()["AL-7"] == -9


def test_member_ideal_points_see_a_rewrite_by_another_process(tmp_path, monkeypatch):
    from app.pipeline.analyze import score_calculator

    path = tmp_path / "member_ideal_points.json"
    monkeypatch.setattr(score_calculator, "_MEMBER_IDEAL_POINTS_PATH", str(path))
    monkeypatch.setattr(score_calculator, "_member_ideal_points_cache", None)
    _write(path, {"senate": {"members": {"A1": 0.1}}}, 1000)
    assert score_calculator._member_ideal_points("senate")["members"]["A1"] == 0.1
    _write(path, {"senate": {"members": {"A1": 0.4}}}, 2000)
    assert score_calculator._member_ideal_points("senate")["members"]["A1"] == 0.4
