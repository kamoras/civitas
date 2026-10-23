"""scripts/derive_redrawn_maps.py: an election's maps from the Census
Bureau's block equivalency files, and the reader that serves the derived
copy (live_results.sync._maps)."""

import importlib.util
import json
import pathlib
import zipfile

from app.live_results import sync

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("derive_redrawn_maps", _BACKEND / "scripts" / "derive_redrawn_maps.py")
dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm)

FIPS = {"48": "TX", "29": "MO", "50": "VT"}


def _bef(path: pathlib.Path, rows: dict[str, str], own: list[str]) -> pathlib.Path:
    """A BEF zip: the national file, and a file of its own per state in `own`."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("NationalCD.txt", "GEOID,CDFP\n" + "".join(f"{g},{cd}\n" for g, cd in rows.items()))
        for fips in own:
            zf.writestr(f"CD_{fips}.txt", "x")
    return path


def _blocks(tx: list[str], mo: list[str]) -> dict[str, str]:
    rows = {f"48{i:013d}": cd for i, cd in enumerate(tx)}
    rows.update({f"29{i:013d}": cd for i, cd in enumerate(mo)})
    rows["500000000000001"] = "00"
    return rows


def _setup(monkeypatch, tmp_path, befs: dict[int, pathlib.Path]):
    monkeypatch.setattr(dm, "find_bef", lambda c: (f"https://census/cd{c}.zip", "2028-07-01 10:00") if c in befs else None)
    monkeypatch.setattr(dm, "_fetch", lambda url, cache: befs[int(url.rsplit("cd", 1)[1].split(".")[0])])


def test_a_state_whose_blocks_moved_is_listed_on_the_new_map(monkeypatch, tmp_path):
    befs = {
        120: _bef(tmp_path / "120.zip", _blocks(["01", "02", "02"], ["01", "02"]), []),
        121: _bef(tmp_path / "121.zip", _blocks(["01", "01", "02"], ["01", "02"]), ["48"]),
    }
    _setup(monkeypatch, tmp_path, befs)
    cycles = {"2026": {"TX": {"map": "CD120"}, "MO": {"map": "CD119"}}}
    befs[119] = befs[120]  # MO's 2026 map is the 119th's, identical here
    entry, note = dm.derive(2028, cycles, FIPS, tmp_path)
    assert sorted(entry) == ["TX"], note
    assert entry["TX"]["map"] == "CD121" and entry["TX"]["source"]["issuedBy"].startswith("U.S. Census Bureau")
    assert "121st" in entry["TX"]["reason"]


def test_no_state_moved_is_an_empty_entry(monkeypatch, tmp_path):
    rows = _blocks(["01", "02"], ["01", "02"])
    befs = {119: _bef(tmp_path / "119.zip", rows, []), 120: _bef(tmp_path / "120.zip", rows, []),
            121: _bef(tmp_path / "121.zip", rows, [])}
    _setup(monkeypatch, tmp_path, befs)
    entry, _ = dm.derive(2028, {"2026": {"TX": {"map": "CD120"}}}, FIPS, tmp_path)
    assert entry == {}


def test_no_file_yet_leaves_the_cycle_unconfirmed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, {})
    entry, note = dm.derive(2028, {}, FIPS, tmp_path)
    assert entry is None and "121st Congress" in note


def test_moved_blocks_without_the_state_s_own_file_are_refused(monkeypatch, tmp_path):
    befs = {119: _bef(tmp_path / "119.zip", _blocks(["01", "02"], ["01", "02"]), []),
            121: _bef(tmp_path / "121.zip", _blocks(["02", "02"], ["01", "02"]), [])}
    _setup(monkeypatch, tmp_path, befs)
    entry, note = dm.derive(2028, {}, FIPS, tmp_path)
    assert entry is None and "none of its own" in note


def test_a_new_census_s_blocks_are_refused(monkeypatch, tmp_path):
    befs = {119: _bef(tmp_path / "119.zip", _blocks(["01", "02"], ["01", "02"]), []),
            121: _bef(tmp_path / "121.zip", _blocks(["01", "02", "02"], ["01", "02"]), ["48"])}
    _setup(monkeypatch, tmp_path, befs)
    entry, note = dm.derive(2028, {}, FIPS, tmp_path)
    assert entry is None and "new census" in note


def test_build_keeps_the_researched_cycles_and_records_what_is_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(dm, "fips_states", lambda cache: FIPS)
    _setup(monkeypatch, tmp_path, {})
    doc = dm.build(2028, tmp_path)
    assert doc["cycles"] == json.loads(dm.BUNDLED.read_text())["cycles"]
    assert set(doc["_pending"]) == {"2028"} and doc["_derived"] == {}


def test_the_reader_serves_the_derived_copy(monkeypatch, tmp_path):
    runtime = pathlib.Path(sync.runtime_data_path(sync.MAPS_NAME))
    runtime.parent.mkdir(parents=True, exist_ok=True)
    bundled = json.loads(dm.BUNDLED.read_text())
    runtime.write_text(json.dumps({**bundled, "cycles": {**bundled["cycles"], "2028": {}},
                                   "_pending": {}}))
    monkeypatch.setattr(sync, "_redrawn_cache", None)
    monkeypatch.setattr(sync, "_redrawn_stamp", None)
    assert "2028" in sync._maps()
    runtime.write_text(json.dumps({**bundled, "_pending": {"2028": "no file yet"}}))
    assert "2028" not in sync._maps() and sync.maps_pending() == {"2028": "no file yet"}
