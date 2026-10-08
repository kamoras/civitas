"""The public API's CORS, through the real app's middleware stack.

The site's CORSMiddleware allows the site's own origins only and answers
every preflight before routing, so without api/public.PublicApiPreflight a
preflight from any other origin to the public API got 400 — the open CORS
the spec promises held only for simple GETs."""

import pytest
from fastapi.testclient import TestClient

from app.api.public import PREFIX
from app.api.public_mcp import PATH as MCP_PATH
from app.main import app

client = TestClient(app)
FOREIGN = "https://example.com"


def test_a_preflight_from_any_origin_is_answered_open():
    resp = client.options(
        f"{PREFIX}/senators",
        headers={"Origin": FOREIGN, "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "mcp-protocol-version"},
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "Mcp-Protocol-Version" in resp.headers["access-control-allow-headers"]


def test_a_browser_mcp_client_can_post():
    resp = client.options(
        MCP_PATH,
        headers={"Origin": FOREIGN, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"},
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "POST" in resp.headers["access-control-allow-methods"]


@pytest.mark.parametrize("path,status", [
    ("/nonexistent", 404),            # was 405: a catch-all OPTIONS route matched the path
    ("/senators/a%2Fb/extra", 404),
    ("/search?q=x", 422),
])
def test_errors_are_readable_from_any_origin(path, status):
    """An error is an answer too: without the open origin, a page on
    another origin saw a network error instead of the 404 or 422."""
    resp = client.get(f"{PREFIX}{path}", headers={"Origin": FOREIGN})
    assert resp.status_code == status
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "X-RateLimit-Remaining" in resp.headers["access-control-expose-headers"]
    assert "Retry-After" in resp.headers["access-control-expose-headers"]


def test_the_rest_of_the_api_keeps_the_site_only_policy():
    resp = client.options(
        "/api/senators",
        headers={"Origin": FOREIGN, "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 400
