"""Keeps app/data/host_terms.json true: each listed host's terms page is
re-read every week, and a change raises an ops alert.

The page is read from the Internet Archive's newest capture of it, never
from the host: a site whose terms forbid automated access forbids an
automated read of its terms page too, and reading the Archive's copy sends
the host nothing (the same reasoning as web_archive.py's readers). The
capture's text (its <main>, else its body, whitespace folded) is compared
with what the entry was reviewed against:

- the quoted sentence is gone: the host may now allow automated access, or
  word the ban another way. It stays unasked (the safe side) and the alert
  quotes the change, since whether new legal wording allows Civitas in is
  for a person to read.
- the sentence is there but the terms around it (`excerpt`) hash other
  than `text_sha256`: a clause beside it changed (the copying clause, say);
  same alert.
- no capture newer than STALE_DAYS: the alert says the terms could not be
  re-read, never that they changed.

Each check is recorded (api_cache, CHECK_TIER) and shown on /about/crawler
as the terms' last-confirmed date. Outside app/pipeline/: it moves no
analysis fingerprint.
"""

import asyncio
import hashlib
import html
import logging
import re
from datetime import date, datetime, timedelta

from lxml import html as lxml_html

from app import host_terms
from app.pipeline.cache import api_cache_get, api_cache_set
from app.database import SessionLocal
from app.http_client import make_async_client
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.pipeline.fetch import web_archive
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

CHECK_TIER = "host-terms-check"
CHECK_EVERY = timedelta(days=7)
# The Archive captures a busy state page every few weeks (Michigan's terms:
# 2026-08-21, 2026-10-06, 2026-10-21).
STALE_DAYS = 60
_TTL_HOURS = 24 * 400


def page_text(body: bytes) -> str:
    """The page's reading text: its <main> (else body), entities decoded,
    whitespace folded."""
    tree = lxml_html.fromstring(body)
    for junk in tree.xpath("//script|//style|//noscript"):
        junk.drop_tree()
    main = tree.xpath("//main") or tree.xpath("//body") or [tree]
    return " ".join(html.unescape(main[0].text_content()).split())


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("“", '"').replace("”", '"')).strip()


# How much of the terms around the quoted sentence is hashed: the clauses
# beside it (Michigan's copying and commercial-use clauses sit within 1,500
# characters after it), not the page's banners and menus, which change for
# reasons that have nothing to do with the terms.
EXCERPT_BEFORE = 1500
EXCERPT_AFTER = 3000


def excerpt(entry: dict, text: str) -> str | None:
    """The terms around the entry's quoted sentence, or None when the
    sentence is not in `text`."""
    folded, quote = _fold(text), _fold(entry.get("quote") or "")
    at = folded.find(quote) if quote else -1
    if at < 0:
        return None
    return folded[max(0, at - EXCERPT_BEFORE): at + len(quote) + EXCERPT_AFTER]


def verdict(entry: dict, text: str) -> str:
    """"same", "changed" (the sentence is there, the terms around it aren't
    the reviewed ones) or "sentence gone"."""
    part = excerpt(entry, text)
    if part is None:
        return "sentence gone"
    reviewed = entry.get("text_sha256")
    return "same" if not reviewed or reviewed == text_hash(part) else "changed"


def last_check(db, host: str) -> dict | None:
    return api_cache_get(db, CHECK_TIER, host, max_age_hours=_TTL_HOURS)


def _due(record: dict | None) -> bool:
    if not record or not record.get("checkedAt"):
        return True
    return utcnow() - _parse(record["checkedAt"]) >= CHECK_EVERY


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.rstrip("Z"))


async def _check(client, db, host: str, entry: dict) -> dict:
    condition = f"host-terms-{host}"
    previous = last_check(db, host) or {}
    capture = await web_archive.newest_capture(client, entry["terms_url"])
    body = await web_archive.read_page(client, capture) if capture else None
    now = utcnow().isoformat() + "Z"
    if body is None:
        record = {**previous, "checkedAt": now, "outcome": "unread"}
        confirmed = previous.get("confirmedOn")
        if not confirmed or date.fromisoformat(confirmed) < utcnow().date() - timedelta(days=STALE_DAYS):
            send_ops_alert(
                f"Terms of use for {host} not re-read",
                f"No readable Internet Archive capture of {entry['terms_url']} since "
                f"{confirmed or 'the entry was written'}. {host} stays unasked "
                "(app/data/host_terms.json); the check retries next week.",
                dedupe_key=f"{condition}-unread", condition=condition,
            )
        api_cache_set(db, CHECK_TIER, host, record, normal_ttl_hours=_TTL_HOURS)
        return record
    text = page_text(body)
    result = verdict(entry, text)
    part = excerpt(entry, text)
    seen = text_hash(part if part is not None else text)
    record = {"checkedAt": now, "outcome": result, "capture": capture.page_url,
              "capturedOn": capture.captured_on.isoformat(), "textSha256": seen,
              "confirmedOn": capture.captured_on.isoformat() if result == "same" else previous.get("confirmedOn")}
    if result == "same":
        resolve_ops_alert(condition)
    else:
        what = ("no longer contains the sentence quoted in app/data/host_terms.json"
                if result == "sentence gone" else "has changed since it was reviewed (text_sha256)")
        send_ops_alert(
            f"Terms of use for {host} changed",
            f"The Internet Archive's capture of {entry['terms_url']} on {capture.captured_on} {what}. "
            f"{host} stays unasked until the entry is reviewed: read the terms at {capture.page_url} and "
            f"update the entry (its quote, and text_sha256 {seen} if the excerpt is reviewed as is), or remove "
            "it if they now allow automated access.",
            dedupe_key=f"{condition}-{seen[:16]}", condition=condition,
        )
    api_cache_set(db, CHECK_TIER, host, record, normal_ttl_hours=_TTL_HOURS)
    return record


async def _run(db) -> dict:
    out = {}
    async with make_async_client(timeout=60.0) as client:
        for host, entry in sorted(host_terms.all_hosts().items()):
            if _due(last_check(db, host)):
                out[host] = (await _check(client, db, host, entry))["outcome"]
    return out


def run_host_terms_check() -> dict:
    """The scheduled job: each listed host whose last check is a week old."""
    db = SessionLocal()
    try:
        outcomes = asyncio.run(_run(db))
        if outcomes:
            logger.info("Host terms check: %s", outcomes)
        return outcomes
    finally:
        db.close()
