"""The public API's MCP server (api/public_mcp.py), spoken to over its own
transport — JSON-RPC over streamable HTTP, as an MCP client would."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api import public
from app.api.public_mcp import PATH, McpEndpoint
from app.api.router import api_router
from app.database import get_db
from app.models import ApiRequestCount, MemberIdAlias, Senator
from tests.visits_helpers import _drain_queue_and_write

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@asynccontextmanager
async def mcp_client(db_session):
    """An initialized MCP connection. Entered inside each test rather than as
    a fixture: the session manager's task group must be exited by the task
    that entered it, and pytest-asyncio tears fixtures down in another."""
    db_session.add(Senator(
        id="jon-brennan", name="Jon Brennan", state="GA", party="D",
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
        result = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "jon-brennan"}})
        assert not result.get("isError")
        assert result["structuredContent"]["name"] == "Jon Brennan"
        assert result["structuredContent"]["siteUrl"].endswith("/politicians/jon-brennan")

        page = await mcp("tools/call", {"name": "list_senators", "arguments": {"state": "GA"}})
        assert [e["id"] for e in page["structuredContent"]["entries"]] == ["jon-brennan"]


async def test_a_null_optional_argument_is_not_given(db_session):
    """Each optional parameter's input schema allows null; sent on, it went
    out as an empty query value and was refused with a 422."""
    async with mcp_client(db_session) as mcp:
        page = await mcp("tools/call", {"name": "list_senators",
                                        "arguments": {"party": None, "state": None, "page": None}})
        assert not page.get("isError"), page
        assert [e["id"] for e in page["structuredContent"]["entries"]] == ["jon-brennan"]
        missing = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": None}})
        assert missing["isError"] and "senator_id" in missing["content"][0]["text"]


async def test_a_renamed_id_answers_under_the_current_one(db_session):
    db_session.add(MemberIdAlias(old_id="brennan-old", new_id="jon-brennan"))
    db_session.commit()
    async with mcp_client(db_session) as mcp:
        result = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "brennan-old"}})
        assert not result.get("isError")
        assert result["structuredContent"]["id"] == "jon-brennan"
        history = await mcp("tools/call", {"name": "get_senator_history", "arguments": {"senator_id": "brennan-old"}})
        assert history["structuredContent"]["id"] == "jon-brennan"


async def test_an_id_with_a_slash_is_no_such_member(db_session):
    """Was 405 Method Not Allowed: a catch-all OPTIONS route matched the path."""
    async with mcp_client(db_session) as mcp:
        result = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "../x"}})
        assert result["isError"] and "404" in result["content"][0]["text"]


async def test_errors_come_back_as_readable_tool_results(db_session):
    async with mcp_client(db_session) as mcp:
        missing = await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "nobody"}})
        assert missing["isError"] and "404" in missing["content"][0]["text"]
        invalid = await mcp("tools/call", {"name": "list_senators", "arguments": {"party": "X"}})
        assert invalid["isError"] and "422" in invalid["content"][0]["text"]
        unknown = await mcp("tools/call", {"name": "drop_tables", "arguments": {}})
        assert unknown["isError"]


async def test_tool_calls_and_connections_are_counted_on_the_mcp_channel(db_session):
    async with mcp_client(db_session) as mcp:
        await mcp("tools/list")
        await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "jon-brennan"}})
        await mcp("tools/call", {"name": "get_senator", "arguments": {"senator_id": "nobody"}})
    _drain_queue_and_write(db_session)
    counts = {(r.endpoint, r.channel, r.status): r.count for r in db_session.query(ApiRequestCount).all()}
    assert counts == {
        ("tools/list", "mcp", 200): 1,
        ("get_senator", "mcp", 200): 1,
        ("get_senator", "mcp", 404): 1,
    }


async def test_a_get_is_refused_at_once_rather_than_holding_a_stream_open(db_session):
    """The SDK answers a GET with the server-to-client SSE stream even in
    stateless mode, and this server never writes to it, so each GET held a
    connection open until nginx's read timeout. Refused with 405 instead."""
    import asyncio

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    endpoint = McpEndpoint()
    app.add_route(PATH, endpoint, methods=["GET", "POST", "DELETE"])
    async with endpoint.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://civitas",
    ) as client:
        for accept in ("text/event-stream", "*/*"):
            resp = await asyncio.wait_for(client.get(PATH, headers={"Accept": accept}), timeout=5)
            assert resp.status_code == 405
            assert resp.headers["allow"] == "POST"
        # POST still answers.
        resp = await client.post(PATH, headers=HEADERS, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        })
        assert resp.status_code == 200
