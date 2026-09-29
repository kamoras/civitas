"""nginx/civitas.conf sends background work to the pipeline process.

Production runs the read-only API (PROCESS_ROLE=api) and the pipeline
(PROCESS_ROLE=worker) as separate services, and nginx decides which one a
request reaches. The API process refuses to start background work (a 503),
so a trigger nginx sends to the wrong place fails loudly — but only when
someone presses it. This fails at CI time instead: every POST route the
app has (and PUT, PATCH, DELETE) is either routed to the pipeline, or
listed below as one the API process serves itself.
"""

import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[2] / "nginx" / "civitas.conf"

# Mutating routes (POST, PUT, PATCH, DELETE) served by the API process.
# Adding a route here is a claim that it starts no background writer
# (app.background.start_writer / writing) — the API process would refuse it.
SERVED_BY_API = {
    "/api/action/pulse",
    "/api/explore/{doc_id}/comments",
    "/api/feedback",
    "/api/track-visit",
    "/api/track-timing",
}


_LOCATION = re.compile(r"^\s*location\s+(=|~|\^~)?\s*(\S+)\s*\{(.*?)^\s*\}", re.M | re.S)
# The upstream (the internal api-misses server) the catch-all /api/ hands
# its cache misses to.
_MISSES_HOP = "api_misses"


def _servers() -> tuple[str, str]:
    """(the public server's text, the internal cache-miss server's)."""
    text = CONF.read_text()
    return (text[text.index("listen 8081"):],
            text[text.index("listen 127.0.0.1:8090"):text.index("listen 8081")])


def _locations(server: str) -> list[tuple[str, str, str, str]]:
    """(modifier, pattern, upstream variable or the miss hop, body) for
    every location block of `server`."""
    found = []
    for match in _LOCATION.finditer(server):
        modifier, pattern, body = match.group(1) or "", match.group(2), match.group(3)
        upstream = re.search(r"proxy_pass\s+http://(?:\$(\w+)|(" + re.escape(_MISSES_HOP) + "))", body)
        found.append((modifier, pattern, "" if upstream is None else (upstream.group(1) or upstream.group(2)), body))
    return found


def _match(locations, path: str):
    """The location nginx picks for `path`, by its own precedence: an exact
    match; else the longest prefix, taken outright when it is `^~`; else
    the first matching regex; else that longest prefix."""
    for location in locations:
        if location[0] == "=" and location[1] == path:
            return location
    prefixes = [loc for loc in locations if loc[0] in ("", "^~") and path.startswith(loc[1])]
    longest = max(prefixes, key=lambda loc: len(loc[1]), default=None)
    if longest is not None and longest[0] == "^~":
        return longest
    for location in locations:
        if location[0] == "~" and re.search(location[1], path):
            return location
    return longest


def route(path: str) -> str:
    """The upstream nginx proxies `path` to — through the internal
    cache-miss server when the public location hops there."""
    public, internal = _servers()
    location = _match(_locations(public), path)
    if location is None:
        return ""
    if location[2] == _MISSES_HOP:
        location = _match(_locations(internal), path)
        return location[2] if location is not None else ""
    return location[2]


# Every method that can change something — not only POST: a PUT, PATCH or
# DELETE route that starts background work has to be routed the same way.
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _post_routes() -> list[str]:
    from app.main import app

    return sorted({
        r.path for r in app.routes
        if _MUTATING & set(getattr(r, "methods", None) or ()) and r.path.startswith("/api/")
    })


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_mutating_route_is_routed_or_served_by_the_api():
    unrouted = [
        path for path in _post_routes()
        if path not in SERVED_BY_API and route(_concrete(path)) != "pipeline_upstream"
    ]
    assert unrouted == [], (
        f"{unrouted}: route to $pipeline_upstream in nginx/civitas.conf if it starts "
        "background work, or add it to SERVED_BY_API if it doesn't"
    )


def test_routes_served_by_the_api_stay_there():
    for path in SERVED_BY_API:
        assert route(_concrete(path)) == "backend_upstream", path


@pytest.mark.parametrize("path", [
    "/api/pipeline/trigger",
    "/api/justices/pipeline/trigger",
    "/api/presidents/pipeline/trigger",
    "/api/explore/pipeline/trigger",
    "/api/action/refresh",
    "/api/admin/pipeline/status",
    "/api/admin/data/reset",
])
def test_background_work_goes_to_the_pipeline(path):
    assert route(path) == "pipeline_upstream"


def test_an_admin_path_the_trigger_regex_also_matches_keeps_the_admin_rules():
    # nginx tries regexes before plain prefixes: without `^~` on the admin
    # location, the trigger regex would take this path and skip its
    # local-network allow list.
    admin = [(m, p) for m, p, _, _ in _locations(_servers()[0]) if p == "/api/admin/"]
    assert admin == [("^~", "/api/admin/")]
    assert route("/api/admin/pipeline/trigger") == "pipeline_upstream"


@pytest.mark.parametrize("path", [
    "/api/senators", "/api/representatives/X000001", "/api/pipeline/status",
    "/api/public/v1/senators", "/api/explore",
])
def test_reads_go_to_the_api(path):
    assert route(path) == "backend_upstream"


def test_og_images_are_rendered_by_the_frontend():
    assert route("/api/og") == "frontend_upstream"


def test_every_cached_location_rate_limits_its_misses_and_not_its_hits():
    """limit_req before the cache would refuse cache hits, so no cached
    public location has one; each sends its misses through the internal
    server, whose location for that path does — a cache-busting query
    string gets no unlimited path to the backend or the OG renderer."""
    public, internal = _servers()
    cached = [loc for loc in _locations(public) if "proxy_cache " in loc[3] and loc[1].startswith("/api/")]
    assert {loc[1] for loc in cached} >= {"/api/", "/api/explore", "/api/config", "/api/og"}
    for modifier, pattern, upstream, body in cached:
        assert "limit_req" not in body and upstream == _MISSES_HOP, pattern
        inner = _match(_locations(internal), pattern)
        assert inner is not None and "limit_req zone=" in inner[3], pattern
    assert "search_miss_limit" in _match(_locations(internal), "/api/explore")[3]
    # The hop mustn't append itself to X-Forwarded-For: its last entry is
    # how the backend identifies clients.
    assert "$proxy_add_x_forwarded_for" not in internal


def test_explore_summaries_stream_from_the_pipeline_process():
    # A generation outlives its request and is shared by every reader of
    # the text: background work, in the one pipeline process.
    assert route("/api/explore/123/summary") == "pipeline_upstream"
    assert route("/api/explore/123") == "backend_upstream"
    assert route("/api/explore/123/comments") == "backend_upstream"
