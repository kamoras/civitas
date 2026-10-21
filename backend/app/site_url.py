"""The site's public address: the one place the backend types its domain.

Each instance names its own: SITE_URL comes from the environment variable of
that name (.env.example, docs/self-hosting.md), defaulting to the production
site. app.config's SITE_URL setting defaults to this, and the frontend's
lib/site.ts is built from the same .env value (docker-compose.yml passes it
as NEXT_PUBLIC_SITE_URL), so links, feeds and cards agree across the stack.
tests/test_site_url.py fails on the domain typed anywhere else.

Standard library only, like app.contact: maintenance scripts that run on a
bare `python3` import it.
"""

import os
from urllib.parse import urlsplit

DEFAULT_SITE_URL = "https://civitas-research.org"

SITE_URL = (os.environ.get("SITE_URL") or DEFAULT_SITE_URL).rstrip("/")


def domain_of(url: str) -> str:
    """The host of a site URL: "example.org" for "https://example.org"."""
    return urlsplit(url).netloc


SITE_DOMAIN = domain_of(SITE_URL)
