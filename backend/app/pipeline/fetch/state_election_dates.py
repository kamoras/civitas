"""When each state holds its primary.

The November general is statutory and computed (election_calendar.py); a
PRIMARY date is not. Every state picks its own, they move between cycles,
and there is no free national feed of them — so the only durable answer is
to read each state's date off the same feed its results already come from,
which is what this does. Nothing here is a stored calendar to be
maintained; every date is re-read from the state.

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

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PATHS = (
    "/data/state_election_dates.json",
    os.path.join(os.getcwd(), "data", "state_election_dates.json"),
)

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    for path in _PATHS:
        try:
            with open(path, encoding="utf-8") as fh:
                _cache = json.load(fh) or {}
                return _cache
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception("Failed to read election dates file %s", path)
    _cache = {}
    return _cache


def primary_date(state: str, cycle: int) -> str | None:
    """The ISO date of `state`'s `cycle` primary, or None if unknown —
    which is the honest answer for a state with no registered source."""
    return (_load().get(f"{cycle}-{state.upper()}") or {}).get("primary")


def all_dates() -> dict[str, Any]:
    """Every date known, keyed "{cycle}-{STATE}"."""
    return dict(_load())


def save(state: str, cycle: int, dates: dict) -> None:
    global _cache
    known = dict(_load())
    known[f"{cycle}-{state.upper()}"] = {k: v for k, v in dates.items() if v}
    for path in _PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(known, fh, indent=2, sort_keys=True)
            break
        except OSError:
            continue
    else:
        logger.warning("Nowhere writable to record election dates for %s", state)
    _cache = known


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
        return found

    if strategy == "tx_civix":
        return await _civix_dates(client, cycle, st)

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
