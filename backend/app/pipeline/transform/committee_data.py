"""Loads committee membership and chamber leadership titles.

Ingested from unitedstates/congress-legislators (CC0-1.0) into
app/data/committee_membership.json and app/data/leadership_roles.json by
scripts/fetch_committee_data.py — Congress.gov's own API exposes neither
(confirmed 2026-07: member records carry no committee/leadership fields,
and committee-detail records list bills/reports/nominations but never a
member roster). Same lazy-load-once-and-cache pattern as
score_calculator.py's _district_pvi().
"""

import json
import logging
import pathlib

logger = logging.getLogger(__name__)

_DATA_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data"

_committee_membership_cache: dict[str, list[dict]] | None = None
_leadership_roles_cache: dict[str, str] | None = None


def _load_json_cache(filename: str, json_key: str, missing_data_context: str) -> dict:
    """Read `json_key` out of app/data/`filename`, or an empty dict (logged)
    if the file hasn't been generated yet. Shared by both loaders below —
    same file-missing-is-normal-until-the-fetch-script-runs handling."""
    path = _DATA_DIR / filename
    try:
        return json.loads(path.read_text())[json_key]
    except Exception:
        logger.warning(
            "%s unavailable — %s until scripts/fetch_committee_data.py is run",
            filename, missing_data_context,
        )
        return {}


def load_committee_membership() -> dict[str, list[dict]]:
    """bioguide_id -> [{committeeName, chamber, title}, ...]."""
    global _committee_membership_cache
    if _committee_membership_cache is None:
        _committee_membership_cache = _load_json_cache(
            "committee_membership.json", "membership", "committees will be empty",
        )
    return _committee_membership_cache


def load_leadership_roles() -> dict[str, str]:
    """bioguide_id -> current leadership title (e.g. "Senate Majority Leader").

    Most members correctly have no entry at all — absence means "no
    current leadership title," not missing data.
    """
    global _leadership_roles_cache
    if _leadership_roles_cache is None:
        _leadership_roles_cache = _load_json_cache(
            "leadership_roles.json", "roles", "leadership titles will be empty",
        )
    return _leadership_roles_cache


def clear_committee_data_cache() -> None:
    """Clear cached lookups (call between pipeline runs if data was refreshed)."""
    global _committee_membership_cache, _leadership_roles_cache
    _committee_membership_cache = None
    _leadership_roles_cache = None
