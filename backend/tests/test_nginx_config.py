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
    servers = [b for b in _walk(_parse(CONF.read_text())) if b.head == "server"]
    assert servers
    for server in servers:
        assert INCLUDE in server.directives


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
