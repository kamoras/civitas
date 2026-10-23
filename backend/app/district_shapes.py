"""Where the browser fetches district shapes from (AGENTS.md §8).

The shapes the location finder and the ballot's district map read are
built by scripts/build_district_lookup.py and build_district_topology.py.
The copies bundled with the frontend (frontend/public/data/, described by
app/data/district_shapes.json) cover the maps in use when they were built;
when a needed map has none (a redraw, a new census), the data refresh
builds the shapes on the data volume (scripts/build_district_shapes.py,
one directory per build) and GET /api/district-shapes/ serves them. A
manifest says what each holds, so the API names, per state and map, the
one place that has it: the refresh's build first, then the bundled files;
nothing when neither does (the page then says it can't place the reader,
and links the official lookup).

Folders are named by map ("congress-119": the lines the 119th Congress's
block equivalency file drew), so a state that kept its map is found in an
earlier Congress's folder (district_lines.lookup_lines).
"""

from __future__ import annotations

import os
import pathlib
import re

from app import census_lines, data_refresh
from app.atomic_write import runtime_data_path

FILE = "district_shapes.json"
ROOT_NAME = "district_shapes"  # the refresh's builds, under the data volume
API_PREFIX = "/api/district-shapes"


def _manifests() -> list[dict]:
    """The refresh's build (when it has one), then the bundled files."""
    in_use = data_refresh.read(FILE)
    bundled = census_lines.bundled_json(FILE)
    return [d for d in (in_use, bundled) if d.get("dir")] + [bundled]


def _base(doc: dict) -> str:
    # The bundled files are frontend/public/data/, served at /data (a URL,
    # written here rather than as a module constant, which the tests'
    # data-volume redirect would take for a path on the volume).
    return f"{API_PREFIX}/{doc['dir']}" if doc.get("dir") else "/data"


def lookup_base(folder: str | None, state: str) -> str | None:
    """The URL prefix under which `folder`/`state`.json is served (the
    lookup folder's parent), or None when no build holds it."""
    if not folder:
        return None
    for doc in _manifests():
        if state in ((doc.get("lookup") or {}).get(folder) or []):
            return f"{_base(doc)}/lookup"
    return None


def legislature_lookup(state: str, cycle: int) -> tuple[str | None, str | None]:
    """(folder, URL prefix) of the state legislative shapes for `cycle`'s
    ballot: the newest build for an election at or before it that holds
    the state ("election-2026"), else (None, None)."""
    best = None
    for doc in _manifests():
        for folder, states in (doc.get("lookup") or {}).items():
            if not folder.startswith("election-") or state not in states:
                continue
            year = int(folder.removeprefix("election-"))
            if year <= cycle and (best is None or year > best[0]):
                best = (year, folder, f"{_base(doc)}/lookup")
    return (best[1], best[2]) if best else (None, None)


def district_map_url(state: str, congress: int) -> str | None:
    """The ballot's district map (TopoJSON outlines) for `state` on
    `congress`'s lines, or None when no build holds that state's map."""
    cycles = census_lines.maps_cycles()
    if not census_lines.listed(cycles, congress):
        return None
    want = census_lines.state_maps(congress, [state], cycles)[state]
    for doc in _manifests():
        if ((doc.get("cd") or {}).get("maps") or {}).get(state) == want:
            return f"{_base(doc)}/cd/{state}.json"
    return None


# What a build's directory and file names can be (build_district_shapes.py
# names a build by its UTC time; the files are lookup/<folder>/<ST>[.kind]
# .json and cd/<ST>.json): the route accepts nothing else.
BUILD_RE = re.compile(r"\d{8}T\d{6}Z")
PATH_RE = re.compile(r"(lookup/(congress-\d{1,3}|election-\d{4})/[A-Z]{2}(\.(towns|legislature))?|cd/[A-Z]{2})\.json")


def root() -> pathlib.Path:
    return pathlib.Path(runtime_data_path(ROOT_NAME))


def served_file(build: str, path: str) -> pathlib.Path | None:
    """The file of the refresh's build in use at `path` ("lookup/congress-
    121/TX.json", "cd/TX.json"), or None: only names of the shapes' own
    form, only the build the manifest in use names, checked again on the
    resolved path, so no request can name a file outside that build."""
    if not BUILD_RE.fullmatch(build or "") or not PATH_RE.fullmatch(path or ""):
        return None
    if build != data_refresh.read(FILE).get("dir"):
        return None
    base = os.path.realpath(root() / build)
    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(base + os.sep) or not os.path.isfile(target):
        return None
    return pathlib.Path(target)
