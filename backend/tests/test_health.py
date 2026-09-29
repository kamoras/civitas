"""The container health check's endpoint answers from the event loop alone."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health.router, prefix="/api")
    return TestClient(app)


def test_live_needs_neither_the_database_nor_llama_server():
    # Either dependency failing would raise if /live touched it.
    with patch.object(health, "make_async_client", side_effect=AssertionError("called llama-server")), \
            patch.object(health, "get_db", side_effect=AssertionError("opened the database")):
        resp = _client().get("/api/live")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
