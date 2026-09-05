"""Match already-fetched news + freshly-searched Bluesky posts to Races by
candidate-name match with mandatory corroboration.

Deliberately deterministic string matching, not embedding similarity —
but a bare surname is NOT treated as identifying (2026-07 review F8: with
thousands of FEC candidates, the roster's surname set covers a large
fraction of common English surnames, so surname-only matching against
general news/social text GUARANTEES false associations — an article about
any unrelated Smith attached to every race with a Smith in it; the worst
realistic failure is a scandal story pinned to the wrong same-surname
candidate). A match therefore requires the surname PLUS corroboration in
the same text:

  "full_name"       — the candidate's own first name also appears
                      (word-boundary, case-insensitive), or
  "surname_context" — the candidate's state name appears (full name, e.g.
                      "Georgia" — the 2-letter code is far too ambiguous
                      in prose).

If, after corroboration, an item still matches candidates in MORE THAN
ONE race, it is dropped entirely rather than guessed or fanned out — the
same no-guessed-attribution rule fec.find_candidate applies to financial
data (ambiguity => None, never "probably this one"). One item attaches to
at most one race.

Coverage items store the source's own title/summary verbatim — never
LLM-generated — so the on-site feed has no hallucination surface; only
the separate Bluesky-posting path (election_bluesky.py) generates text,
and it is restricted to items matched on the stronger "full_name" basis
(match_basis is stored per item for exactly that gate).
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Candidate, RaceCoverageItem
from app.pipeline.fetch.bluesky_search import search_posts
from app.pipeline.fetch.news_feeds import fetch_news_articles
from app.pipeline.run_tracker import PipelineRunTracker
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Below this length a surname is too likely to produce false-positive
# matches in general news/social text ("OZ", "LEE", "ROE") to trust even
# with corroboration.
MIN_SURNAME_LENGTH = 4

# Bluesky searches per ingestion pass — a rotating, watermarked batch
# (Candidate.last_coverage_search), same bounded-batch design as the FEC
# financial refresh. One pass never searches the whole roster: at the
# 15-minute election-season cadence that would be thousands of requests
# per run (2026-07 review B1).
BLUESKY_SEARCH_BATCH = 50

# In-process guard shared by the 15-minute election-season refresh and the
# nightly pipeline's coverage phase, so two ingestion/posting passes can't
# interleave (2026-07 review B3: duplicate rows and duplicate public
# posts; same pattern as _hourly_action_refresh's guard after the
# 2026-07-13 pileup incident).
_coverage_tracker = PipelineRunTracker()


def is_coverage_refresh_running() -> bool:
    return _coverage_tracker.is_running


def coverage_refresh_age():
    """Wall-clock age of the in-process coverage refresh, or None when idle."""
    return _coverage_tracker.age


def coverage_tracker() -> PipelineRunTracker:
    return _coverage_tracker


# 2-letter state code -> full state name, for the corroboration check.
STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def _surname(name: str) -> str:
    """FEC candidate names are "LAST, FIRST MIDDLE" — the surname is what
    news coverage and social posts actually use, not the full FEC string."""
    return name.split(",")[0].strip()


def _first_name(name: str) -> str | None:
    """First given name from "LAST, FIRST MIDDLE", or None when it's too
    short to corroborate anything (an initial like "J." matches noise)."""
    parts = name.split(",")
    if len(parts) < 2:
        return None
    tokens = parts[1].strip().split()
    if not tokens:
        return None
    first = tokens[0].strip(".")
    return first if len(first) >= 3 else None


def _word_pattern(word: str) -> "re.Pattern[str]":
    return re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)


@dataclass
class CandidateMatcher:
    """Compiled match predicates for one candidate."""
    candidate_id: str
    race_id: str
    state: str
    surname_re: "re.Pattern[str]"
    first_re: "re.Pattern[str] | None"
    state_re: "re.Pattern[str]"

    def match_basis(self, text: str) -> str | None:
        """"full_name" / "surname_context" / None — see module docstring."""
        if not self.surname_re.search(text):
            return None
        if self.first_re is not None and self.first_re.search(text):
            return "full_name"
        if self.state_re.search(text):
            return "surname_context"
        return None


def _build_matchers(db: Session) -> list[CandidateMatcher]:
    matchers = []
    rows = db.query(Candidate.id, Candidate.name, Candidate.race_id).all()
    for cand_id, name, race_id in rows:
        if not name or not race_id:
            continue
        surname = _surname(name)
        if len(surname) < MIN_SURNAME_LENGTH:
            continue
        # Race ids are "{cycle}-SEN-{ST}[-SPECIAL]" / "{cycle}-HOUSE-{ST}-{n}".
        parts = race_id.split("-")
        state_name = STATE_NAMES.get(parts[2]) if len(parts) >= 3 else None
        if state_name is None:
            continue
        first = _first_name(name)
        matchers.append(CandidateMatcher(
            candidate_id=cand_id,
            race_id=race_id,
            state=parts[2],
            surname_re=_word_pattern(surname),
            first_re=_word_pattern(first) if first else None,
            state_re=_word_pattern(state_name),
        ))
    return matchers


_BASIS_RANK = {"full_name": 0, "surname_context": 1}


def resolve_item_race(
    matchers: list[CandidateMatcher], text: str,
) -> tuple[CandidateMatcher, str] | None:
    """The single (matcher, basis) an item attaches to, or None.

    Ambiguity rule: corroborated matches in more than one distinct race
    mean the text doesn't identify one candidate — drop rather than guess
    (or worse, attach everywhere). Multiple matched candidates within the
    SAME race (e.g. an article naming two rivals in one primary) is fine —
    the attachment target is the race; the strongest-basis candidate is
    recorded as the match evidence.
    """
    matched: list[tuple[CandidateMatcher, str]] = []
    for m in matchers:
        basis = m.match_basis(text)
        if basis is not None:
            matched.append((m, basis))
    if not matched:
        return None
    races = {m.race_id for m, _ in matched}
    if len(races) > 1:
        logger.debug(
            "Coverage item matched %d races (%s) — dropped as ambiguous",
            len(races), ", ".join(sorted(races)),
        )
        return None
    return min(matched, key=lambda pair: _BASIS_RANK[pair[1]])


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize to the repo's naive-UTC convention (time_utils.utcnow) at
    the ingestion boundary — RSS/atproto sources hand us aware datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _already_ingested(db: Session, race_id: str, url: str) -> bool:
    return (
        db.query(RaceCoverageItem.id)
        .filter(RaceCoverageItem.race_id == race_id, RaceCoverageItem.url == url)
        .first()
        is not None
    )


def _store_if_new(db: Session, race_id: str, **fields) -> bool:
    if _already_ingested(db, race_id, fields["url"]):
        return False
    db.add(RaceCoverageItem(race_id=race_id, **fields))
    return True


def _candidates_for_bluesky_search(db: Session, limit: int) -> list[Candidate]:
    """Rotating watermarked batch: active candidates only (statutory
    status, raised funds, or incumbent — paper filers don't get search
    traffic), never-searched first, then longest-unsearched first.

    Deliberately not deduped against app/candidate_dedup.py's merge rule:
    this is a flat, cross-race batch, and applying dedupe here would mean
    grouping it by race first. It wouldn't even reliably save a request —
    this module's own _surname (raw "before the comma", unlike
    candidate_dedup's normalized_surname) doesn't strip generational
    suffixes, so a real duplicate pair like "ONDER JR, ROBERT FRANK" /
    "ONDER, ROBERT FOR JR." still produces two different search strings
    ("ROBERT ONDER JR" vs "ROBERT ONDER"). Any overlap in results is
    absorbed downstream anyway (_store_if_new's per-race URL check). Not
    worth the restructuring for a savings this inconsistent — unlike
    _roster_fact (election_bluesky.py), which resolves a stored id
    because posting a wrong/dropped one is a correctness problem, not
    just wasted work.
    """
    return (
        db.query(Candidate)
        .filter(or_(
            Candidate.candidate_status == "C",
            Candidate.has_raised_funds.is_(True),
            Candidate.incumbent_challenge == "I",
        ))
        .order_by(
            Candidate.last_coverage_search.is_(None).desc(),
            Candidate.last_coverage_search.asc(),
        )
        .limit(limit)
        .all()
    )


async def ingest_race_coverage(db: Session, client: httpx.AsyncClient) -> int:
    """Match existing RSS articles + fresh Bluesky search results to races.
    Returns the number of NEW coverage items stored (a url already
    ingested for that race is skipped, not duplicated).
    """
    matchers = _build_matchers(db)
    if not matchers:
        return 0

    ingested = 0

    # ── News: re-classifies articles the Action Center already fetched
    # (fetch_news_articles is cheap/idempotent — it hits the same RSS
    # feeds news_feeds.py always has, no new source added here) ──
    articles = fetch_news_articles()
    for article in articles:
        haystack = f"{article.title} {article.summary}"
        resolved = resolve_item_race(matchers, haystack)
        if resolved is None:
            continue
        matcher, basis = resolved
        if _store_if_new(
            db, matcher.race_id,
            source_type="news", source_name=article.source_name,
            title=(article.title or "")[:500], url=article.url,
            summary=article.summary,
            published_at=_to_naive_utc(article.published),
            matched_candidate_id=matcher.candidate_id, match_basis=basis,
        ):
            ingested += 1

    # ── Bluesky: one full-name search per candidate, rotating bounded
    # batch (see BLUESKY_SEARCH_BATCH). The query is the candidate's
    # "First Last" — not the bare surname — so the search itself is
    # already scoped to the person; results still pass through the same
    # corroborated matcher before anything is stored. ──
    for cand in _candidates_for_bluesky_search(db, BLUESKY_SEARCH_BATCH):
        cand.last_coverage_search = utcnow()
        first = _first_name(cand.name or "")
        surname = _surname(cand.name or "")
        if not first or len(surname) < MIN_SURNAME_LENGTH:
            # Without a usable first name the search query would degrade
            # to the bare surname — exactly the noise source the matcher
            # exists to reject; skip the search (the news path still
            # covers this candidate via corroborated matching).
            continue
        for post in await search_posts(client, f"{first} {surname}"):
            resolved = resolve_item_race(matchers, post.text)
            if resolved is None:
                continue
            post_matcher, basis = resolved
            if _store_if_new(
                db, post_matcher.race_id,
                source_type="bluesky", source_name=f"@{post.author_handle}",
                title=post.text[:200], url=post.url,
                summary=post.text, author=post.author_handle,
                published_at=_to_naive_utc(post.published),
                matched_candidate_id=post_matcher.candidate_id, match_basis=basis,
            ):
                ingested += 1

    db.commit()
    return ingested
