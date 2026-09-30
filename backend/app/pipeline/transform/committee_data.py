"""Loads committee membership and chamber leadership titles.

Ingested from unitedstates/congress-legislators (CC0-1.0) — Congress.gov's
own API exposes neither (confirmed 2026-07: member records carry no
committee/leadership fields, and committee-detail records list bills/
reports/nominations but never a member roster). Refreshed automatically
by app/pipeline/fetch/committee_leadership.py (weekly, or immediately if
missing) to /data/committee_membership.json, /data/leadership_roles.json
and /data/leadership_tenures.json on the persistent writable volume — same fully-automated, no-manual-step
pattern as member_ideal_points.json.

The bundled app/data/*.json files (git-tracked, updated only by manually
running scripts/fetch_committee_data.py) are checked only as a fallback,
for the narrow window before the first successful automated ingest on a
fresh volume. Same lazy-load-once-and-cache pattern as
score_calculator.py's _district_pvi().
"""

import logging
import pathlib

from app.file_cache import Uncached

logger = logging.getLogger(__name__)

_PERSISTENT_DATA_DIR = pathlib.Path("/data")
_DATA_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data"

_committee_membership_cache: dict[str, list[dict]] | None = None
_leadership_roles_cache: dict[str, str] | None = None
_leadership_tenures_cache: dict[str, list[dict]] | None = None


def _load_json_cache(filename: str, json_key: str, missing_data_context: str) -> dict:
    """Read `json_key` out of `filename`, preferring the persistent volume's
    auto-refreshed copy (/data/) over the git-tracked bundled fallback
    (app/data/), or an empty dict (logged) if neither exists yet. Shared by
    both loaders below."""
    from app.file_cache import read_json_preferring

    def has_section(data) -> bool:
        return isinstance(data, dict) and isinstance(data.get(json_key), dict)

    try:
        data = read_json_preferring(
            _PERSISTENT_DATA_DIR / filename, _DATA_DIR / filename, default=None, accept=has_section,
        )
    except Uncached as unreadable:
        # The volume copy exists but can't be read right now: the fallback's
        # section, this once (Uncached again, for the caller's cache).
        raise Uncached(unreadable.value[json_key] if unreadable.value else {}) from None
    if data is not None:
        return data[json_key]
    logger.warning(
        "%s unavailable in /data or the bundled fallback — %s until the "
        "first successful committee_leadership refresh",
        filename, missing_data_context,
    )
    return {}


def load_committee_membership() -> dict[str, list[dict]]:
    """bioguide_id -> [{committeeName, chamber, title}, ...]."""
    global _committee_membership_cache
    if _committee_membership_cache is None:
        try:
            _committee_membership_cache = _load_json_cache(
                "committee_membership.json", "membership", "committees will be empty",
            )
        except Uncached as unreadable:
            return unreadable.value  # not kept: read again next time
    return _committee_membership_cache


def load_leadership_roles() -> dict[str, str]:
    """bioguide_id -> current leadership title (e.g. "Senate Majority Leader").

    Most members correctly have no entry at all — absence means "no
    current leadership title," not missing data.
    """
    global _leadership_roles_cache
    if _leadership_roles_cache is None:
        try:
            _leadership_roles_cache = _load_json_cache(
                "leadership_roles.json", "roles", "leadership titles will be empty",
            )
        except Uncached as unreadable:
            return unreadable.value  # not kept: read again next time
    return _leadership_roles_cache


def load_leadership_tenures() -> dict[str, list[dict]]:
    """bioguide_id -> [{title, chamber, start, end}, ...]: every leadership
    role held, with its dates (end None = still held). Built by
    committee_leadership.build_leadership_tenures from the same source as
    load_leadership_roles, which keeps only the current title.
    """
    global _leadership_tenures_cache
    if _leadership_tenures_cache is None:
        try:
            _leadership_tenures_cache = _load_json_cache(
                "leadership_tenures.json", "tenures",
                "leadership tenure checks fall back to current titles",
            )
        except Uncached as unreadable:
            return unreadable.value  # not kept: read again next time
    return _leadership_tenures_cache


def leadership_tenures_on_volume() -> bool:
    """Whether the persistent volume has its own leadership_tenures.json.
    The file was added after leadership_roles.json, so a volume that
    already had roles still needs one refresh to get it rather than
    serving the bundled fallback until the weekly cadence comes round."""
    return (_PERSISTENT_DATA_DIR / "leadership_tenures.json").exists()


def clear_committee_data_cache() -> None:
    """Clear cached lookups (call between pipeline runs if data was refreshed)."""
    global _committee_membership_cache, _leadership_roles_cache, _leadership_tenures_cache
    _committee_membership_cache = None
    _leadership_roles_cache = None
    _leadership_tenures_cache = None
