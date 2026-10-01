"""The public API's MCP server (api/public_mcp.py), spoken to over its own
transport — JSON-RPC over streamable HTTP, as an MCP client would."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api import public
from app.api.public_mcp import PATH, McpEndpoint
from app.api.router import api_router
from app.database import get_db
from app.models import Senator

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@asynccontextmanager
async def mcp_client(db_session):
    """An initialized MCP connection. Entered inside each test rather than as
    a fixture: the session manager's task group must be exited by the task
    that entered it, and pytest-asyncio tears fixtures down in another."""
    db_session.add(Senator(
        id="jon-ossoff", name="Jon Ossoff", state="GA", party="D",
        score_funding_independence=60, score_promise_persistence=50, score_constituent_alignment=55,
        score_funding_diversity=40, score_legislative_effectiveness=70,
    ))
    db_session.commit()
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    endpoint = McpEndpoint()
    app.add_route(PATH, endpoint, methods=["GET", "POST", "DELETE"])
    ids = iter(range(1, 1000))

    async with endpoint.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://civitas",
    ) as client:
        async def rpc(method: str, params: dict | None = None) -> dict:
            resp = await client.post(PATH, headers=HEADERS, json={
                "jsonrpc": "2.0", "id": next(ids), "method": method, "params": params or {},
            })
            assert resp.status_code == 200, resp.text
            assert resp.headers["access-control-allow-origin"] == "*"
            body = resp.json()
            assert "error" not in body, body
            return body["result"]

        init = await rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        })
        assert init["serverInfo"]["name"] == "civitas"
        yield rpc


async def test_every_public_endpoint_is_a_read_only_tool(db_session):
    async with mcp_client(db_session) as mcp:
        tools = {t["name"]: t for t in (await mcp("tools/list"))["tools"]}
        documented = {
            op["operationId"] for ops in public.openapi_spec_dict()["paths"].values() for op in ops.values()
        }
        assert set(tools) == documented
        assert {"list_senators", "get_senator", "search_documents"} <= set(tools)
        get_senator = tools["get_senator"]
        assert get_senator["inputSchema"]["required"] == ["senator_id"]
        assert get_senator["annotations"]["readOnlyHint"] is True
        party = tools["list_senators"]["inputSchema"]["properties"]["party"]
        assert "D" in party["anyOf"][0]["enum"]


async def test_a_tool_call_answers_what_the_http_route_does(db_session):
    async with mcp_client(db_session) as mcp:
        result = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "jon-ossoff"}})
        assert not result.get("isError")
        assert result["structuredContent"]["name"] == "Jon Ossoff"
        assert result["structuredContent"]["siteUrl"].endswith("/politicians/jon-ossoff")

        page = await mcp("tools/call", {"name": "list_senators", "arguments": {"state": "GA"}})
        assert [e["id"] for e in page["structuredContent"]["entries"]] == ["jon-ossoff"]


async def test_errors_come_back_as_readable_tool_results(db_session):
    async with mcp_client(db_session) as mcp:
        missing = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "nobody"}})
        assert missing["isError"] and "404" in missing["content"][0]["text"]
        invalid = await mcp("tools/call", {"name": "list_senators", "arguments": {"party": "X"}})
        assert invalid["isError"] and "422" in invalid["content"][0]["text"]
        unknown = await mcp("tools/call", {"name": "drop_tables", "arguments": {}})
        assert unknown["isError"]
