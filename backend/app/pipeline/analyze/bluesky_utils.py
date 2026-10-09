"""Shared utilities for Bluesky posting modules."""

import logging
import re
from html import unescape

import httpx

from app.contact import BOT_USER_AGENT, SELF_FETCH_USER_AGENT
from app.config import settings

logger = logging.getLogger(__name__)

# Bluesky's per-post character limit.
BSKY_MAX_CHARS = 300


def truncate_on_boundary(text: str, budget: int) -> str:
    """Trim `text` to at most `budget` chars, preferring a sentence boundary
    past the midpoint, else a word boundary, else a hard cut.

    Used to fit a generated body under Bluesky's 300-char limit while keeping
    the trailing URL intact.
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


_HASHTAG_RE = re.compile(r"#(\w+)")


def strip_hashtags(text: str) -> str:
    """Convert #word to word. Its own function (not inlined) so the final
    guard in bluesky_poster._publish — a defense-in-depth check at the
    actual public-posting boundary, independent of whatever already ran
    upstream — shares the same pattern instead of a 5th independent copy
    that could drift from this one (2026-08 cleanup)."""
    return _HASHTAG_RE.sub(r"\1", text).strip()


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

    # A single space, not a blank line — confirmed live against NPR's own
    # Bluesky posts (public.api.bsky.app getAuthorFeed), the account this
    # was compared against: "...at age 53. n.pr/4h4XVR4", one space, no
    # newline. Saves a character of the 300-char budget for content over
    # the previous "\n\n" separator, though the real gap against an
    # account like NPR is their own short-domain link shortener (n.pr,
    # ~13 chars) vs. this platform's full civitas-research.org URL
    # (~45 chars) — a separate, much bigger lever this doesn't address.
    full_text = f"{text} {url}"
    if len(full_text) > BSKY_MAX_CHARS:
        budget = BSKY_MAX_CHARS - len(url) - 1  # 1 for the space separator
        full_text = f"{truncate_on_boundary(text, budget)} {url}"

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


def og_card(html: str) -> dict[str, str]:
    """A page's link card from its Open Graph tags: {title, description,
    image, image_alt}, each "" when the page doesn't set it. The one reading
    of a card: the Bluesky embed and the feed entry (app/broadcast.py) both
    come from it, so they can't disagree."""
    def _og(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        ) or re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
            html, re.IGNORECASE,
        )
        return unescape(m.group(1)) if m else ""

    return {
        "title": _og("title"),
        "description": _og("description"),
        "image": _og("image"),
        "image_alt": _og("image:alt"),
    }


def fetch_og_card(url: str) -> dict[str, str] | None:
    """`url`'s link card (og_card), or None when the page can't be read.
    Sent as SELF_FETCH_USER_AGENT: most of these are Civitas's own pages,
    and the logs should say it was the app reading itself."""
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": SELF_FETCH_USER_AGENT})
        resp.raise_for_status()
    except Exception:
        logger.debug("Link card fetch failed for %s", url)
        return None
    return og_card(resp.text)


def build_link_card(client, url: str):
    """Fetch OG metadata from url and build a Bluesky external embed (link card).

    Returns None on any failure so callers can post without an embed.
    """
    from atproto import models as bsky_models

    card = fetch_og_card(url)
    if card is None:
        return None

    thumb = None
    if card["image"]:
        try:
            img_resp = httpx.get(
                card["image"], timeout=10, follow_redirects=True, headers={"User-Agent": BOT_USER_AGENT},
            )
            img_resp.raise_for_status()
            blob = client.upload_blob(img_resp.content)
            thumb = blob.blob
        except Exception:
            logger.debug("Thumbnail upload failed for %s", card["image"])

    return bsky_models.AppBskyEmbedExternal.Main(
        external=bsky_models.AppBskyEmbedExternal.External(
            uri=url,
            title=card["title"] or "Civitas // Public Record",
            description=card["description"],
            thumb=thumb,
        )
    )
