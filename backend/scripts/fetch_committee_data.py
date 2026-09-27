"""Fetch current committee memberships and chamber leadership titles.

Superseded as the primary data path by app/pipeline/fetch/
committee_leadership.py (2026-07), which runs this same fetch/build/gate
logic automatically inside the Supplementary pipeline (weekly, or
immediately if the persistent volume has no data yet) and writes to
/data/ rather than these bundled files. This script still exists to
regenerate the bundled app/data/*.json fallback below — the copy served
only in the narrow window before a fresh deployment's first automated
ingest completes.

Congress.gov's official API does not expose either of these (confirmed
2026-07: member records carry no committee/leadership fields, and
committee-detail records list bills/reports/nominations handled by that
committee but never a member roster — a real, structural gap, not
something missed by this project's own fetch code). Sourced instead from
unitedstates/congress-legislators (CC0-1.0, actively maintained — verified
live, most recent commit at time of writing already reflected a senator's
death the same day it happened).

Regenerates three files:
  app/data/committee_membership.json — bioguide_id -> [{committeeName,
    chamber, title}], full committees only (not subcommittees, to keep
    scope reasonable for this pass — the source data supports
    subcommittee-level detail as a documented future enhancement).
  app/data/leadership_roles.json — bioguide_id -> current title (e.g.
    "Senate Majority Leader"), only for members with an active role.
    Most members correctly have no entry at all.
  app/data/leadership_tenures.json — bioguide_id -> every leadership role
    held, with start/end dates, for checking whether a member held a
    title on a given vote's date (normalize_votes.MAJORITY_LEADER_TITLES).

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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# The build/gate logic is the automated ingest's own, imported rather than
# copied, so the bundled fallback can't drift from what the pipeline writes.
from app.pipeline.fetch.committee_leadership import (  # noqa: E402
    build_committee_membership,
    build_leadership_roles,
    build_leadership_tenures,
    ingestion_gates,
)

UA = {
    "User-Agent": "CivitasCivicPlatform/1.0 (committee/leadership ingestion; "
                  "contact: mack.ryanm@gmail.com)",
}
SOURCE_BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "data"
DEFAULT_MEMBERSHIP_OUTPUT = DATA_DIR / "committee_membership.json"
DEFAULT_LEADERSHIP_OUTPUT = DATA_DIR / "leadership_roles.json"
DEFAULT_TENURES_OUTPUT = DATA_DIR / "leadership_tenures.json"


def fetch_yaml(filename: str):
    req = urllib.request.Request(f"{SOURCE_BASE}/{filename}", headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return yaml.safe_load(resp.read())


def main() -> int:
    membership_raw = fetch_yaml("committee-membership-current.yaml")
    committees_raw = fetch_yaml("committees-current.yaml")
    legislators_raw = fetch_yaml("legislators-current.yaml")

    committee_membership = build_committee_membership(membership_raw, committees_raw)
    leadership_roles = build_leadership_roles(legislators_raw)
    leadership_tenures = build_leadership_tenures(legislators_raw)

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

    with open(DEFAULT_TENURES_OUTPUT, "w") as f:
        json.dump(
            {
                "_source": (
                    f"unitedstates/congress-legislators (CC0-1.0), retrieved {today}; "
                    "regenerate with backend/scripts/fetch_committee_data.py"
                ),
                "tenures": leadership_tenures,
            },
            f, indent=1, sort_keys=True,
        )

    print(f"wrote {DEFAULT_MEMBERSHIP_OUTPUT}")
    print(f"wrote {DEFAULT_LEADERSHIP_OUTPUT}")
    print(f"wrote {DEFAULT_TENURES_OUTPUT}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
