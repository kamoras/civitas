"""Scoring revisions: accepted refits that move member scores (AGENTS.md 3a).

A refit of a scoring calibration's structure or threshold
(position_confidence.json, commemorative_calibration.json) is installed by
app/data_refresh.py only under the acceptance rule (app/acceptance.py). When
one is, member scores move at the next run under the same code, so the
scoring version moves with it, by itself: each installed refit is recorded
here (`record`), and the version every score snapshot carries is the code's
ALGORITHM_VERSION followed by the count of refits installed under it
("v6.38.1" after the first). The score trend chart marks the change from
the snapshots' own versions, /changelog lists each revision
(GET /api/score-revisions) beside the code's versions, and the ops notice
says what moved. A new ALGORITHM_VERSION starts its count again.

The record is runtime data on the data volume (/data/scoring_revisions.json),
written by the pipeline process alone and read by both.
"""

from __future__ import annotations

from app.atomic_write import runtime_data_path, update_json_file
from app.file_cache import read_json
from app.time_utils import utcnow

FILE = "scoring_revisions.json"


def revisions() -> list[dict]:
    """Every recorded revision, oldest first."""
    try:
        doc = read_json(runtime_data_path(FILE))
    except OSError:
        return []
    return list((doc or {}).get("revisions") or [])


def scoring_version(base: str) -> str:
    """The version scores carry under the code's `base` version."""
    n = sum(1 for r in revisions() if r.get("base") == base)
    return f"{base}.{n}" if n else base


def record(base: str, file: str, sha256: str, why: str) -> str:
    """Record an installed refit of `file` under `base`; the new version."""
    def add(doc: dict) -> dict:
        past = list(doc.get("revisions") or [])
        n = sum(1 for r in past if r.get("base") == base) + 1
        past.append({"base": base, "version": f"{base}.{n}", "at": utcnow().isoformat(timespec="seconds"),
                     "file": file, "sha256": sha256, "why": why})
        return {**doc, "revisions": past}

    return update_json_file(runtime_data_path(FILE), add, indent=1, end="\n")["revisions"][-1]["version"]
