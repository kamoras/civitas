"""A route that awaits nothing runs on the API worker's event loop for as
long as it takes, and every other request that worker holds waits behind
it, the container's liveness check (/api/live) included. Three missed
checks and Swarm replaces the task, so readers get bad gateways until the
new one starts. FastAPI runs a plain `def` route in its threadpool instead,
off the loop. So a route is `async def` only if it awaits something."""

import ast
import asyncio
import pathlib
import time

import httpx

from app.database import get_db, get_visits_db

API = pathlib.Path(__file__).resolve().parent.parent / "app" / "api"

# Routes that stay on the event loop though they await nothing, and why.
ON_THE_LOOP = {
    # The liveness check: it exists to answer from the event loop itself, so
    # a loop frozen by blocking work fails it.
    ("health.py", "live"): "answers from the event loop, to probe it",
    # Puts onto the visit consumer's asyncio.Queue, which is not
    # thread-safe; its docstring says it must stay async.
    ("visits.py", "track_timing"): "puts onto the loop's asyncio.Queue",
}

_ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "api_route"}


def _async_routes_awaiting_nothing() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(API.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                       and d.func.attr in _ROUTE_DECORATORS for d in node.decorator_list):
                continue
            if not any(isinstance(x, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for x in ast.walk(node)):
                found.add((path.name, node.name))
    return found


def test_every_route_that_awaits_nothing_is_a_plain_function():
    blocking = _async_routes_awaiting_nothing() - set(ON_THE_LOOP)
    assert not blocking, (
        f"async def routes that await nothing hold the event loop for their whole run: {sorted(blocking)}. "
        "Make them plain def (FastAPI runs those in its threadpool), or name them in ON_THE_LOOP with why."
    )


def test_every_exception_is_still_an_async_route_awaiting_nothing():
    assert set(ON_THE_LOOP) <= _async_routes_awaiting_nothing()


def test_a_slow_action_center_request_does_not_hold_the_liveness_check(db_session, monkeypatch):
    """The Today tab's request, slowed to 1.5 s: /api/live, asked once it is
    under way, answers before it ends. As async def the request held the
    loop, and the check could not even be sent until it had finished."""
    import threading

    from app.api import action
    from app.main import app

    started, ended = threading.Event(), {}

    def slow_issues(db, for_date=None):
        started.set()
        time.sleep(1.5)
        ended["at"] = time.monotonic()
        return []

    monkeypatch.setattr(action, "_latest_current_issues", slow_issues)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_visits_db] = lambda: db_session

    async def both():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            slow = asyncio.create_task(client.get("/api/action/issues"))
            while not started.is_set():
                await asyncio.sleep(0.01)
            live = await client.get("/api/live")
            answered = time.monotonic()
            assert (await slow).status_code == 200
            return live.status_code, answered

    try:
        status, answered = asyncio.run(both())
    finally:
        app.dependency_overrides.clear()
    assert status == 200
    assert answered < ended["at"], "/api/live was answered only after the Action Center request finished"
