"""Versioned URLs for the site's icon and its cards (frontend /api/og).

Feed readers, chat bots and their image proxies (MonitoRSS, Discord) cache a
picture by its URL for good: after the new brand (#1147) Discord kept showing
the old icon for /apple-icon.png and the old dark card for
/api/og?state=MT. So every such URL Civitas emits carries `v=`, a short hash
of the files that draw it, and a new design is a new URL.

The hashes are computed by frontend/scripts/assetVersions.mjs: the frontend
uses them live at build time; the backend reads the copy that script writes
to app/data/asset_versions.json (`npm run asset-versions`), held equal to
the frontend's files by src/lib/assetVersions.test.ts.
"""
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.broadcast import SITE_URL

_PATH = Path(__file__).parent / "data" / "asset_versions.json"
OG_PATH = "/api/og"
# Every path that draws a card: the record and page cards, and the site card
# (frontend app/opengraph-image.tsx), which some pages share.
_CARD_PATHS = frozenset({OG_PATH, "/opengraph-image"})


@lru_cache(maxsize=1)
def _versions() -> dict:
    return json.loads(_PATH.read_text())


def icon_url() -> str:
    """The PNG icon a feed names (Discord and most readers don't draw SVG)."""
    return f"{SITE_URL}/apple-icon.png?v={_versions()['icon']}"


def og_card_url(query: str) -> str:
    """The site's card for `query` ("state=MT"), at the current design."""
    return versioned_card(f"{SITE_URL}{OG_PATH}?{query}")


def versioned_card(url: str | None) -> str | None:
    """`url` with the current card version when it is one of the site's
    cards (any stored one, captured before versioning or under an older
    design); any other URL unchanged."""
    if not url:
        return url
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" != SITE_URL or parts.path not in _CARD_PATHS:
        return url
    # Kept as written (not re-encoded), so it is the URL the page emits.
    params = [p for p in parts.query.split("&") if p and not p.startswith("v=")]
    params.append(f"v={_versions()['ogCard']}")
    return urlunsplit(parts._replace(query="&".join(params)))
