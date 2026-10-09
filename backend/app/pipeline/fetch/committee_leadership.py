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
membership.json, /data/leadership_roles.json and /data/leadership_
tenures.json (the persistent writable
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
from lxml import etree

from app.atomic_write import write_text_atomic
from app.http_client import make_async_client
from app.pipeline.fetch.house_clerk import MEMBER_DATA_URL
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.pipeline.transform.normalize_members import strip_accents

logger = logging.getLogger(__name__)

SOURCE_BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
SOURCE_DESC = (
    "unitedstates/congress-legislators (CC0-1.0). Refreshed automatically "
    "(weekly, or immediately if missing) by "
    "app/pipeline/fetch/committee_leadership.py."
)

_MEMBERSHIP_PATH = "/data/committee_membership.json"
_LEADERSHIP_PATH = "/data/leadership_roles.json"
_TENURES_PATH = "/data/leadership_tenures.json"

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


# congress-legislators updates its committee file every month or two, and a
# member seated in between has no entry: on 2026-10-08 a senator seated in
# July and a representative seated in September showed no committees, while
# the chambers' own lists named four and one. For a sitting member it doesn't
# list, the chamber's list is read instead — the Clerk's member data for the
# House, senate.gov's committee rosters for the Senate. Only for those
# members: congress-legislators carries the titles both chambers' lists
# leave out ("Ranking Member" in the House) and matched both lists
# everywhere else (every other sitting member, the same day).
SENATE_ROSTER_URL = "https://www.senate.gov/general/contact_information/senators_cfm.xml"
SENATE_COMMITTEE_URL = "https://www.senate.gov/general/committee_membership/committee_memberships_{code}.xml"
# senate.gov's position words, as the form prints them; "Member" is no title.
_SENATE_POSITIONS = {"Chairman": "Chairman", "Ranking": "Ranking Member", "Vice Chairman": "Vice Chairman"}


def _xml_root(body: bytes | None):
    if not body:
        return None
    try:
        return etree.fromstring(body)
    except etree.XMLSyntaxError:
        return None


def house_assignments_from_clerk(
    member_data: bytes | None, committees_raw: list[dict], known: set[str],
) -> dict[str, list[dict]]:
    """Committee assignments from the Clerk's MemberData.xml for the sitting
    members not in `known`, named as congress-legislators names them (the
    Clerk's comcode is its house_committee_id plus "00")."""
    root = _xml_root(member_data)
    if root is None:
        return {}
    by_code = {f"{c['house_committee_id']}00": c for c in committees_raw if c.get("house_committee_id")}
    clerk_names = {c.get("comcode"): " ".join((c.findtext("committee-fullname") or "").split())
                   for c in root.iterfind("committees/committee")}
    result: dict[str, list[dict]] = {}
    for member in root.iterfind("members/member"):
        bioguide = (member.findtext("member-info/bioguideID") or "").strip()
        if not bioguide or bioguide in known:
            continue
        for assignment in member.iterfind("committee-assignments/committee"):
            code = assignment.get("comcode")
            info = by_code.get(code)
            name = info["name"] if info else clerk_names.get(code)
            if not name:
                continue
            result.setdefault(bioguide, []).append({
                "committeeName": name,
                "chamber": info.get("type", "house") if info else "house",
                "title": assignment.get("leadership"),
            })
    return result


def senate_unlisted(roster_xml: bytes | None, known: set[str]) -> dict[tuple[str, str], str]:
    """(state, folded surname) -> bioguide for the senators senate.gov lists
    that congress-legislators' committee file doesn't."""
    root = _xml_root(roster_xml)
    if root is None:
        return {}
    unlisted = {}
    for member in root.iterfind("member"):
        bioguide = (member.findtext("bioguide_id") or "").strip()
        if bioguide and bioguide not in known:
            unlisted[((member.findtext("state") or "").strip(), _fold(member.findtext("last_name")))] = bioguide
    return unlisted


def senate_assignments(committee_xml: bytes | None, info: dict, unlisted: dict[tuple[str, str], str]) -> dict[str, list[dict]]:
    """The unlisted senators' seats on one senate.gov committee roster."""
    root = _xml_root(committee_xml)
    if root is None:
        return {}
    result: dict[str, list[dict]] = {}
    for member in root.iterfind("committees/members/member"):
        bioguide = unlisted.get(((member.findtext("state") or "").strip(), _fold(member.findtext("name/last"))))
        if bioguide:
            result.setdefault(bioguide, []).append({
                "committeeName": info["name"],
                "chamber": info.get("type", "senate"),
                "title": _SENATE_POSITIONS.get((member.findtext("position") or "").strip()),
            })
    return result


def _fold(text: str | None) -> str:
    return " ".join(strip_accents(text or "").lower().split())


async def _fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes | None:
    resp = await fetch_with_retry(client, _rate_limiter, "GET", url, retry_on_4xx=False, log_label=f"committee-leadership {url}")
    return resp.content if resp is not None else None


async def fill_unlisted_members(
    client: httpx.AsyncClient, membership: dict[str, list[dict]], committees_raw: list[dict],
) -> int:
    """Add the chambers' own committee lists for sitting members
    congress-legislators doesn't list (see SENATE_ROSTER_URL's note).
    Best-effort: a list that can't be read adds nothing. Returns how many
    members it added."""
    known = set(membership)
    added = house_assignments_from_clerk(await _fetch_bytes(client, MEMBER_DATA_URL), committees_raw, known)
    unlisted = senate_unlisted(await _fetch_bytes(client, SENATE_ROSTER_URL), known)
    if unlisted:
        for info in committees_raw:
            code = info.get("senate_committee_id")
            if not code:
                continue
            seats = senate_assignments(await _fetch_bytes(client, SENATE_COMMITTEE_URL.format(code=code)), info, unlisted)
            for bioguide, rows in seats.items():
                added.setdefault(bioguide, []).extend(rows)
    for bioguide, rows in added.items():
        membership[bioguide] = rows
    if added:
        logger.info("committee-leadership: %d sitting member(s) read from the chambers' own lists: %s",
                    len(added), ", ".join(sorted(added)))
    return len(added)


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


def build_leadership_tenures(legislators_raw: list[dict]) -> dict[str, list[dict]]:
    """bioguide_id -> every leadership role the member has held, current or
    past: [{title, chamber, start, end}], oldest first. `end` is None for a
    role still held.

    build_leadership_roles keeps only the current title, which is right for
    display but can't answer "did this member hold that title on the day of
    a given vote" — Constituent Alignment needs that (see
    normalize_votes.MAJORITY_LEADER_TITLES), because a title change
    mid-window (a leader whose party loses the majority) would otherwise
    reclassify votes cast under the old title. The source's `start`/`end`
    are the role's own dates; an `end` equal to the next role's `start` is
    the handover day, so a span is read as half-open [start, end).
    """
    result: dict[str, list[dict]] = {}
    for person in legislators_raw:
        bioguide = (person.get("id") or {}).get("bioguide")
        if not bioguide:
            continue
        spans = [
            {
                "title": r["title"],
                "chamber": r.get("chamber"),
                "start": str(r["start"]) if r.get("start") else None,
                "end": str(r["end"]) if r.get("end") else None,
            }
            for r in (person.get("leadership_roles") or [])
            if r.get("title")
        ]
        if spans:
            spans.sort(key=lambda r: r["start"] or "")
            result[bioguide] = spans
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
    write_text_atomic(p, json.dumps({"_source": SOURCE_DESC, key: data}, indent=1, sort_keys=True) + "\n")


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
        try:
            await fill_unlisted_members(client, committee_membership, committees_raw)
        except Exception:
            # Best-effort: congress-legislators' data is still written.
            logger.warning("committee-leadership: the chambers' own lists could not be read", exc_info=True)
        leadership_roles = build_leadership_roles(legislators_raw)
        leadership_tenures = build_leadership_tenures(legislators_raw)
        failures = ingestion_gates(committee_membership, leadership_roles)
        if failures:
            for f in failures:
                logger.warning("committee-leadership ingestion gate failed: %s", f)
            return False

        _write_json(_MEMBERSHIP_PATH, "membership", committee_membership)
        _write_json(_LEADERSHIP_PATH, "roles", leadership_roles)
        _write_json(_TENURES_PATH, "tenures", leadership_tenures)
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
