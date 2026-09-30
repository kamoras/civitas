"""Smoke test that the FastAPI app actually wires up end to end.

Every other test imports individual modules directly and never touches
app.main / app.api.router — so a broken import anywhere in that chain
(e.g. a stale module path after a rename) can slip through the whole
suite green while the app is actually unable to start. Confirmed the
hard way (2026-07-12): a rename left three files importing a module
path that no longer existed, and CI passed anyway.
"""


def test_app_imports_and_has_routes():
    # app.main imports app.scheduler at module level, and with it every
    # pipeline entry point the scheduler wires up (only ever invoked by
    # cron / the admin panel) — so this import covers that chain too.
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/health" in paths
    assert "/api/live" in paths
    assert any(p.startswith("/api/senators") for p in paths)
    assert any(p.startswith("/api/admin") for p in paths)

