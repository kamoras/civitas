"""When each state holds its primary.

The November general is statutory and computed (election_calendar.py); a
PRIMARY date is not. Every state picks its own and they move between
cycles, so nothing here is a stored calendar anybody maintains — every
date is re-read.

There ARE two sources, and both are used, because they cover different
gaps:

  FEC — api.open.fec.gov's election-dates endpoint carries the federal
        primary AND runoff date for every state, DC and the territories,
        in three calls. This is the one that makes "what is every state's
        status right now" answerable at all: it needs no per-state
        coverage, so a state nobody has written an adapter for still gets
        a real date. Verified live 2026-08-18 — 53 jurisdictions, and it
        agrees exactly with the nine dates read independently from state
        feeds the day before, including California, whose own certified
        results file carries no date anywhere.

  The state's own feed — kept as the cross-check and the fallback. It is
        the authority on its own election, it needs no API key, and a
        disagreement between the two is worth knowing about rather than
        averaging away.

Each source kind already knows the answer:

  filings   — the filing list states the election a candidate filed for
              (North Carolina's "03/03/2026"), which is the primary date
              outright.
  tabular   — discovery already dates the results file it picks, whether
              from the URL (Florida's 20260818_...), the folder (North
              Carolina's ENRS/2026_03_03/) or the portal's own
              electionDate.
  clarity   — the elections list carries a Date per election.
  tx_civix  — the elections list carries a date and a TYPE code, so the
              primary identifies itself without string-matching.

Read weekly rather than nightly (see crawl_for_new_sources): a date moves
once a cycle, and there is nothing to gain from asking every night.
"""

import logging
import re
from typing import Any

import httpx

from app.atomic_write import LockTimeout, NotSaved, runtime_data_path, update_json_file
from app.file_cache import Stamp, read_json_preferring, reload_if_moved, new_reload_lock

logger = logging.getLogger(__name__)

_reload_lock = new_reload_lock()

# Three pages covers a cycle's ~240 federal election dates with headroom;
# more than this would mean the endpoint's shape changed, which should stop
# rather than page forever — and a read cut short there is incomplete.
_FEC_MAX_PAGES = 6

_FILE = "state_election_dates.json"
# Set by tests; otherwise runtime_data_path(_FILE).
_PATH: str | None = None

_cache: dict[str, Any] | None = None
_cache_stamp: Stamp = None

# Each state's entry ("{cycle}-{ST}") keeps what each source said under its
# own keys, so neither overwrites the other: "primary"/"runoff" from the
# state's own feed, "fec_primary"/"fec_runoff"/"senate" from the national
# calendar. The state is the authority on its own election and wins where
# both answer; a disagreement is logged, not averaged away. "state_feed"
# lists which of primary/runoff a state's feed wrote: before the two were
# kept apart, the calendar wrote those keys too, and a complete read drops
# such a legacy value (primary_date falls back to fec_primary; a state with
# its own feed rewrites its date within a week). Per key, not per entry: a
# feed that states only the runoff says nothing about a legacy primary.
_STATE_FEED = "state_feed"
_STATE_KEYS = ("primary", "runoff")


def _state_written(entry: dict) -> set[str]:
    written = entry.get(_STATE_FEED)
    if written is True:  # an earlier form of the marker, meaning both
        return set(_STATE_KEYS)
    return set(written) if isinstance(written, list) else set()


def _path() -> str:
    return _PATH or runtime_data_path(_FILE)


def _read(path: str) -> dict[str, Any]:
    # file_cache.read_json_preferring: an unreadable file is not an empty
    # one — returned this once and retried, never kept. (Writes re-read the
    # file under their lock, so it can never be written back as the whole
    # file.)
    data = read_json_preferring(path, default={})
    return data if isinstance(data, dict) else {}


def _load() -> dict[str, Any]:
    global _cache, _cache_stamp
    path = _path()
    # The election pipeline (the pipeline process) writes the file; the API
    # processes read it here and reload when its mtime moves
    # (file_cache.reload_if_moved) — invalidate_cache() reaches only its caller.
    with _reload_lock:
        _cache, _cache_stamp = reload_if_moved([path], _cache, _cache_stamp, lambda: _read(path))
        return _cache


def invalidate_cache() -> None:
    """Re-read the file on next use — at the start of each crawl or sync
    pass, so a pass sees what another process wrote since."""
    global _cache
    _cache = None


def _update(change) -> None:
    """Apply `change` (dict -> dict) to the file as it is on disk now, under
    its lock, so no concurrent writer's change is lost. Raises NotSaved."""
    global _cache
    path = _path()
    try:
        update_json_file(path, change, indent=2, sort_keys=True)
        # Re-read on next use rather than stamp what was written: a stat
        # taken after the write could already describe a later writer's
        # file, and would pin this older copy until the next change.
        _cache = None
    except (OSError, LockTimeout) as error:
        raise NotSaved(f"election dates not saved to {path}: {error}") from error


def primary_date(state: str, cycle: int) -> str | None:
    """The ISO date of `state`'s `cycle` primary, or None if unknown —
    which is the honest answer for a state with no registered source. The
    state's own feed first, then the national calendar."""
    entry = _load().get(f"{cycle}-{state.upper()}") or {}
    return entry.get("primary") or entry.get("fec_primary")


# The key under which a complete read of the national calendar is
# recorded, so "the FEC lists no Senate election here" can be told apart
# from "we have never read the calendar".
_CALENDAR_KEY = "_CALENDAR"


def senate_election_known(state: str, cycle: int) -> bool | None:
    """Whether the FEC's election calendar lists a `cycle` Senate general
    election in `state` — True/False once the calendar has been read in
    full, None if it never has (callers then fall back to the class
    rotation).

    The calendar is the only record of a Senate SPECIAL election that
    exists. Without it the roster treated any Senate filer in a state with
    no regular seat up as running in a special — and minted a "special
    election" for New York and Hawaii in 2026 out of nothing but serial
    filers with no money and no FEC candidate status."""
    known = _load()
    if f"{cycle}-{_CALENDAR_KEY}" not in known:
        return None
    return bool((known.get(f"{cycle}-{state.upper()}") or {}).get("senate"))


def all_dates() -> dict[str, Any]:
    """Every date known, keyed "{cycle}-{STATE}"."""
    return dict(_load())


def _disagreement(state: str, entry: dict) -> None:
    ours, fec = entry.get("primary"), entry.get("fec_primary")
    if ours and fec and ours != fec:
        logger.warning(
            "%s's own feed dates its primary %s; the FEC calendar says %s — "
            "showing the state's", state, ours, fec,
        )


def save(state: str, cycle: int, dates: dict) -> None:
    """Record what a state's own feed says about its cycle ("primary",
    "runoff"). Merges rather than replaces, so a read that knows only the
    primary doesn't drop the runoff. Raises NotSaved."""
    key = f"{cycle}-{state.upper()}"
    stated = {k: v for k, v in dates.items() if v and k in ("primary", "runoff")}

    if not stated:
        return

    def change(known: dict) -> dict:
        entry = known.get(key) or {}
        known[key] = {
            **entry, **stated,
            _STATE_FEED: sorted(_state_written(entry) | set(stated)),
        }
        _disagreement(state.upper(), known[key])
        return known

    _update(change)


def save_calendar(cycle: int, calendar: dict[str, dict], *, complete: bool, read_on: str) -> None:
    """Record the national calendar in one locked write. A COMPLETE read
    is the calendar: every state's FEC fields are replaced by what it says —
    a Senate election it no longer lists is retracted — and the read is
    marked, which is what lets "no Senate election here" be believed. An
    incomplete read only adds: a state missing from it may be on a page
    that failed, so nothing is retracted and the read isn't marked.
    Raises NotSaved."""
    prefix = f"{cycle}-"

    def change(known: dict) -> dict:
        states = set(calendar)
        if complete:
            states |= {
                k[len(prefix):] for k in known
                if k.startswith(prefix) and k != f"{prefix}{_CALENDAR_KEY}"
            }
        for state in states:
            key = f"{prefix}{state}"
            listed = calendar.get(state) or {}
            fec = {
                "fec_primary": listed.get("primary"),
                "fec_runoff": listed.get("runoff"),
                "senate": listed.get("senate"),
            }
            entry = dict(known.get(key) or {})
            if complete:
                for legacy in set(_STATE_KEYS) - _state_written(entry):
                    entry.pop(legacy, None)
            for field, value in fec.items():
                if value:
                    entry[field] = value
                elif complete:
                    entry.pop(field, None)
            if entry:
                known[key] = entry
                _disagreement(state, entry)
            else:
                known.pop(key, None)
        if complete:
            known[f"{prefix}{_CALENDAR_KEY}"] = {"read": read_on}
        return known

    _update(change)


async def fetch_fec_calendar(
    client: httpx.AsyncClient, cycle: int,
) -> tuple[dict[str, dict], bool]:
    """({state: {"primary", "runoff", "senate"}}, complete) for every state
    the FEC lists a federal election for. `complete` only when every page
    came back and there was something on them — a read cut short is still
    returned, since what it has is true, but it can't say what is absent
    (save_calendar). Empty and incomplete on a failure of the first page.

    Special elections are excluded: a special primary is a different race
    on its own schedule, and folding one in would report a state's regular
    primary as whenever its last vacancy happened to be filled.
    """
    from app.pipeline.fetch.fec import _fetch_with_retry

    rows: list[dict] = []
    page, complete = 1, False
    while page <= _FEC_MAX_PAGES:
        payload = await _fetch_with_retry(
            client,
            "https://api.open.fec.gov/v1/election-dates/"
            f"?election_year={cycle}&per_page=100&page={page}",
        )
        if not payload:
            logger.warning("FEC election-date calendar page %d failed — read incomplete", page)
            break
        rows += payload.get("results") or []
        pagination = payload.get("pagination") or {}
        if page >= pagination.get("pages", 1):
            # Complete only as the endpoint itself counts it: a page with
            # no pagination, or one that came back short, can't say what
            # is absent — and a complete read retracts (save_calendar).
            count = pagination.get("count")
            complete = bool(rows) and isinstance(count, int) and len(rows) == count
            if not complete:
                logger.warning(
                    "FEC election-date calendar read %d row(s) of %s — read incomplete",
                    len(rows), count,
                )
            break
        page += 1
    else:
        logger.warning(
            "FEC election-date calendar has more than %d pages — read incomplete",
            _FEC_MAX_PAGES,
        )

    calendar: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r.get("election_date") or ""):
        kind = (row.get("election_type_full") or "").lower()
        state, held = row.get("election_state"), row.get("election_date")
        if not state or not held or "special" in kind:
            continue
        if row.get("office_sought") not in ("H", "S"):
            continue
        entry = calendar.setdefault(state.upper(), {})
        if row.get("office_sought") == "S" and kind == "general election":
            # A regular seat or a special filled on election day (FL and
            # OH in 2026) — either way, a Senate race on this ballot.
            entry.setdefault("senate", held)
        if kind == "primary election":
            entry.setdefault("primary", held)
        elif "runoff" in kind and "general" not in kind:
            entry.setdefault("runoff", held)
    return {s: d for s, d in calendar.items() if d}, complete


async def discover_dates(
    client: httpx.AsyncClient, cycle: int, state: str, source: dict,
) -> dict:
    """{"primary": iso|None, "runoff": iso|None} for this state's cycle,
    read from whatever feed the state's own source already uses."""
    from app.pipeline.fetch.state_candidates_clarity import CLARITY_BASE, _get as _clarity_get
    from app.pipeline.fetch.state_candidates_tabular import _discover_urls
    from app.pipeline.fetch.state_source_crawler import _PRIMARY_RE, _RUNOFF_RE

    st = state.upper()
    strategy = source.get("strategy")

    if strategy == "clarity":
        resp = await _clarity_get(
            client, f"{CLARITY_BASE}/{st}/elections.json", f"{st} Clarity elections",
        )
        try:
            elections = resp.json() if resp is not None else []
        except ValueError:
            elections = []
        found: dict[str, str] = {}
        for entry in elections if isinstance(elections, list) else []:
            if not isinstance(entry, dict):
                continue
            stamp = f"{entry.get('Date') or ''} {entry.get('ElectionName') or ''}"
            if str(cycle) not in stamp or not _PRIMARY_RE.search(stamp):
                continue
            iso = _us_date(str(entry.get("Date") or ""))
            key = "runoff" if _RUNOFF_RE.search(stamp) else "primary"
            if iso and key not in found:
                found[key] = iso
        if not found and (source.get("discovery") or {}).get("mode") == "landing_page":
            # West Virginia's own elections.json is empty — its results
            # ARE reachable (state_candidates_clarity.py's landing_page
            # discovery finds them), but that page carries no exact day,
            # only a year, so no date can be read here either. Logged
            # explicitly rather than left as a silent empty dict, so this
            # is distinguishable in logs from a state whose date genuinely
            # isn't known yet.
            logger.info(
                "%s's Clarity elections list is empty; its landing_page-"
                "discovered results carry no exact date either — leaving "
                "the primary date unknown rather than guessing", st,
            )
        return found

    if strategy == "tx_civix":
        return await _civix_dates(client, cycle, st)

    if strategy == "canvass_xml":
        from app.pipeline.fetch.state_candidates_canvass_xml import discover_primary_date
        return await discover_primary_date(client, cycle, st, source)

    if strategy == "tabular":
        stages = await _discover_urls(client, st, cycle, source.get("discovery") or {})
        dated = [s for s in stages if s.get("held")]
        if not dated:
            return {}
        return {
            "primary": min(s["held"] for s in dated if not s["runoff"]) if any(
                not s["runoff"] for s in dated
            ) else None,
            "runoff": min(
                (s["held"] for s in dated if s["runoff"]), default=None,
            ),
        }
    return {}


def _us_date(raw: str) -> str | None:
    """Clarity writes "6/30/2026"."""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw.strip())
    if not m:
        return None
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


async def _civix_dates(client: httpx.AsyncClient, cycle: int, state: str) -> dict:
    """Civix codes the election TYPE ("P" primary, "RU" runoff), so the
    primary identifies itself without matching any wording."""
    from app.pipeline.fetch.state_candidates_tx import CIVIX_BASE, _HEADERS, _rate_limiter
    from app.pipeline.fetch.http_utils import fetch_with_retry

    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", f"{CIVIX_BASE}/getElectionsByYear/{cycle}",
        timeout=30.0, log_label=f"{state} Civix elections", headers=_HEADERS,
    )
    if resp is None:
        return {}
    try:
        elections = resp.json() or []
    except ValueError:
        return {}
    wanted = {"P": "primary", "RU": "runoff"}
    found: dict[str, str] = {}
    for entry in elections if isinstance(elections, list) else []:
        key = wanted.get(str(entry.get("cdElectionType") or ""))
        raw = str(entry.get("dtElection") or entry.get("dtElectionDate") or "")[:10]
        if key and raw and key not in found:
            iso = raw if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) else _us_date(raw)
            if iso:
                found[key] = iso
    return found
