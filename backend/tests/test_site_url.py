"""The site's domain is typed in two places only: backend/app/site_url.py and
frontend/src/lib/site.ts. Everything else reads SITE_URL (.env), so a
self-hosted copy under another domain needs no edit to any file
(docs/self-hosting.md). Tests, docs and generated data's provenance may name
it; code, config and scripts may not.
"""

import re
from pathlib import Path

import pytest

from app.site_url import DEFAULT_SITE_URL, domain_of

REPO = Path(__file__).resolve().parents[2]
DOMAIN = domain_of(DEFAULT_SITE_URL)
ALLOWED = {
    REPO / "backend/app/site_url.py",
    REPO / "frontend/src/lib/site.ts",
}
SCANNED = [
    (REPO / "backend/app", ("*.py",)),
    (REPO / "backend/scripts", ("*.py", "*.sh")),
    (REPO / "frontend/src", ("*.ts", "*.tsx", "*.js", "*.mjs")),
    (REPO / "frontend/public", ("*",)),
    (REPO / "nginx", ("*",)),
]
TOP_LEVEL = ["docker-compose.yml", "docker-compose.swarm.yml", "docker-compose.dev.yml",
             "check-and-deploy.sh", "frontend/Dockerfile", "frontend/next.config.mjs",
             "frontend/next.config.js", "frontend/next.config.ts"]


def _is_test(path: Path) -> bool:
    return ".test." in path.name or "/tests/" in path.as_posix() or path.name.startswith("test_")


def _files():
    for root, patterns in SCANNED:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                if path.is_file() and "node_modules" not in path.parts and not _is_test(path):
                    yield path
    for rel in TOP_LEVEL:
        path = REPO / rel
        if path.exists():
            yield path


# The backend image holds backend/ only; the frontend half runs in a checkout.
needs_checkout = pytest.mark.skipif(not (REPO / "frontend/src").exists(), reason="frontend/ not in this checkout")


@needs_checkout
def test_nothing_else_types_the_domain():
    pattern = re.compile(re.escape(DOMAIN))
    offenders = sorted(
        str(path.relative_to(REPO))
        for path in set(_files())
        if path not in ALLOWED and pattern.search(path.read_text(errors="ignore"))
    )
    assert not offenders, f"{DOMAIN} typed outside site_url.py / site.ts: {offenders}"


@needs_checkout
def test_both_defaults_name_the_same_site():
    site_ts = (REPO / "frontend/src/lib/site.ts").read_text()
    assert f'process.env.NEXT_PUBLIC_SITE_URL || "{DEFAULT_SITE_URL}"' in site_ts


@needs_checkout
def test_the_frontend_build_gets_the_same_env_value():
    compose = (REPO / "docker-compose.yml").read_text()
    assert "NEXT_PUBLIC_SITE_URL: ${SITE_URL:-}" in compose
    assert 'ARG NEXT_PUBLIC_SITE_URL=""' in (REPO / "frontend/Dockerfile").read_text()


def test_settings_follow_the_env(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SITE_URL", "https://civic.example.net")
    s = Settings(_env_file=None)
    assert s.SITE_URL == "https://civic.example.net"
    assert s.SITE_DOMAIN == "civic.example.net"
    monkeypatch.delenv("SITE_URL")
    assert Settings(_env_file=None).SITE_DOMAIN == DOMAIN
