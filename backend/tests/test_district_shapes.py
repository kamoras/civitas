"""Where the browser fetches district shapes (app/district_shapes.py), the
route serving a data refresh's build of them, and when that build is due."""

import importlib.util
import json
import pathlib
from datetime import datetime, timezone

import pytest

from app import census_lines, data_refresh, district_lines, district_shapes
from app.config import settings

REPO = pathlib.Path(__file__).resolve().parents[2]
BUILD = "20280801T030000Z"
CYCLES = census_lines.maps_cycles()


@pytest.fixture
def maps(monkeypatch):
    def use(cycles):
        monkeypatch.setattr(census_lines, "maps_cycles", lambda: cycles)
        monkeypatch.setattr(district_lines, "_maps", lambda: cycles)
    return use


@pytest.fixture
def built(monkeypatch, tmp_path):
    """A refresh's build in use holding the 121st's New York lines."""
    doc = {"dir": BUILD, "lookup": {"congress-121": ["NY"]}, "cd": {"congress": 121, "maps": {"NY": 121}}}
    monkeypatch.setattr(data_refresh, "read", lambda name: doc if name == district_shapes.FILE else {})
    monkeypatch.setattr(district_shapes, "root", lambda: tmp_path)
    (tmp_path / BUILD / "lookup" / "congress-121").mkdir(parents=True)
    (tmp_path / BUILD / "lookup" / "congress-121" / "NY.json").write_text("{}")
    return doc


def test_the_bundled_manifest_describes_the_bundled_files():
    spec = importlib.util.spec_from_file_location("bds", REPO / "backend" / "scripts" / "build_district_shapes.py")
    bds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bds)
    bundled = json.loads((REPO / "backend" / "app" / "data" / "district_shapes.json").read_text())
    described = bds.describe(REPO / "frontend" / "public" / "data", bundled["cd"]["congress"], None)
    assert {k: v for k, v in described.items() if k != "_source"} == {
        k: v for k, v in bundled.items() if k != "_source"}


def test_the_bundled_files_answer_today_s_maps():
    assert district_shapes.lookup_base("congress-119", "NY") == "/data/lookup"
    assert district_shapes.lookup_base("congress-120", "TX") == "/data/lookup"
    assert district_shapes.lookup_base("congress-120", "NY") is None  # NY kept its map: congress-119
    assert district_shapes.legislature_lookup("NY", 2026) == ("election-2026", "/data/lookup")
    assert district_shapes.legislature_lookup("NY", 2028) == ("election-2026", "/data/lookup")
    assert district_shapes.legislature_lookup("NY", 2024) == (None, None)
    assert district_shapes.district_map_url("TX", 120) == "/data/cd/TX.json"


def test_a_redraw_is_served_from_the_refresh_s_build_once_built(maps, built):
    maps({**CYCLES, "2028": {"NY": {"map": "CD121"}}})
    assert district_shapes.lookup_base("congress-121", "NY") == f"/api/district-shapes/{BUILD}/lookup"
    assert district_shapes.district_map_url("NY", 121) == f"/api/district-shapes/{BUILD}/cd/NY.json"
    # A state that kept its map is still the bundled files'.
    assert district_shapes.district_map_url("TX", 121) == "/data/cd/TX.json"
    assert district_shapes.lookup_base("congress-121", "PA") is None


def test_a_map_no_build_holds_is_named_nowhere(maps):
    maps({**CYCLES, "2028": {"NY": {"map": "CD121"}}})
    assert district_shapes.district_map_url("NY", 121) is None
    assert district_shapes.lookup_base("congress-121", "NY") is None


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.router import api_router

    app = FastAPI()
    app.include_router(api_router)
    return TestClient(app)


def test_the_route_serves_only_the_build_in_use_and_nothing_outside_it(built, client):
    ok = client.get(f"/api/district-shapes/{BUILD}/lookup/congress-121/NY.json")
    assert ok.status_code == 200 and "immutable" in ok.headers["cache-control"]
    for path in ("/api/district-shapes/20270101T000000Z/lookup/congress-121/NY.json",
                 f"/api/district-shapes/{BUILD}/lookup/congress-121/PA.json",
                 f"/api/district-shapes/{BUILD}/../district_shapes.json",
                 f"/api/district-shapes/{BUILD}/lookup/%2e%2e/%2e%2e/secret.json"):
        assert client.get(path).status_code == 404, path


def test_the_refresh_builds_shapes_only_for_a_map_none_holds(freeze_utcnow, monkeypatch, maps):
    freeze_utcnow(datetime(2027, 6, 1, 15, tzinfo=timezone.utc))
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 120)
    monkeypatch.setattr(data_refresh, "_election", lambda: type("E", (), {"cycle": 2028})())
    ctx = data_refresh.Context(district_shapes.FILE, {}, None, None)
    assert data_refresh._shapes_missing(ctx) == []  # a new Congress, no redraw
    maps({**CYCLES, "2028": {}})
    assert data_refresh._shapes_missing(ctx) == []  # 2028 listed, nobody redrew
    maps({**CYCLES, "2028": {"NY": {"map": "CD121"}}})
    assert data_refresh._shapes_missing(ctx) == ["congress-121/NY", "cd/NY on the 121st Congress's map"]
    argv = data_refresh._shapes_command(ctx)
    assert argv[argv.index("--cycle") + 1] == "2028"
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--congress"] == ["120", "121"]
