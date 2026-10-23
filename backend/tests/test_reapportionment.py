"""A decennial census flows through with no edit: its apportionment is read
from the Census Bureau's table, its first election lists every state on
the new map (gated on that apportionment), and every builder reads the
new census's blocks, county names and P.L. files by rule. The next one is
2030's, first used for 2032's election (the 123rd Congress): the tests run
on synthetic files and, where the clock matters, at a frozen 2032 date."""

import importlib.util
import json
import pathlib
import zipfile
from datetime import datetime, timezone

import pytest

from app import census_lines, data_refresh, district_lines, electoral_votes
from app.config import settings

_BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _BACKEND / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dm = _load("derive_redrawn_maps")
fa = _load("fetch_apportionment")

FIPS = {"48": "TX", "50": "VT"}


def test_each_congress_is_on_the_census_its_election_is_held_under():
    assert census_lines.census_year(118) == census_lines.census_year(122) == 2020  # 2022 to 2030
    assert census_lines.census_year(123) == 2030  # 2032
    assert census_lines.blocks_url(123, "48").endswith("/TIGER2030/TABBLOCK30/tl_2030_48_tabblock30.zip")
    assert census_lines.pl_url("TX", 2030).endswith("/decennial/2030/data/01-Redistricting_File--PL_94-171/Texas/tx2030.pl.zip")
    assert census_lines.county_names_url(2030).endswith("/codes2030/national_county2030.txt")


def test_the_apportionment_table_is_read_and_gated():
    rows = [["STATE", "APPORTIONMENT POPULATION", "NUMBER OF APPORTIONED REPRESENTATIVES"],
            ["Texas", "29183290", "38", "2"], ["Vermont", "643503", "1", "0"],
            ["District of Columbia", "689545", "0"], ["TOTAL", "331108434", "435"]]
    assert fa.seats_from_table(rows) == {"TX": 38, "VT": 1}
    on_file = json.loads((_BACKEND / "app" / "data" / "apportionment.json").read_text())
    assert fa.gate(on_file["censuses"], {}) == []
    assert fa.gate({"2020": {"TX": 38}}, on_file)[0].startswith("2020: 1 states")
    assert fa.gate({}, on_file) == ["censuses on file before and not found now: ['2020']"]
    # Table 1's seats are the seats of the House the 119th Congress's lines draw.
    counties = json.loads((_BACKEND / "app" / "data" / "county_district_crosswalk_sitting.json").read_text())
    per_state = {}
    for key in counties["districts"]:
        per_state[key.split("-")[0]] = per_state.get(key.split("-")[0], 0) + 1
    assert per_state == census_lines.apportionment(2020)


def _bef(path, seats: dict[str, int], blocks_per_seat: int = 2):
    rows = []
    for fips, n in seats.items():
        for d in range(1, n + 1):
            for b in range(blocks_per_seat):
                rows.append(f"{fips}{d:03d}{b:010d},{0 if n == 1 else d:02d}")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("NationalCD123.txt", "GEOID,CDFP\n" + "\n".join(rows) + "\n")
    return path


@pytest.fixture
def census_2030(monkeypatch, tmp_path):
    befs = {123: _bef(tmp_path / "cd123.zip", {"48": 40, "50": 1})}
    monkeypatch.setattr(dm, "find_bef", lambda c: (f"https://census/cd{c}.zip", "2032-07-01 10:00") if c in befs else None)
    monkeypatch.setattr(dm, "_fetch", lambda url, cache: befs[int(url.rsplit("cd", 1)[1].split(".")[0])])
    return befs


def test_the_first_election_after_a_census_lists_every_state_on_the_new_map(monkeypatch, tmp_path, census_2030):
    monkeypatch.setattr(census_lines, "apportionment", lambda census: {"TX": 40, "VT": 1} if census == 2030 else {})
    entry, note = dm.derive(2032, {}, FIPS, tmp_path)
    assert set(entry) == {"TX", "VT"} and {e["map"] for e in entry.values()} == {"CD123"}
    assert {e["kind"] for e in entry.values()} == {"census"} and "new census" in note
    assert "40 seats" in entry["TX"]["reason"] and "1 seat)" in entry["VT"]["reason"]


def test_a_block_file_that_disagrees_with_the_apportionment_is_refused(monkeypatch, tmp_path, census_2030):
    monkeypatch.setattr(census_lines, "apportionment", lambda census: {"TX": 39, "VT": 1})
    entry, note = dm.derive(2032, {}, FIPS, tmp_path)
    assert entry is None and "TX (40 seats, apportioned 39)" in note


def test_no_apportionment_on_file_keeps_the_cycle_pending(monkeypatch, tmp_path, census_2030):
    monkeypatch.setattr(census_lines, "apportionment", lambda census: {})
    entry, note = dm.derive(2032, {}, FIPS, tmp_path)
    assert entry is None and "apportionment is not on file" in note


def test_electoral_votes_follow_the_census_the_election_is_held_under(monkeypatch):
    monkeypatch.setattr(electoral_votes, "apportionment",
                        lambda census: {"TX": 40, "VT": 1} if census == 2030 else {})
    monkeypatch.setattr(electoral_votes, "get_district_pvi_map", lambda: {"TX-1": 0, "TX-2": 0, "VT-0": 0})
    assert electoral_votes.electoral_votes(2032) == {"TX": 42, "VT": 3, "DC": 3}
    # 2028 is held under the 2020 census: before its table is read, the sitting House's seats.
    assert electoral_votes.electoral_votes(2028) == {"TX": 4, "VT": 3, "DC": 3}


def test_after_a_census_every_table_and_shape_rebuilds_on_the_new_map(freeze_utcnow, monkeypatch):
    """2032-07-01: the 122nd sits, the 2032 election seats the 123rd, and
    the maps file lists every state on CD123 (the census entry)."""
    freeze_utcnow(datetime(2032, 7, 1, 15, tzinfo=timezone.utc))
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 122)
    monkeypatch.setattr(data_refresh, "_election", lambda: type("E", (), {"cycle": 2032, "phase": "campaign"})())
    cycles = {**census_lines.maps_cycles(), "2028": {}, "2030": {},
              "2032": {st: {"map": "CD123"} for st in ("TX", "NY", "VT")}}
    monkeypatch.setattr(census_lines, "maps_cycles", lambda: cycles)
    monkeypatch.setattr(district_lines, "_maps", lambda: cycles)
    assert data_refresh.line_targets() == (122, 123)
    tables = {119: {"TX-1": 1, "NY-1": 1, "VT-0": 1}, 120: {"TX-1": 1, "NY-1": 1, "VT-0": 1}}
    assert data_refresh._uncovered(tables, (122, 123)) == [123]
    assert district_lines.lookup_lines("VT", 123) == "congress-123"
    # The computed lean can't carry the old window onto new blocks: its
    # build stops and says what it needs, the refresh keeps the last copy
    # and alerts.
    lean = _load("build_district_lean")
    with pytest.raises(SystemExit, match="drawn on the 2030 census's blocks"):
        lean.build_state("VT", pathlib.Path("/nonexistent"), {}, [], None, [123], cycles)
