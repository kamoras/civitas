"""Shared utilities for Bluesky posting modules."""

import logging
import re

import httpx

logger = logging.getLogger(__name__)


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
        return m.group(1) if m else ""

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
