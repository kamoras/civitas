"""Per-district Cook PVI — per-run ingestion, same persistent-volume
pattern as fetch/committee_leadership.py and fetch/voteview.py.

Was previously a standalone script (scripts/fetch_district_pvi.py) run
manually with its output committed to git under app/data/district_pvi.json.
Fully automated now: this scrapes whichever Cook PVI value each district's
Wikipedia infobox *currently* shows, so — unlike state_pvi.json (see
ops_alerts.check_state_pvi_staleness for why that one is NOT automatable
the same way) — there is no election-year window hardcoded here to go
stale. Re-running this on a schedule naturally picks up whatever Cook
Political Report next publishes and Wikipedia editors transcribe, with no
code change ever required. Supplementary refreshes /data/district_pvi.json
on the same weekly-or-empty cadence as its other ingests. A fetch/gate
failure keeps the previous run's data (never punitive), same contract as
every other automated ingest in this package.
"""

import json
import logging
import pathlib
import re
import urllib.parse

import httpx

from app.atomic_write import write_text_atomic
from app.http_client import make_async_client
from app.ordinals import ordinal
from app.pipeline.fetch.house_clerk import fetch_house_apportionment
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Which districts exist comes from the House Clerk's seat list
# (house_clerk.fetch_house_apportionment), read each refresh: a
# reapportionment after a census, or a state's seat count changing, is
# picked up with no edit here.

API = "https://en.wikipedia.org/w/api.php"
_PVI_PATH = "/data/district_pvi.json"

# A once-a-week batch of ~9 requests to a large, stable public site needs
# no aggressive pacing.
_rate_limiter = RateLimiter(rps=2.0)

_PVI_RE = re.compile(r"(?i)\|\s*(?:cpvi|cook[_ ]?pvi)\s*=\s*([^\n|}]+)")
_VALUE_RE = re.compile(r"(?i)\b(EVEN|[DR]\s*\+\s*\d+)\b")


def district_title(state_name: str, district: int) -> str:
    possessive = f"{state_name}'s"
    if district == 0:
        return f"{possessive} at-large congressional district"
    return f"{possessive} {ordinal(district)} congressional district"


def parse_pvi(wikitext: str) -> int | None:
    m = _PVI_RE.search(wikitext)
    if not m:
        return None
    v = _VALUE_RE.search(m.group(1))
    if not v:
        return None
    raw = v.group(1).upper().replace(" ", "")
    if raw == "EVEN":
        return 0
    sign = 1 if raw.startswith("R") else -1
    return sign * int(raw.split("+")[1])


async def _fetch_batch(titles: list[str], client: httpx.AsyncClient) -> dict[str, str]:
    params = {
        "action": "query", "prop": "revisions", "rvprop": "content",
        "rvslots": "main", "format": "json", "redirects": "1",
        "titles": "|".join(titles),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url,
        retry_on_4xx=False, log_label="district-pvi wikipedia batch",
    )
    if resp is None:
        return {}
    data = resp.json()
    back = {}
    for rd in data["query"].get("redirects", []):
        back[rd["to"]] = rd["from"]
    for norm in data["query"].get("normalized", []):
        back[norm["to"]] = norm["from"]
    out = {}
    for page in data["query"]["pages"].values():
        title = page.get("title", "")
        asked = back.get(title, title)
        revs = page.get("revisions")
        if revs:
            out[asked] = revs[0]["slots"]["main"]["*"]
    return out


def ingestion_gates(result: dict[str, int], seats: dict[str, int]) -> list[str]:
    """Structural sanity checks on the retrieved table — guard the
    ingestion (sign convention, coverage, parse drift), not the scores.
    `seats`: {state: voting seats}, the House's apportionment."""
    failures = []
    expected = sum(seats.values())
    if len(result) != expected:
        failures.append(f"expected {expected} districts, got {len(result)}")
    states = {k.split("-")[0] for k in result}
    if states != set(seats):
        failures.append(f"state coverage mismatch: {sorted(set(seats) ^ states)}")
    vals = list(result.values())
    if not all(-45 <= v <= 45 for v in vals):
        failures.append("PVI outside plausible +/-45 range — parse drift?")
    r_lean = sum(1 for v in vals if v > 0)
    d_lean = sum(1 for v in vals if v < 0)
    # Neither side under about a third of the House (the bound was 150 of
    # 435 when it was a count): a sign flip or a parse that reads every
    # district one way lands far outside it.
    low, high = round(expected * 0.345), round(expected * 0.655)
    if not (low <= r_lean <= high and low <= d_lean <= high):
        failures.append(f"implausible lean split R={r_lean} D={d_lean}")
    return failures


async def refresh_district_pvi(client: httpx.AsyncClient | None = None) -> bool:
    """Fetch, parse, gate, and persist per-district Cook PVI. Returns True
    on a successful write, False otherwise.

    NEVER raises and never writes gated-bad data: any failure keeps the
    previous run's /data/district_pvi.json, logs why, and lets the
    pipeline run continue.
    """
    own_client = client is None
    if own_client:
        client = make_async_client(follow_redirects=True)
    try:
        apportionment = await fetch_house_apportionment(client)
        if not apportionment:
            logger.warning("district-pvi: no apportionment from the House Clerk — keeping previous data")
            return False
        seats = {st: a["seats"] for st, a in apportionment.items()}
        pairs = []
        for st, n in sorted(seats.items()):
            pairs.extend([(st, 0)] if n == 1 else [(st, i) for i in range(1, n + 1)])
        titles = {district_title(apportionment[s]["name"], d): f"{s}-{d}" for s, d in pairs}

        result: dict[str, int] = {}
        missing: list[str] = []
        title_list = list(titles)
        for i in range(0, len(title_list), 50):
            batch = title_list[i:i + 50]
            pages = await _fetch_batch(batch, client)
            for t in batch:
                key = titles[t]
                wt = pages.get(t)
                pvi = parse_pvi(wt) if wt else None
                if pvi is None:
                    missing.append(f"{key} ({t})")
                else:
                    result[key] = pvi

        if missing:
            logger.warning(
                "district-pvi: %d districts unparsed (e.g. %s) — keeping previous data",
                len(missing), missing[0],
            )
            return False

        failures = ingestion_gates(result, seats)
        if failures:
            for f in failures:
                logger.warning("district-pvi ingestion gate failed: %s", f)
            return False

        from datetime import date
        path = pathlib.Path(_PVI_PATH)
        write_text_atomic(path, json.dumps(
            {
                "_source": (
                    "Wikipedia district infoboxes (Cook PVI). Refreshed "
                    "automatically (weekly, or immediately if missing) by "
                    "app/pipeline/fetch/district_pvi.py."
                ),
                "_sign": "positive = R lean, negative = D lean (matches state_pvi.json)",
                "_as_of": date.today().isoformat(),
                "districts": result,
            },
            indent=1, sort_keys=True,
        ) + "\n")

        from app.pipeline.analyze import score_calculator
        score_calculator._district_pvi_cache = None
        logger.info("district-pvi refreshed: %d districts", len(result))
        return True
    except Exception:
        logger.warning(
            "district-pvi refresh failed — keeping previous data; run continues",
            exc_info=True,
        )
        return False
    finally:
        if own_client:
            await client.aclose()
