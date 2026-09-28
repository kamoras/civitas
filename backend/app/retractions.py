"""Withdrawn content: the public retraction log (app/data/retractions.json).

The log is the record of what Civitas published and withdrew, and why.
An issue in it answers 410 with its reason, so a link someone saw in a
post explains itself rather than breaking; its rows are removed by the
data migration the entry names. Its Bluesky posts are deleted by
`delete_retracted_posts`, which runs on the scheduler until every listed
post is gone and records each one, so a failed deletion is retried and a
done one is never repeated.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)

_PATH = Path(__file__).parent / "data" / "retractions.json"
_CACHE_TIER = "bsky-retracted"
_KEEP_HOURS = 24 * 365 * 10


@lru_cache(maxsize=1)
def entries() -> list[dict]:
    return json.loads(_PATH.read_text())["retractions"]


def retraction_for_issue(issue_id: str) -> dict | None:
    """The entry withdrawing this issue (public id or legacy numeric id)."""
    for entry in entries():
        if issue_id in entry["publicIds"] or (issue_id.isdigit() and int(issue_id) in entry["issueIds"]):
            return {"date": entry["date"], "reason": entry["reason"]}
    return None


def _client():
    """A logged-in Bluesky client, or None when login fails (retried next run).
    One login per run: Bluesky allows about 30 sessions per five minutes, and
    an entry can list hundreds of posts."""
    from atproto import Client  # imported here like publish_post: a missing package only fails this job

    try:
        client = Client()
        client.login(settings.BSKY_HANDLE, settings.BSKY_APP_PASSWORD)
        return client
    except Exception as exc:
        logger.warning("Could not log in to delete retracted posts: %s", exc)
        return None


def _delete_post(client, uri: str) -> bool:
    """Delete one of the account's posts. True when it is gone, including
    already gone; False when the deletion failed and should be retried."""
    try:
        client.delete_post(uri)
        return True
    except Exception as exc:
        # A post that no longer exists answers "not found": the goal is met.
        if "not found" in str(exc).lower() or "could not locate" in str(exc).lower():
            return True
        logger.warning("Could not delete retracted post %s: %s", uri, exc)
        return False


def delete_retracted_posts(db: Session) -> int:
    """Delete every listed post not yet recorded as deleted. Returns how
    many were deleted this run. No credentials, nothing to do."""
    if not settings.BSKY_HANDLE or not settings.BSKY_APP_PASSWORD:
        return 0
    pending = [
        uri for entry in entries() for uri in entry["bskyPosts"]
        if not api_cache_get(db, _CACHE_TIER, uri, max_age_hours=_KEEP_HOURS)
    ]
    if not pending:
        return 0
    client = _client()
    if client is None:
        return 0
    deleted = 0
    for uri in pending:
        if _delete_post(client, uri):
            api_cache_set(db, _CACHE_TIER, uri, {"deleted": True}, normal_ttl_hours=_KEEP_HOURS)
            deleted += 1
            logger.info("Deleted retracted Bluesky post %s", uri)
    return deleted
