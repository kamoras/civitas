"""Fetch current committee memberships and chamber leadership titles.

Congress.gov's official API does not expose either of these (confirmed
2026-07: member records carry no committee/leadership fields, and
committee-detail records list bills/reports/nominations handled by that
committee but never a member roster — a real, structural gap, not
something missed by this project's own fetch code). Sourced instead from
unitedstates/congress-legislators (CC0-1.0, actively maintained — verified
live, most recent commit at time of writing already reflected a senator's
death the same day it happened).

Regenerates two files:
  app/data/committee_membership.json — bioguide_id -> [{committeeName,
    chamber, title}], full committees only (not subcommittees, to keep
    scope reasonable for this pass — the source data supports
    subcommittee-level detail as a documented future enhancement).
  app/data/leadership_roles.json — bioguide_id -> current title (e.g.
    "Senate Majority Leader"), only for members with an active role.
    Most members correctly have no entry at all.

Run from the repo (network required):
    python3 backend/scripts/fetch_committee_data.py

Exits 1 if any ingestion gate fails.
"""

import datetime
import json
import pathlib
import sys
import urllib.request

import yaml

UA = {
    "User-Agent": "CivitasCivicPlatform/1.0 (committee/leadership ingestion; "
                  "contact: mack.ryanm@gmail.com)",
}
SOURCE_BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "data"
DEFAULT_MEMBERSHIP_OUTPUT = DATA_DIR / "committee_membership.json"
DEFAULT_LEADERSHIP_OUTPUT = DATA_DIR / "leadership_roles.json"


def fetch_yaml(filename: str):
    req = urllib.request.Request(f"{SOURCE_BASE}/{filename}", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return yaml.safe_load(resp.read())


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


def main() -> int:
    membership_raw = fetch_yaml("committee-membership-current.yaml")
    committees_raw = fetch_yaml("committees-current.yaml")
    legislators_raw = fetch_yaml("legislators-current.yaml")

    committee_membership = build_committee_membership(membership_raw, committees_raw)
    leadership_roles = build_leadership_roles(legislators_raw)

    print(f"{len(committee_membership)} members with >=1 full-committee assignment")
    print(f"{len(leadership_roles)} members with a current leadership title:")
    for bioguide, title in sorted(leadership_roles.items(), key=lambda kv: kv[1]):
        print(f"  {title:<40} {bioguide}")

    failures = ingestion_gates(committee_membership, leadership_roles)
    for f in failures:
        print("GATE FAILED:", f)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    with open(DEFAULT_MEMBERSHIP_OUTPUT, "w") as f:
        json.dump(
            {
                "_source": (
                    f"unitedstates/congress-legislators (CC0-1.0), retrieved {today}; "
                    "regenerate with backend/scripts/fetch_committee_data.py"
                ),
                "membership": committee_membership,
            },
            f, indent=1, sort_keys=True,
        )
    with open(DEFAULT_LEADERSHIP_OUTPUT, "w") as f:
        json.dump(
            {
                "_source": (
                    f"unitedstates/congress-legislators (CC0-1.0), retrieved {today}; "
                    "regenerate with backend/scripts/fetch_committee_data.py"
                ),
                "roles": leadership_roles,
            },
            f, indent=1, sort_keys=True,
        )

    print(f"wrote {DEFAULT_MEMBERSHIP_OUTPUT}")
    print(f"wrote {DEFAULT_LEADERSHIP_OUTPUT}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
