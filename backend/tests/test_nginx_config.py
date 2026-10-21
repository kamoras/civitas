"""nginx/civitas.conf keeps the site's security headers on every response.

nginx inherits `add_header` from the enclosing level only into a block that
sets none of its own — a `location`, and an `if` inside one, alike. So one
`add_header X-Cache-Status ...` in a location silently dropped nosniff,
X-Frame-Options, Referrer-Policy and Permissions-Policy on its responses
(found 2026-09 on about 18 locations, /api/og's images included). The
headers live in nginx/security-headers.conf, and any block that adds a
header must include it itself. These tests read the config as text: nginx is
not installed where the fast suite runs.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

NGINX_DIR = Path(__file__).resolve().parents[2] / "nginx"
CONF = NGINX_DIR / "civitas.conf"
SNIPPET = NGINX_DIR / "security-headers.conf"
INCLUDE = "include /etc/nginx/snippets/security-headers.conf"
SECURITY_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
)

# The suite also runs inside the backend image, which has no nginx/ directory.
pytestmark = pytest.mark.skipif(not CONF.exists(), reason="nginx/ not in this checkout")


@dataclass
class Block:
    head: str
    directives: list[str] = field(default_factory=list)
    children: list["Block"] = field(default_factory=list)


def _parse(text: str) -> Block:
    """The config as a tree of blocks, each holding its own directives.

    Tokenised on `;`, `{` and `}` rather than on lines, so a block written
    on one line is seen the same as one spread over several. Comments are
    dropped and quoted strings kept whole; like nginx, a `#` or a quote
    counts only at the start of a token.
    """
    root = Block("<root>")
    stack = [root]
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        c = text[i]
        at_token_start = not buf or buf[-1].isspace()
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 1
            elif c == quote:
                quote = None
        # As in nginx, a quote or a comment only starts a token: the `#` in
        # `return 301 https://x/a#frag;` is part of the value.
        elif c in "\"'" and at_token_start:
            quote = c
            buf.append(c)
        elif c == "#" and at_token_start:
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif c in ";{}":
            token = " ".join("".join(buf).split())
            buf = []
            if c == ";":
                if token:
                    stack[-1].directives.append(token)
            elif c == "{":
                block = Block(token)
                stack[-1].children.append(block)
                stack.append(block)
            else:
                assert len(stack) > 1, "unbalanced '}' in nginx config"
                stack.pop()
        else:
            buf.append(c)
        i += 1
    assert len(stack) == 1, "unclosed block in nginx config"
    return root


def _walk(block: Block):
    yield block
    for child in block.children:
        yield from _walk(child)


def _dropping_blocks(root: Block) -> list[str]:
    """Blocks that set a header of their own without re-including the
    security headers — each of these drops them."""
    return [
        b.head
        for b in _walk(root)
        if any(d.startswith("add_header ") for d in b.directives)
        and INCLUDE not in b.directives
    ]


def test_every_block_that_adds_a_header_keeps_the_security_headers():
    root = _parse(CONF.read_text())
    locations = [b for b in _walk(root) if b.head.startswith("location")]
    assert len(locations) > 10, "the parser found too few locations"
    assert _dropping_blocks(root) == []


def test_server_level_includes_the_security_headers():
    """Every server a client can reach. The loopback-only "api-misses"
    server is not one: its responses reach clients only through the public
    server's /api/ locations, which add the headers — added there too,
    each would go out twice."""
    servers = [b for b in _walk(_parse(CONF.read_text())) if b.head == "server"]
    public = [s for s in servers if not any(d.startswith("listen 127.0.0.1:") for d in s.directives)]
    assert public and len(public) < len(servers)
    for server in public:
        assert INCLUDE in server.directives
    for server in servers:
        if server not in public:
            assert INCLUDE not in server.directives


def test_redirects_nginx_makes_are_relative():
    # This server sees itself as http on :8081, behind the host nginx and
    # Cloudflare. An absolute redirect (the automatic /feed -> /feed/ one,
    # or any `return`) sent readers to http://civitas-research.org:8081/.
    servers = [b for b in _walk(_parse(CONF.read_text())) if b.head == "server"]
    assert servers
    for server in servers:
        assert "absolute_redirect off" in server.directives


def test_www_redirects_to_the_one_canonical_host():
    # www answered 200 with the whole site: a duplicate of every page for
    # search engines. It must 301 to the apex, path and query kept.
    public = [
        s
        for s in _walk(_parse(CONF.read_text()))
        if s.head == "server" and not any(d.startswith("listen 127.0.0.1:") for d in s.directives)
    ]
    (server,) = public
    (block,) = [b for b in server.children if b.head == "if ($host = www.civitas-research.org)"]
    assert block.directives == ["return 301 https://civitas-research.org$request_uri"]


def test_security_headers_live_only_in_the_snippet():
    """One copy: a header set in civitas.conf directly would be the start of
    a second list that drifts from the first."""
    conf = [d for b in _walk(_parse(CONF.read_text())) for d in b.directives]
    assert not [
        d
        for d in conf
        if d.startswith("add_header") and any(n in d for n in SECURITY_HEADERS)
    ]
    snippet = _parse(SNIPPET.read_text()).directives
    for name in SECURITY_HEADERS:
        assert any(
            d.startswith(f"add_header {name} ") and d.endswith(" always")
            for d in snippet
        ), name


def test_pages_leave_marked_no_transform():
    """Without it Cloudflare injects its analytics beacon and a script that
    sets a one-year cookie into every page (see the map's comment). Each
    location that proxies to the frontend and isn't a static-asset or image
    route must replace Next's Cache-Control with the no-transform one."""
    root = _parse(CONF.read_text())
    maps = [
        b for b in _walk(root) if b.head.startswith("map $upstream_http_cache_control")
    ]
    assert maps and any(
        '"$upstream_http_cache_control, no-transform"' in d for d in maps[0].directives
    )
    # The api-misses server answers only the public server, whose location
    # replaces the header on the way out (its /search, as for any page).
    public = [
        b for b in _walk(root)
        if b.head == "server" and "server_name api-misses" not in b.directives
    ]
    page_locations = [
        b
        for server in public
        for b in _walk(server)
        if b.head.startswith("location")
        and "proxy_pass http://$frontend_upstream" in b.directives
        and b.head
        not in ("location /_next/static/", "location /photo/", "location = /api/og")
    ]
    assert {b.head for b in page_locations} >= {"location /", "location ^~ /admin"}
    # A public location that hops to the miss server for a page.
    assert any(
        b.head == "location = /search" and "proxy_pass http://api_misses" in b.directives
        and "add_header Cache-Control $cache_control_no_transform always" in b.directives
        for server in public for b in _walk(server)
    )
    for b in page_locations:
        assert "proxy_hide_header Cache-Control" in b.directives, b.head
        assert (
            "add_header Cache-Control $cache_control_no_transform always"
            in b.directives
        ), b.head


def test_the_image_copies_the_snippet_where_the_config_includes_it():
    dockerfile = (NGINX_DIR / "Dockerfile").read_text()
    assert (
        "COPY security-headers.conf /etc/nginx/snippets/security-headers.conf"
        in dockerfile
    )


# The checks above are only as good as the parser: known-bad configs must fail.


def test_the_check_catches_a_location_that_drops_the_headers():
    bad = """
    server {
        include /etc/nginx/snippets/security-headers.conf;
        location = /api/x {
            proxy_pass http://b;
            add_header X-Cache-Status $upstream_cache_status;
        }
        location / {
            proxy_pass http://f;
        }
    }
    """
    assert _dropping_blocks(_parse(bad)) == ["location = /api/x"]


def test_the_check_catches_a_one_line_location():
    bad = "server { include /etc/nginx/snippets/security-headers.conf; location = /a { add_header X-A 1; } }"
    assert _dropping_blocks(_parse(bad)) == ["location = /a"]


def test_the_check_catches_an_if_block_inside_a_covered_location():
    """An `if` that adds a header resets inheritance too, even inside a
    location that includes the snippet."""
    bad = """
    server {
        include /etc/nginx/snippets/security-headers.conf;
        location /b {
            include /etc/nginx/snippets/security-headers.conf;
            add_header X-B 1;
            if ($arg_x) { add_header X-C 1; }
        }
    }
    """
    assert _dropping_blocks(_parse(bad)) == ["if ($arg_x)"]


def test_the_parser_keeps_quoted_values_and_drops_comments():
    root = _parse('server { add_header P "a#b; c={}" always; # x; {\n }')
    assert root.children[0].directives == ['add_header P "a#b; c={}" always']


def test_the_parser_treats_a_mid_token_hash_as_part_of_the_value():
    root = _parse("server { location /a { return 301 https://x/a#frag; } }")
    assert root.children[0].children[0].directives == ["return 301 https://x/a#frag"]


def test_access_log_keeps_no_query_strings_or_referrers():
    """What a reader searched for is in the query string (/search?q=,
    /api/search, Explore's ?q=) and in the referrer of the next page they
    open: neither is written to the container logs (AGENTS.md §8)."""
    text = CONF.read_text()
    fmt = text.split("log_format civitas_private", 1)[1].split(";", 1)[0]
    for leak in ("$request ", '$request"', "$request_uri", "$args", "$query_string", "$http_referer"):
        assert leak not in fmt, leak
    assert "$uri" in fmt
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("access_log") and line != "access_log off;":
            assert line.endswith("civitas_private;"), line


def test_permissions_policy_lets_the_site_itself_ask_for_location():
    # The header's "Use my location" and the district finder ask the browser
    # for a position on a click and place it on the device (AGENTS.md §8).
    # geolocation=() refused every such request on this site; =(self) allows
    # this origin only, still none for a frame from another host.
    text = SNIPPET.read_text()
    assert "geolocation=(self)" in text
    assert "geolocation=()" not in text
