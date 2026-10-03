"""The public API as an MCP server: /api/public/v1/mcp (streamable HTTP).

Every documented public endpoint is a tool, generated from the same OpenAPI
spec /developers renders (public.openapi_spec_dict) — its name the route's
operation id, its description the route's summary and docstring, its input
schema the route's parameters. A route added to the public API is a tool
with no change here.

A tool call runs the real route in-process, as a GET from the caller's own
IP, so it answers exactly what the HTTP API would — same validation, same
404s, same per-IP rate limit (a tool call spends one request of it, like
the HTTP call it stands for). Listing tools reads the cached spec and
spends nothing.

Stateless and JSON-only: nothing is kept between requests, so any API
worker can answer any request and there is no session to remember (§8 in
AGENTS.md — nothing about the caller is retained).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Receive, Scope, Send

from app.api.public import CHANNEL_HEADER, PREFIX, openapi_spec_dict
from app.api.visits import record_api_request
from app.api.rate_limit import client_ip
from app.broadcast import SITE_URL
from app.http_client import make_async_client

PATH = f"{PREFIX}/mcp"

_INSTRUCTIONS = (
    "Civitas scores U.S. senators and representatives on how well they represent their "
    "constituents (0-100, higher is better) from official voting, funding and legislative "
    "records, and searches floor speeches, presidential actions, Supreme Court opinions and "
    "federal rules. Members are identified by the id in their Civitas URL (e.g. jon-ossoff); "
    "find one with list_senators or list_representatives, filtered by state. Cite a record's "
    f"siteUrl when you use it. How scores are computed: {SITE_URL}/about/scores."
)


def _operations() -> dict[str, tuple[str, dict]]:
    """operationId -> (path, operation) for every documented GET."""
    return {
        op["operationId"]: (path, op)
        for path, ops in openapi_spec_dict()["paths"].items()
        for method, op in ops.items()
        if method == "get"
    }


def _tool(name: str, path: str, op: dict) -> mcp_types.Tool:
    params = op.get("parameters", [])
    properties = {
        p["name"]: {**p["schema"], **({"description": p["description"]} if "description" in p else {})}
        for p in params
    }
    return mcp_types.Tool(
        name=name,
        title=op.get("summary"),
        description="\n\n".join(filter(None, [op.get("summary"), op.get("description")])),
        input_schema={
            "type": "object",
            "properties": properties,
            "required": [p["name"] for p in params if p.get("required")],
            "additionalProperties": False,
        },
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )


async def _list_tools(ctx, params) -> mcp_types.ListToolsResult:
    # Counted like an endpoint: a client lists tools when it connects, so
    # this is how often assistants pick the server up.
    record_api_request("tools/list", "mcp", 200)
    return mcp_types.ListToolsResult(tools=[_tool(n, p, op) for n, (p, op) in _operations().items()])


async def _call_tool(ctx, params: mcp_types.CallToolRequestParams) -> mcp_types.CallToolResult:
    found = _operations().get(params.name)
    if found is None:
        return _error(f"No tool named {params.name!r}.")
    path, op = found
    args = dict(params.arguments or {})
    for p in op.get("parameters", []):
        if p["in"] == "path":
            if p["name"] not in args:
                return _error(f"Missing required argument {p['name']!r}.")
            path = path.replace("{" + p["name"] + "}", quote(str(args.pop(p["name"])), safe=""))
    request = ctx.request
    transport = httpx.ASGITransport(app=request.app, client=(client_ip(request), 0))
    async with make_async_client(
        transport=transport, base_url="http://civitas", headers={CHANNEL_HEADER: "mcp"},
    ) as client:
        resp = await client.get(path, params=args)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    if resp.status_code != 200:
        return _error(f"HTTP {resp.status_code}: {text}")
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structured_content=body if isinstance(body, dict) else {"items": body},
    )


def _error(message: str) -> mcp_types.CallToolResult:
    # Inside the result, not a protocol error, so the model can read it and
    # correct its call (MCP's own guidance for tool errors).
    return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=message)], is_error=True)


class McpEndpoint:
    """The ASGI app main.py mounts at PATH; `run()` belongs in its lifespan.

    A session manager can run only once, so each `run()` makes a new one: a
    lifespan that starts again in the same process (tests do; so would an
    embedding server) gets a working endpoint rather than a RuntimeError."""

    def __init__(self) -> None:
        self._server = Server(
            "civitas",
            version="v1",
            title="Civitas",
            instructions=_INSTRUCTIONS,
            website_url=f"{SITE_URL}/developers",
            on_list_tools=_list_tools,
            on_call_tool=_call_tool,
        )
        self._manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        manager = StreamableHTTPSessionManager(
            app=self._server,
            stateless=True,
            json_response=True,
            # DNS-rebinding protection guards a server on the user's own
            # machine from pages in their browser. This one is public,
            # unauthenticated and read-only: there is nothing to rebind to.
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with manager.run():
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("method") == "GET":
            # A GET opens the server-to-client SSE stream, which the SDK
            # serves even in stateless mode and which this server never
            # writes to: nothing is pushed to a client between its own
            # requests. Left to the SDK, every GET (curl's default Accept
            # */* counts as accepting event-stream) held a connection open
            # until nginx's 120 s read timeout, on a public, unauthenticated
            # path. The streamable HTTP spec lets a server answer 405.
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"content-type", b"text/plain"), (b"allow", b"POST")]})
            await send({"type": "http.response.body", "body": b"Method Not Allowed: POST JSON-RPC here"})
            return
        if self._manager is None:
            # Outside the lifespan (or between runs): unavailable, not a crash.
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"text/plain"), (b"retry-after", b"5")]})
            await send({"type": "http.response.body", "body": b"MCP server starting"})
            return

        async def send_with_cors(message) -> None:
            # Browser-based MCP clients read the answer cross-origin.
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []),
                                                  (b"access-control-allow-origin", b"*")]}
            await send(message)

        await self._manager.handle_request(scope, receive, send_with_cors)
