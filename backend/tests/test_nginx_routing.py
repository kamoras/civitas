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
    "/api/explore/{doc_id}/comments",
    "/api/feedback",
    "/api/track-visit",
    "/api/track-timing",
    # The public API's MCP server: its POSTs are read-only tool calls.
    "/api/public/v1/mcp",
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
    # The Atom feeds are the API's too, under public paths of their own;
    # /photo/ is every image a page shows.
    cached = [loc for loc in _locations(public)
              if "proxy_cache " in loc[3] and loc[1].startswith(("/api/", "/feed", "/photo/"))]
    assert {loc[1] for loc in cached} >= {
        "/api/", "/api/explore", "/api/config", "/api/og", "/feed.xml", "/feed/", "/photo/",
    }
    for modifier, pattern, upstream, body in cached:
        assert "limit_req" not in body and upstream == _MISSES_HOP, pattern
        inner = _match(_locations(internal), pattern)
        assert inner is not None and "limit_req zone=" in inner[3], pattern
    assert "search_miss_limit" in _match(_locations(internal), "/api/explore")[3]
    assert "photo_miss_limit" in _match(_locations(internal), "/photo/bioguide/X000001")[3]
    # The hop mustn't append itself to X-Forwarded-For: its last entry is
    # how the backend identifies clients.
    assert "$proxy_add_x_forwarded_for" not in internal


def test_photos_are_served_by_the_frontend_through_the_miss_hop():
    assert route("/photo/bioguide/X000001") == "frontend_upstream"
    assert route("/photo/justice/x") == "frontend_upstream"


def _directive(body: str, name: str) -> str:
    match = re.search(rf"^\s*{name}\s+([^;]+);", body, re.M)
    assert match, name
    return match.group(1)


def test_a_cold_directory_load_is_paced_not_refused():
    """The directory shows every member's portrait at once, so a reader's
    first visit after the photo cache went cold is that many misses. The
    miss burst must hold them all (an excess would be refused, and that
    portrait left blank), and the public location must wait out the
    pacing of the last one rather than time out on it."""
    public, internal = _servers()
    inner = _directive(_match(_locations(internal), "/photo/x/y")[3], "limit_req")
    burst = int(re.search(r"burst=(\d+)", inner).group(1))
    delay = int(re.search(r"delay=(\d+)", inner).group(1))
    rate = int(re.search(r"zone=photo_miss_limit:\S+ rate=(\d+)r/s", CONF.read_text()).group(1))
    # 535 voting members of Congress, 6 delegates, 9 justices.
    assert burst >= 535 + 6 + 9
    outer = _directive(_match(_locations(public), "/photo/x/y")[3], "proxy_read_timeout")
    assert int(outer.rstrip("s")) > (burst - delay) / rate


def test_explore_summaries_stream_from_the_pipeline_process():
    # A generation outlives its request and is shared by every reader of
    # the text: background work, in the one pipeline process.
    assert route("/api/explore/123/summary") == "pipeline_upstream"
    assert route("/api/explore/123") == "backend_upstream"
    assert route("/api/explore/123/comments") == "backend_upstream"


def test_every_refusal_nginx_makes_on_the_summary_route_is_a_wait():
    # The page asks again after a wait (X-Summary-Wait) and shows anything
    # else as a failure. limit_req and limit_conn refuse with a bare 503 by
    # default, which error_page would not turn into one.
    public, _ = _servers()
    body = _match(_locations(public), "/api/explore/1/summary")[3]
    assert "limit_req_status 429;" in body
    assert "limit_conn_status 429;" in body
    assert "error_page 429 502 504 = @summary_wait;" in body


def test_a_summary_read_goes_through_the_miss_hop_and_is_never_served_stale_while_updating():
    # Its "none yet" answer is a no-store 204 a background refresh can't
    # store: served stale while updating, a changed document would keep its
    # old summary for good.
    public, internal = _servers()
    location = _match(_locations(public), "/api/explore/1/cached-summary")
    assert location is not None and location[2] == _MISSES_HOP
    assert "proxy_cache civitas_cache" in location[3] and "limit_req" not in location[3]
    assert "updating" not in location[3]
    # Nor stale on an error: the page asks the pipeline instead.
    assert "proxy_cache_use_stale off;" in location[3]
    assert "limit_req zone=" in _match(_locations(internal), "/api/explore/1/cached-summary")[3]
    # Nor does any location that follows the backend's Cache-Control: stale
    # while refreshing comes only from a response's own
    # stale-while-revalidate (api/cache_headers.py).
    for path in ("/api/senators", "/api/explore", "/api/action/monitors"):
        assert "updating" not in _match(_locations(public), path)[3], path


def test_only_the_action_center_lists_are_served_stale_while_updating():
    # nginx alone may serve these stale while one request refreshes: their
    # 30s Cache-Control leaves stale-while-revalidate out for browsers, and
    # they answer every request with a cacheable 200, so a refresh always
    # replaces the stale copy. A single issue (a 404 once gone) is not one.
    public, internal = _servers()
    for path in ("/api/action/issues", "/api/action/issues/recent"):
        location = _match(_locations(public), path)
        assert "updating" in location[3] and location[2] == _MISSES_HOP, path
        # In a cache whose short `inactive` bounds how old a stale copy is.
        assert "proxy_cache action_lists_cache;" in location[3], path
        assert "limit_req" not in location[3], path
        assert "limit_req zone=" in _match(_locations(internal), path)[3], path
    for path in ("/api/action/issues/i123", "/api/action/issues/recent/x", "/api/action/monitors"):
        assert "updating" not in _match(_locations(public), path)[3], path
    zone = re.search(r"keys_zone=action_lists_cache:\S+.*?inactive=(\d+)m", CONF.read_text(), re.S)
    assert zone is not None and int(zone.group(1)) <= 5


def test_callers_cannot_claim_the_mcp_channel():
    """Usage counts trust CHANNEL_HEADER (api/public.py); only the MCP
    server's in-process calls may set it, so nginx clears it on the way in."""
    public_server, _ = _servers()
    for path in ("/api/public/v1/senators", "/api/public/v1/mcp"):
        body = _match(_locations(public_server), path)[3]
        assert re.search(r'proxy_set_header\s+X-Civitas-Channel\s+"";', body), path


def test_the_pipeline_restarting_is_an_answer_not_a_bad_gateway():
    # A deploy stops the pipeline before starting the new one; for that half
    # minute every location proxied to it answers 503 with Retry-After
    # (@pipeline_restarting, or the summary route's own wait), not a 502.
    public, _ = _servers()
    to_pipeline = [(p, body) for _, p, upstream, body in _locations(public) if upstream == "pipeline_upstream"]
    assert to_pipeline
    for pattern, body in to_pipeline:
        assert re.search(r"error_page[^;]*\b502\b[^;]*\b504\b[^;]*= @(pipeline_restarting|summary_wait);", body), pattern
    assert "return 503" in public[public.index("location @pipeline_restarting"):]
