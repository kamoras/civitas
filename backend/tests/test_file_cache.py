"""app/file_cache.py: module caches of files another process rewrites.

The pipeline process rewrites these files; the API processes cache them.
A writer's own cache reset never reaches a reader in another process, so
each reader reloads when the file's mtime moves. Simulated here by writing
the file behind the module's back, as the other process would."""

import json
import os

import pytest

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


class TestReloadIfMoved:
    """The one rule every runtime-file cache goes through."""

    def test_the_stamp_is_taken_before_the_read(self, tmp_path):
        # A rewrite landing during the read must leave the next call to
        # reload, not pin what was read under the newer stamp.
        import os

        from app.file_cache import reload_if_moved

        path = tmp_path / "f.json"
        path.write_text("1")
        os.utime(path, (1000, 1000))

        def read_then_rewritten():
            value = path.read_text()
            path.write_text("2")
            os.utime(path, (2000, 2000))
            return value

        value, stamp = reload_if_moved([path], None, None, read_then_rewritten)
        assert value == "1"
        value, stamp = reload_if_moved([path], value, stamp, path.read_text)
        assert value == "2"

    def test_an_uncached_result_is_returned_and_retried(self, tmp_path):
        from app.file_cache import Uncached, reload_if_moved

        path = tmp_path / "f.json"
        path.write_text("x")

        def unreadable():
            raise Uncached("fallback")

        value, stamp = reload_if_moved([path], None, None, unreadable)
        assert value == "fallback"
        value, stamp = reload_if_moved([path], value, stamp, lambda: "read")
        assert value == "read"


def test_ballot_lookup_does_not_keep_the_bundled_copy_after_a_transient_read_error(tmp_path, monkeypatch):
    import json

    from app.pipeline.fetch import ballot_lookup

    live = tmp_path / "ballot_lookup.json"
    live.write_text(json.dumps({"live": True}))
    monkeypatch.setattr(ballot_lookup, "_VOLUME_PATH", str(live))
    monkeypatch.setattr(ballot_lookup, "_cache", None)
    real_open = open
    failing = [True]

    def flaky_open(path, *a, **k):
        if str(path) == str(live) and failing[0]:
            raise PermissionError("busy")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", flaky_open)
    assert "live" not in ballot_lookup._load()  # the bundled copy, this once
    failing[0] = False
    assert ballot_lookup._load() == {"live": True}  # not pinned


class TestReadJson:
    def test_absent_and_invalid_are_none_unreadable_raises(self, tmp_path):
        from app.file_cache import read_json

        assert read_json(tmp_path / "missing.json") is None
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert read_json(bad) is None
        with pytest.raises(OSError):
            read_json(tmp_path)  # a directory: exists, can't be read

    def test_prefers_the_first_and_flags_a_skipped_unreadable_one(self, tmp_path):
        from app.file_cache import Uncached, read_json_preferring

        bundled = tmp_path / "bundled.json"
        bundled.write_text('{"b": 1}')
        assert read_json_preferring(tmp_path / "missing.json", bundled, default={}) == {"b": 1}
        with pytest.raises(Uncached) as fell_back:
            read_json_preferring(tmp_path, bundled, default={})  # the runtime copy unreadable
        assert fell_back.value.value == {"b": 1}
