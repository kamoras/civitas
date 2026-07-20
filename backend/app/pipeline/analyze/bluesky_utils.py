"""Shared utilities for Bluesky posting modules."""

import logging
import re
from html import unescape

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Bluesky's per-post character limit.
BSKY_MAX_CHARS = 300


def truncate_on_boundary(text: str, budget: int) -> str:
    """Trim `text` to at most `budget` chars, preferring a sentence boundary
    past the midpoint, else a word boundary, else a hard cut.

    Used to fit a generated body under Bluesky's 300-char limit while keeping
    the trailing URL intact. (Distinct from bluesky_poster._sanitize, which
    also strips hashtags and uses an any-position sentence cut for a different
    budget.)
    """
    trimmed = text[:budget]
    cut = -1
    for punct in (".", "!", "?"):
        idx = trimmed.rfind(punct)
        if idx > len(trimmed) // 2:
            cut = max(cut, idx + 1)  # include the punctuation char
    if cut > 0:
        return trimmed[:cut]
    last_space = trimmed.rfind(" ")
    if last_space > 0:
        return trimmed[:last_space]
    return trimmed


def publish_post(text: str, url: str, *, success_msg: str, error_context: str) -> bool:
    """Post `text` followed by `url` to Bluesky, rendering the URL as a
    clickable link (rich-text facet) with an OG link card. Returns True on
    success, False on missing credentials or any failure.

    Consolidates the near-identical posting bodies of bluesky_poster._publish,
    bluesky_spotlight._publish_spotlight and _publish_weekly, including the
    subtle UTF-8 byte-offset facet math. Callers supply the URL, the success
    log line, and a short error context.
    """
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        logger.debug("Bluesky credentials not set — skipping publish")
        return False

    full_text = f"{text}\n\n{url}"
    if len(full_text) > BSKY_MAX_CHARS:
        budget = BSKY_MAX_CHARS - len(url) - 2  # 2 for the \n\n separator
        full_text = f"{truncate_on_boundary(text, budget)}\n\n{url}"

    try:
        from atproto import Client, models  # imported here so a missing package only fails at post time
        client = Client()
        client.login(handle, app_password)

        # Bluesky facet byte offsets are UTF-8 encoded positions.
        encoded = full_text.encode("utf-8")
        url_bytes = url.encode("utf-8")
        url_start = encoded.find(url_bytes)
        facets = [
            models.AppBskyRichtextFacet.Main(
                features=[models.AppBskyRichtextFacet.Link(uri=url)],
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=url_start,
                    byte_end=url_start + len(url_bytes),
                ),
            )
        ]

        embed = build_link_card(client, url)
        client.send_post(full_text, facets=facets, embed=embed)
        logger.info("%s", success_msg)
        return True
    except ImportError:
        logger.error("atproto package not installed — cannot post to Bluesky")
        return False
    except Exception:
        logger.exception("Bluesky post failed (%s)", error_context)
        return False


def build_link_card(client, url: str):
    """Fetch OG metadata from url and build a Bluesky external embed (link card).

    Returns None on any failure so callers can post without an embed.
    """
    from atproto import models as bsky_models

    try:
        resp = httpx.get(
            url,
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Civitas-Bot/1.0"},
        )
        resp.raise_for_status()
        html = resp.text
    except Exception:
        logger.debug("Link card fetch failed for %s", url)
        return None

    def _og(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        ) or re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
            html, re.IGNORECASE,
        )
        return unescape(m.group(1)) if m else ""

    title = _og("title") or "Civitas // Public Record"
    description = _og("description") or ""
    image_url = _og("image")

    thumb = None
    if image_url:
        try:
            img_resp = httpx.get(image_url, timeout=10, follow_redirects=True)
            img_resp.raise_for_status()
            blob = client.upload_blob(img_resp.content)
            thumb = blob.blob
        except Exception:
            logger.debug("Thumbnail upload failed for %s", image_url)

    return bsky_models.AppBskyEmbedExternal.Main(
        external=bsky_models.AppBskyEmbedExternal.External(
            uri=url,
            title=title,
            description=description,
            thumb=thumb,
        )
    )
