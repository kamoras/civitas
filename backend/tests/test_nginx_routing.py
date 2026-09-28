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

# Mutating routes (POST, PUT, PATCH, DELETE) that do their work inside the
# request, in the API process.
# Adding a route here is a claim that it starts no background writer
# (app.background.start_writer / writing) — the API process would refuse it.
SERVED_BY_API = {
    "/api/action/pulse",
    "/api/explore/{doc_id}/comments",
    "/api/explore/{doc_id}/summary",
    "/api/feedback",
    "/api/track-visit",
    "/api/track-timing",
}


_LOCATION = re.compile(r"^\s*location\s+(=|~|\^~)?\s*(\S+)\s*\{(.*?)^\s*\}", re.M | re.S)
# The upstream (the internal api-misses server) the catch-all /api/ hands
# its cache misses to.
_MISSES_HOP = "api_misses"


def _locations() -> list[tuple[str, str, str]]:
    """(modifier, pattern, upstream variable) for every location block of
    the public server — a hop through the internal cache-miss server
    resolved to the upstream that server proxies to."""
    text = CONF.read_text()
    public = text[text.index("listen 8081"):]
    internal = text[text.index("listen 127.0.0.1:8090"):text.index("listen 8081")]
    misses_upstream = re.search(r"proxy_pass\s+http://\$(\w+)", internal).group(1)
    found = []
    for match in _LOCATION.finditer(public):
        modifier, pattern, body = match.group(1) or "", match.group(2), match.group(3)
        upstream = re.search(r"proxy_pass\s+http://(?:\$(\w+)|" + re.escape(_MISSES_HOP) + ")", body)
        if upstream is None:
            found.append((modifier, pattern, ""))
        else:
            found.append((modifier, pattern, upstream.group(1) or misses_upstream))
    return found


def route(path: str) -> str:
    """The upstream nginx proxies `path` to, by nginx's own precedence:
    an exact match; else the longest prefix, taken outright when it is
    `^~`; else the first matching regex; else that longest prefix."""
    locations = _locations()
    for modifier, pattern, upstream in locations:
        if modifier == "=" and pattern == path:
            return upstream
    prefixes = [(p, m, u) for m, p, u in locations if m in ("", "^~") and path.startswith(p)]
    longest = max(prefixes, key=lambda item: len(item[0]), default=None)
    if longest is not None and longest[1] == "^~":
        return longest[2]
    for modifier, pattern, upstream in locations:
        if modifier == "~" and re.search(pattern, path):
            return upstream
    return longest[2] if longest is not None else ""


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
    admin = [(m, p) for m, p, _ in _locations() if p == "/api/admin/"]
    assert admin == [("^~", "/api/admin/")]
    assert route("/api/admin/pipeline/trigger") == "pipeline_upstream"


@pytest.mark.parametrize("path", [
    "/api/senators", "/api/representatives/X000001", "/api/pipeline/status",
    "/api/public/v1/senators", "/api/explore",
])
def test_reads_go_to_the_api(path):
    assert route(path) == "backend_upstream"


def test_cache_misses_of_the_catch_all_are_rate_limited_and_hits_are_not():
    """limit_req before the cache would refuse cache hits: the catch-all
    has none, and the internal server its misses go through does."""
    text = CONF.read_text()
    public = text[text.index("listen 8081"):]
    internal = text[text.index("listen 127.0.0.1:8090"):text.index("listen 8081")]
    catch_all = next(m.group(3) for m in _LOCATION.finditer(public) if m.group(2) == "/api/" and not m.group(1))
    assert "limit_req" not in catch_all and _MISSES_HOP in catch_all
    assert "limit_req zone=api_miss_limit" in internal
    explore = next(m.group(3) for m in _LOCATION.finditer(public) if m.group(2) == "/api/explore")
    assert "limit_req" not in explore and _MISSES_HOP in explore
    assert "limit_req zone=search_miss_limit" in internal
    # The hop mustn't append itself to X-Forwarded-For: its last entry is
    # how the backend identifies clients.
    assert "$proxy_add_x_forwarded_for" not in internal
