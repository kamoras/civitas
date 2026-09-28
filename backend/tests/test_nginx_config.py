"""nginx/civitas.conf keeps the site's security headers on every response.

nginx inherits `add_header` from the enclosing level only when a location sets
none of its own, so one `add_header X-Cache-Status ...` in a location silently
dropped nosniff, X-Frame-Options, Referrer-Policy and Permissions-Policy on its
responses (found 2026-09 on about 18 locations, /api/og's images included).
The headers live in nginx/security-headers.conf, and any location that adds a
header must include it. These tests read the config as text: nginx itself is
not installed where the fast suite runs.
"""

import re
from pathlib import Path

import pytest

NGINX_DIR = Path(__file__).resolve().parents[2] / "nginx"
CONF = NGINX_DIR / "civitas.conf"
SNIPPET = NGINX_DIR / "security-headers.conf"
INCLUDE = "include /etc/nginx/snippets/security-headers.conf;"

# The suite also runs inside the backend image, which has no nginx/ directory.
pytestmark = pytest.mark.skipif(not CONF.exists(), reason="nginx/ not in this checkout")


def _statements(text: str) -> list[str]:
    """The config's lines, comments and blank lines removed."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _location_blocks(text: str) -> dict[str, list[str]]:
    """Each `location` block's own statements (nested blocks included),
    keyed by its header line."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    depth = 0
    start_depth = 0
    for line in _statements(text):
        if current is None and re.match(r"location\b", line) and line.endswith("{"):
            current, start_depth = line[:-1].strip(), depth
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
        depth += line.count("{") - line.count("}")
        if current is not None and depth <= start_depth:
            current = None
    return blocks


def test_every_location_that_adds_a_header_keeps_the_security_headers():
    blocks = _location_blocks(CONF.read_text())
    assert blocks, "no location blocks parsed"
    missing = [
        name
        for name, body in blocks.items()
        if any(s.startswith("add_header") for s in body) and INCLUDE not in body
    ]
    assert missing == [], f"these locations drop the security headers: {missing}"


def test_server_level_includes_the_security_headers():
    statements = _statements(CONF.read_text())
    first_location = next(i for i, s in enumerate(statements) if s.startswith("location"))
    assert INCLUDE in statements[:first_location]


def test_security_headers_live_only_in_the_snippet():
    """One copy: a header set in civitas.conf directly would be the start of
    a second list that drifts from the first."""
    names = ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy")
    conf = [s for s in _statements(CONF.read_text()) if s.startswith("add_header")]
    assert not [s for s in conf if any(n in s for n in names)]
    snippet = _statements(SNIPPET.read_text())
    for name in names:
        assert any(s.startswith(f"add_header {name} ") and s.endswith("always;") for s in snippet), name


def test_the_image_copies_the_snippet_where_the_config_includes_it():
    dockerfile = (NGINX_DIR / "Dockerfile").read_text()
    assert "COPY security-headers.conf /etc/nginx/snippets/security-headers.conf" in dockerfile


def test_the_parser_sees_a_location_that_drops_the_headers():
    """The check above is only as good as the parser; a known-bad config
    must fail it."""
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
    blocks = _location_blocks(bad)
    assert set(blocks) == {"location = /api/x", "location /"}
    assert INCLUDE not in blocks["location = /api/x"]
