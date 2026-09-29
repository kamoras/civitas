"""Fetch C-SPAN's Presidential Historians Survey — an aggregated expert-
consensus score covering what this platform's other four dimensions
structurally cannot: crisis leadership, moral authority, vision, and
similar historical-consequence judgments that don't reduce to GDP growth,
approval polling, executive-order rate, or rulemaking volume.

This is categorically different from the hand-set Independence/Follow-
Through numbers removed elsewhere in this rewrite. Those were single,
uncited values invented for this platform with no external methodology,
never reproducible by anyone else. This is a real, external, periodically-
run survey (~142 professional historians in the 2021 cycle, scored across
ten categories — Public Persuasion, Crisis Leadership, Economic
Management, Moral Authority, International Relations, Administrative
Skill, Relations with Congress, Vision/Setting an Agenda, Pursued Equal
Justice for All, Performance Within Context of Times) — the same
"trust a well-documented external institution" category as citing BLS or
Federal Register data, just survey-based rather than administrative-
record-based.

Coverage is real but incomplete by construction, not a fetch failure:
  - C-SPAN evaluates a president once their term is complete (2021's
    cycle rated Trump-45's just-finished first term but nothing of
    Biden-46 or Trump-47, who'd only just started or hadn't started yet).
  - The 2025 cycle was explicitly postponed — C-SPAN media relations,
    2026: "with a former president returning to office, conducting the
    survey now would turn it from historical analysis to punditry." 2021
    remains the most recent data. Every currently-serving or just-out-of-
    office president has no score here, same null-when-inapplicable
    pattern as every other dimension in this pipeline.
  - A president with two non-consecutive terms is rated once (historians
    assess the person, not this platform's per-term ids): the score goes
    to each of their terms that had ended by the survey's year — both of
    Cleveland's; in the 2021 edition, Trump's first only. Read from the
    presidents table's term dates, not a per-name rule.

Which edition: the newest C-SPAN publishes. Each refresh tries the
editions from this year back to 2021 (the one the parser was verified
against) and keeps the newest that parses, so a new survey is picked up
without a code change.

Population score stats (mean/stdev, for the z-score+tanh mapping to 0-100
— see president_scorer.calc_historical_legacy) are computed live from
whatever this fetch actually returns, not hardcoded, so they stay correct
if C-SPAN ever republishes an updated cycle.
"""

import logging
import re

import httpx
from lxml import html as lxml_html
from sqlalchemy.orm import Session

from app.models import President
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.historical_executive_orders import name_key, resolve_president_id
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# The edition the parser was verified against (2026-07); newer ones are
# probed each refresh (see the module docstring).
FIRST_EDITION = 2021


def edition_url(edition: int) -> str:
    return f"https://www.c-span.org/presidentsurvey{edition}/?page=overall"

# C-SPAN's WAF blocks requests with no browser-like User-Agent (confirmed
# 2026-07: a plain httpx/default-UA request 403s, the same UA string this
# codebase already uses for congress.gov's own bot-resistant pages works).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Civitas/1.0)"}

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "cspan-historians-survey"
_CACHE_KEY = "latest-overall"
_CACHE_MAX_AGE_HOURS = 24 * 90  # a closed historical survey cycle changes at most once every few years


def _parse_survey_table(html: str, edition: int, terms: list[tuple[str, str, str | None]]) -> dict[str, int]:
    """Returns president_id -> the edition's Final Score (raw C-SPAN points,
    not yet normalized to 0-100 — see calc_historical_legacy). `terms`:
    every president's (id, name, term end), from the presidents table."""
    doc = lxml_html.fromstring(html)
    result: dict[str, int] = {}
    # The page embeds 11 near-identical tables (the aggregate "Final
    # Score" ranking plus one per category, e.g. Economic Management,
    # Crisis Leadership), none distinguishable by table attributes —
    # scoped to div#rgtoverall, the one real container id wrapping only
    # the aggregate table (verified live, 2026-07).
    for row in doc.cssselect("#rgtoverall tr.result"):
        name_cell = row.cssselect("td.name")
        score_cell = row.cssselect("td.score")
        if not name_cell or not score_cell:
            continue
        name = name_cell[0].text_content().strip()
        try:
            score = int(re.sub(r"[^\d]", "", score_cell[0].text_content()))
        except ValueError:
            continue

        ids = rated_terms(name, edition, terms)
        if not ids:
            logger.warning("C-SPAN historians survey: no id mapping for %r", name)
            continue
        for pid in ids:
            result[pid] = score

    return result


def rated_terms(name: str, edition: int, terms: list[tuple[str, str, str | None]]) -> list[str]:
    """The president ids a survey row rates: every term of that person
    (their name, or the id their name resolves to) that had ended by the
    edition's year — historians rate completed terms, and rate a person
    once however many terms they served."""
    resolved = resolve_president_id(name)
    # The person's names: the survey's, and the roster's for the id it
    # resolves to (they can differ: "James A. Garfield" / "James Garfield").
    names = {name_key(name)} | {name_key(n) for pid, n, _ in terms if pid == resolved}
    mine = [(pid, end) for pid, n, end in terms if pid == resolved or name_key(n) in names]
    if not mine:
        return [resolved] if resolved else []
    return [pid for pid, end in mine if end and int(end[:4]) <= edition]


async def fetch_cspan_historians_survey(client: httpx.AsyncClient, db: Session) -> dict[str, int]:
    """Fetch + parse the newest C-SPAN Presidential Historians Survey.

    Returns an empty dict (never None) on failure — callers should treat
    "couldn't fetch this run" as "leave existing rows alone," same as
    every other fetch function's failure posture in this pipeline."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return {k: int(v) for k, v in cached["data"].items()}

    terms = [(p.id, p.name, p.term_end) for p in db.query(President).all()]
    for edition in range(utcnow().year, FIRST_EDITION - 1, -1):
        url = edition_url(edition)
        resp = await fetch_with_retry(
            client, _RATE_LIMITER, "GET", url, log_label="C-SPAN historians survey",
            headers=_HEADERS, retry_on_4xx=False,
        )
        if resp is None or resp.status_code != 200:
            continue  # no such edition (yet), or unreachable: try the one before
        try:
            data = _parse_survey_table(resp.text, edition, terms)
        except Exception:
            logger.exception("Failed to parse C-SPAN historians survey table (%s)", url)
            continue
        # Sanity floor — a real edition rates every past president (44 in
        # 2021); a page that parses to fewer is a layout the parser no
        # longer reads, and an older edition is better than a fragment.
        if len(data) < 40:
            logger.warning("C-SPAN historians survey %s parsed to only %d presidents", url, len(data))
            continue
        api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"edition": edition, "data": data})
        return data

    logger.warning("No C-SPAN historians survey edition could be read")
    return {}
