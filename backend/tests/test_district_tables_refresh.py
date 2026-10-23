"""The district tables rebuild themselves (app/data_refresh.py, AGENTS.md 3a):
which Congresses they need, by rule, and when a rebuild is due.

A table is needed per map in use: a state that kept its map is carried from
an earlier Congress's table (district_lines.table_for_lines), so a new
Congress or a new cycle with no redraw needs nothing rebuilt, and a redraw
(or a new census) does."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import census_lines, data_refresh, district_lines
from app.config import settings

CYCLES_2026 = {"2026": {"TX": {"map": "CD120"}, "MO": {"map": "CD119"}}}
STATES = ("TX", "MO", "NY")


def _table(prefix_states=STATES, n=2):
    return {f"{st}-{d}": [f"{st} place {d}"] for st in prefix_states for d in range(1, n + 1)}


@pytest.fixture
def maps(monkeypatch):
    def use(cycles):
        monkeypatch.setattr(census_lines, "maps_cycles", lambda: cycles)
        monkeypatch.setattr(district_lines, "_maps", lambda: cycles)
    use(CYCLES_2026)
    return use


def _at(freeze_utcnow, monkeypatch, when: datetime, congress: int, cycle: int | None = None):
    freeze_utcnow(when)
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", congress)
    if cycle is not None:  # the election the site is on (active_election, which reads the counts)
        monkeypatch.setattr(data_refresh, "_election", lambda: SimpleNamespace(cycle=cycle))


def test_targets_follow_the_clock_and_the_listing(freeze_utcnow, monkeypatch, maps):
    # Today: the 119th sits, 2026 seats the 120th (listed).
    _at(freeze_utcnow, monkeypatch, datetime(2026, 10, 23, 15, tzinfo=timezone.utc), 119)
    assert data_refresh.line_targets() == (119, 120)
    # After the results window, the site is on 2028, whose maps aren't
    # listed yet: no table is asked for its lines.
    _at(freeze_utcnow, monkeypatch, datetime(2026, 12, 1, 15, tzinfo=timezone.utc), 119, 2028)
    assert data_refresh.line_targets() == (119, 119)
    # The 120th sits on Jan 3, 2027.
    _at(freeze_utcnow, monkeypatch, datetime(2027, 1, 10, 15, tzinfo=timezone.utc), 120, 2028)
    assert data_refresh.line_targets() == (120, 120)
    # 2028 listed (the Census Bureau's file for the 121st is out).
    maps({**CYCLES_2026, "2028": {}})
    assert data_refresh.line_targets() == (120, 121)


def test_a_new_congress_or_cycle_with_no_redraw_needs_nothing_rebuilt(maps):
    tables = {119: _table(), 120: _table()}
    assert data_refresh._uncovered(tables, (120, 120)) == []
    maps({**CYCLES_2026, "2028": {}})
    assert data_refresh._uncovered(tables, (120, 121)) == []


def test_a_redraw_is_due_until_its_table_is_built(maps):
    maps({**CYCLES_2026, "2028": {"NY": {"map": "CD121"}}})
    tables = {119: _table(), 120: _table()}
    assert data_refresh._uncovered(tables, (120, 121)) == [121]
    assert data_refresh._uncovered({**tables, 121: _table()}, (120, 121)) == []


def test_county_command_writes_both_files_by_rule(freeze_utcnow, monkeypatch, maps):
    _at(freeze_utcnow, monkeypatch, datetime(2026, 10, 23, 15, tzinfo=timezone.utc), 119)
    argv = data_refresh._county_command(None)
    assert argv[argv.index("--congress") + 1] == "119"
    assert argv[argv.index("--out") + 1] == "{out:county_district_crosswalk_sitting.json}"
    assert argv[-4:] == ["--congress", "120", "--out", "{out}"]


def test_lean_keeps_every_congress_on_file(freeze_utcnow, monkeypatch, maps):
    _at(freeze_utcnow, monkeypatch, datetime(2027, 1, 10, 15, tzinfo=timezone.utc), 120, 2028)
    maps({**CYCLES_2026, "2028": {}})
    ctx = data_refresh.Context("district_lean.json", {"congresses": {"119": {}, "120": {}}}, None, None)
    argv = data_refresh._lean_command(ctx)
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--congress"] == ["119", "120", "121"]


def test_seats_gate_counts_every_house_seat():
    full = {f"{chr(65 + i // 26)}{chr(65 + i % 26)}-{i}": [] for i in range(435)}
    assert data_refresh._seats_gate(full, "x") == []
    assert data_refresh._seats_gate(dict(list(full.items())[:434]), "x") == ["x: 434 districts, not 435"]


def test_lean_gate_refuses_a_lost_congress_and_a_moved_table():
    full = {f"S-{i}": 1.0 for i in range(435)}
    old = {"congresses": {"119": {"districts": full}, "120": {"districts": full}}}
    assert data_refresh._lean_gate(old, old) == []
    assert data_refresh._lean_gate({"congresses": {"120": {"districts": full}}}, old) == [
        "the 119th Congress's table is gone"]
    moved = {k: (v + 2 if i < 40 else v) for i, (k, v) in enumerate(full.items())}
    new = {"congresses": {"119": {"districts": full}, "120": {"districts": moved}}}
    assert data_refresh._lean_gate(new, old) == [
        "the 120th Congress's table moved 40 of 435 districts by over half a point"]


def test_overlap_follows_the_newest_election_with_a_redraw(freeze_utcnow, monkeypatch, maps):
    _at(freeze_utcnow, monkeypatch, datetime(2026, 10, 23, 15, tzinfo=timezone.utc), 119)
    assert data_refresh._overlap_cycle() == 2026
    _at(freeze_utcnow, monkeypatch, datetime(2027, 6, 1, 15, tzinfo=timezone.utc), 120, 2028)
    assert data_refresh._overlap_cycle() is None  # 2028 not listed: keep 2026's
    maps({**CYCLES_2026, "2028": {}})
    assert data_refresh._overlap_cycle() is None  # listed, nobody redrew: keep 2026's
    maps({**CYCLES_2026, "2028": {"NY": {"map": "CD121"}}})
    assert data_refresh._overlap_cycle() == 2028


def test_every_district_table_has_a_refresh():
    names = {spec.name: spec for spec in data_refresh.DATA_FILES}
    for name in ("county_district_crosswalk.json", "congressional_district_places.json",
                 "state_leg_district_crosswalk.json", "district_lean.json", "redistricting_overlap.json"):
        assert names[name].command is not None and names[name].heavy, name
    assert "county_district_crosswalk_sitting.json" in names["county_district_crosswalk.json"].outputs
