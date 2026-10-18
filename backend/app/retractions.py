"""Withdrawn content: the public retraction log (app/data/retractions.json).

The log is the record of what Civitas published and withdrew, and why.
The API answers 410 with its reason for an issue in it, and the issue page
shows that reason (marked noindex), so a link someone saw in a post
explains itself rather than breaking; its rows are removed by the data
migration the entry names. A withdrawn race-coverage item is left off its
race's page (by URL, which a data reset's re-ingest keeps), and a withdrawn
post (by id: broadcast_posts survives a reset) is left out of the feeds and
never sent again.
"""

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).parent / "data" / "retractions.json"


@lru_cache(maxsize=1)
def entries() -> list[dict]:
    return json.loads(_PATH.read_text())["retractions"]


def retraction_for_issue(issue_id: str) -> dict | None:
    """The entry withdrawing this issue (public id or legacy numeric id)."""
    for entry in entries():
        if issue_id in entry["publicIds"] or (issue_id.isdigit() and int(issue_id) in entry["issueIds"]):
            return {"date": entry["date"], "reason": entry["reason"]}
    return None


def withdrawn_coverage_urls() -> frozenset[str]:
    """URLs of race-coverage items the log withdraws from race pages."""
    return frozenset(item["url"] for entry in entries() for item in entry.get("coverageItems", []))


def withdrawn_post_ids() -> frozenset[int]:
    """Ids of published posts (broadcast_posts) the log withdraws."""
    return frozenset(post_id for entry in entries() for post_id in entry.get("broadcastPostIds", []))
