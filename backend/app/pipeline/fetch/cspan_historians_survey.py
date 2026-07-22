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
  - Grover Cleveland is rated once (historians assess the person, not
    each of this platform's per-term id splits) — applied to both
    cleveland-22 and cleveland-24.

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

from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.historical_executive_orders import resolve_president_id
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://www.c-span.org/presidentsurvey2021/?page=overall"

# C-SPAN's WAF blocks requests with no browser-like User-Agent (confirmed
# 2026-07: a plain httpx/default-UA request 403s, the same UA string this
# codebase already uses for congress.gov's own bot-resistant pages works).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Civitas/1.0)"}

_RATE_LIMITER = RateLimiter(rps=1.0)
_CACHE_TIER = "cspan-historians-survey"
_CACHE_KEY = "2021-overall"
_CACHE_MAX_AGE_HOURS = 24 * 90  # a closed historical survey cycle changes at most once every few years


def _parse_survey_table(html: str) -> dict[str, int]:
    """Returns president_id -> 2021 Final Score (raw C-SPAN points, not
    yet normalized to 0-100 — see calc_historical_legacy)."""
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

        if name == "Grover Cleveland":
            result["cleveland-22"] = score
            result["cleveland-24"] = score
            continue
        if name == "Donald J. Trump":
            # 2021 cycle only rates a completed term — this is Trump-45's
            # just-finished first term, not (nonexistent at the time)
            # trump-47.
            result["trump-45"] = score
            continue

        pid = resolve_president_id(name)
        if pid is None:
            logger.warning("C-SPAN historians survey: no id mapping for %r", name)
            continue
        result[pid] = score

    return result


async def fetch_cspan_historians_survey(client: httpx.AsyncClient, db: Session) -> dict[str, int]:
    """Fetch + parse the 2021 C-SPAN Presidential Historians Survey.

    Returns an empty dict (never None) on failure — callers should treat
    "couldn't fetch this run" as "leave existing rows alone," same as
    every other fetch function's failure posture in this pipeline."""
    cached = api_cache_get(db, _CACHE_TIER, _CACHE_KEY, max_age_hours=_CACHE_MAX_AGE_HOURS)
    if cached is not None:
        return {k: int(v) for k, v in cached["data"].items()}

    resp = await fetch_with_retry(
        client, _RATE_LIMITER, "GET", URL, log_label="C-SPAN historians survey",
        headers=_HEADERS,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Failed to fetch C-SPAN historians survey (%s)", URL)
        return {}

    try:
        data = _parse_survey_table(resp.text)
    except Exception:
        logger.exception("Failed to parse C-SPAN historians survey table")
        return {}

    if len(data) < 40:  # sanity floor — real page covers 44 presidents (46 rows minus Cleveland's dup)
        logger.warning(
            "C-SPAN historians survey parsed to only %d presidents — page structure may have changed",
            len(data),
        )
        return data or {}

    api_cache_set(db, _CACHE_TIER, _CACHE_KEY, {"data": data})
    return data
