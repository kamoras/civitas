"""Committee membership / chamber leadership — per-run ingestion, same
persistent-volume pattern as fetch/voteview.py's member_ideal_points.json.

Congress.gov's own API exposes neither of these (confirmed 2026-07: member
records carry no committee/leadership fields, and committee-detail records
list bills/reports/nominations handled by that committee but never a
member roster — a real, structural gap). Sourced instead from
unitedstates/congress-legislators (CC0-1.0, actively maintained — verified
live, most recent commit at time of writing already reflected a senator's
death the same day it happened).

Was previously a standalone script (scripts/fetch_committee_data.py) run
manually and its output committed to git under app/data/ — meaning
leadership titles ("Speaker of the House", "Senate Majority Leader", etc.)
only ever changed when someone remembered to re-run it and commit the
result. Fully automated now: Supplementary refreshes /data/committee_
membership.json and /data/leadership_roles.json (the persistent writable
volume) on the same weekly-or-empty cadence as its SCOTUS justice refresh.
A fetch/gate failure keeps the previous run's data (never punitive), same
contract as write_member_ideal_points. The bundled app/data/*.json files
remain as the pre-first-successful-ingest fallback (see transform/
committee_data.py) and as a manually-regenerable baseline for local dev.
"""

import datetime
import json
import logging
import pathlib

import httpx
import yaml

from app.http_client import make_async_client
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

SOURCE_BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
SOURCE_DESC = (
    "unitedstates/congress-legislators (CC0-1.0). Refreshed automatically "
    "(weekly, or immediately if missing) by "
    "app/pipeline/fetch/committee_leadership.py."
)

_MEMBERSHIP_PATH = "/data/committee_membership.json"
_LEADERSHIP_PATH = "/data/leadership_roles.json"

# A small, low-frequency site (three files, once a week) — no aggressive
# pacing needed, but the shared retry/limiter infra keeps a transient
# failure from becoming a gate failure.
_rate_limiter = RateLimiter(rps=2.0)


async def _fetch_yaml(filename: str, client: httpx.AsyncClient):
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", f"{SOURCE_BASE}/{filename}",
        retry_on_4xx=False, log_label=f"committee-leadership {filename}",
    )
    if resp is None:
        return None
    return yaml.safe_load(resp.text)


def build_committee_membership(
    membership_raw: dict, committees_raw: list[dict],
) -> dict[str, list[dict]]:
    """committee code -> {name, chamber} for full committees only (top-level
    thomas_id entries) — subcommittee codes in membership_raw simply won't
    match anything here and are skipped, which is the intended scope cut.
    """
    code_to_committee = {}
    for c in committees_raw:
        code = c.get("thomas_id")
        if not code:
            continue
        code_to_committee[code] = {"name": c.get("name", code), "chamber": c.get("type", "")}

    result: dict[str, list[dict]] = {}
    for code, members in membership_raw.items():
        info = code_to_committee.get(code)
        if not info or not isinstance(members, list):
            continue
        for m in members:
            bioguide = m.get("bioguide")
            if not bioguide:
                continue
            result.setdefault(bioguide, []).append({
                "committeeName": info["name"],
                "chamber": info["chamber"],
                "title": m.get("title"),
            })
    return result


def build_leadership_roles(legislators_raw: list[dict]) -> dict[str, str]:
    today = datetime.date.today().isoformat()
    result: dict[str, str] = {}
    for person in legislators_raw:
        bioguide = (person.get("id") or {}).get("bioguide")
        if not bioguide:
            continue
        roles = person.get("leadership_roles") or []
        current = [r for r in roles if not r.get("end") or r["end"] >= today]
        if not current:
            continue
        current.sort(key=lambda r: r.get("start", ""), reverse=True)
        result[bioguide] = current[0]["title"]
    return result


def ingestion_gates(
    committee_membership: dict[str, list[dict]], leadership_roles: dict[str, str],
) -> list[str]:
    """Structural sanity checks — coverage bounds, not political content.

    535 total members of Congress; most serve on at least one committee,
    and chamber leadership is a small, bounded set of titles per chamber
    per party (leader, whip, conference chair, etc.) — these bounds catch
    a parse failure or an empty/truncated fetch, not "the right people."
    """
    failures = []
    if len(committee_membership) < 400:
        failures.append(
            f"suspiciously low committee-membership coverage: "
            f"{len(committee_membership)} members (expected 400+)",
        )
    if not (10 <= len(leadership_roles) <= 80):
        failures.append(
            f"suspicious leadership-role count: {len(leadership_roles)} "
            f"(expected roughly 10-80 across both chambers/parties)",
        )
    return failures


def _write_json(path: str, key: str, data: dict) -> None:
    p = pathlib.Path(path)
    p.write_text(json.dumps({"_source": SOURCE_DESC, key: data}, indent=1, sort_keys=True) + "\n")


async def refresh_committee_leadership_data(client: httpx.AsyncClient | None = None) -> bool:
    """Fetch, build, gate, and persist committee membership + leadership
    roles. Returns True on a successful write, False otherwise.

    NEVER raises and never writes gated-bad data: any failure keeps the
    previous run's files on the volume, logs why, and lets the pipeline
    run continue — same best-effort-side-artifact contract as
    refresh_member_ideal_points.
    """
    own_client = client is None
    if own_client:
        client = make_async_client(follow_redirects=True)
    try:
        membership_raw = await _fetch_yaml("committee-membership-current.yaml", client)
        committees_raw = await _fetch_yaml("committees-current.yaml", client)
        legislators_raw = await _fetch_yaml("legislators-current.yaml", client)
        if membership_raw is None or committees_raw is None or legislators_raw is None:
            logger.warning(
                "committee-leadership fetch failed — keeping previous "
                "committee_membership.json / leadership_roles.json"
            )
            return False

        committee_membership = build_committee_membership(membership_raw, committees_raw)
        leadership_roles = build_leadership_roles(legislators_raw)
        failures = ingestion_gates(committee_membership, leadership_roles)
        if failures:
            for f in failures:
                logger.warning("committee-leadership ingestion gate failed: %s", f)
            return False

        _write_json(_MEMBERSHIP_PATH, "membership", committee_membership)
        _write_json(_LEADERSHIP_PATH, "roles", leadership_roles)
        from app.pipeline.transform.committee_data import clear_committee_data_cache
        clear_committee_data_cache()
        logger.info(
            "committee-leadership refreshed: %d members with committee "
            "assignments, %d with a current leadership title",
            len(committee_membership), len(leadership_roles),
        )
        return True
    except Exception:
        logger.warning(
            "committee-leadership refresh failed — keeping previous data; "
            "run continues", exc_info=True,
        )
        return False
    finally:
        if own_client:
            await client.aclose()
