"""Fake GovInfo responses for the Congress activity tests, built from the
real Daily Digest fixtures. Shared by test_congress_activity and
test_congress_api; not a test module, so nothing here is collected.
"""

import json
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def _fake_get(responses: dict):
    async def fake(client, url, *, label, request_url=None):
        for key, value in responses.items():
            if key in url:
                return value
        return None
    return fake


DIGEST_LISTING = json.dumps({"granules": [
    {"granuleId": "CREC-2026-09-24-pt1-PgD935", "granuleClass": "DAILYDIGEST", "title": "Daily Digest/Senate"},
    {"granuleId": "CREC-2026-09-24-pt1-PgD936", "granuleClass": "DAILYDIGEST", "title": "Daily Digest/Senate Committee Meetings"},
    {"granuleId": "CREC-2026-09-24-pt1-PgD937", "granuleClass": "DAILYDIGEST", "title": "Daily Digest/House of Representatives"},
    {"granuleId": "CREC-2026-09-24-pt1-PgD937-2", "granuleClass": "DAILYDIGEST", "title": "Daily Digest/House Committee Meetings"},
    {"granuleId": "CREC-2026-09-24-pt1-PgD937-4", "granuleClass": "DAILYDIGEST",
     "title": "Daily Digest/Next Meeting of the SENATE + Next Meeting of the HOUSE OF REPRESENTATIVES"},
    {"granuleId": "CREC-2026-09-24-pt1-PgS4959", "granuleClass": "SENATE", "title": "PRAYER"},
]}).encode()


def _digest_responses():
    out = {"api.govinfo.gov/packages/CREC-2026-09-24/granules": DIGEST_LISTING}
    for gid in ("PgD935", "PgD936", "PgD937-2", "PgD937-4", "PgD937"):
        out[f"CREC-2026-09-24-pt1-{gid}.htm"] = (FIX / "daily_digest" / f"CREC-2026-09-24-pt1-{gid}.htm").read_bytes()
    return out
