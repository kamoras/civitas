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
                      in prose). This corroborates nothing when the
                      OUTLET is that state's own newsroom, which names
                      the state in nearly every article it runs; those
                      matches must additionally clear the relevance bar
                      (_corroboration_is_vacuous).

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
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze import race_relevance
from app.pipeline.fetch.bluesky_search import search_is_available, search_posts
from app.pipeline.fetch.news_feeds import (
    STATE_OUTLET_NAMES,
    fetch_news_articles,
    fetch_state_news_articles,
)
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


def _full_name_pattern(first: str, surname: str) -> "re.Pattern[str]":
    """The two names TOGETHER — "Bill Hill" or "Hill, Bill" — not merely
    both present somewhere in the text.

    The old rule asked only that the surname appear capitalised and the
    first name appear anywhere at all, in any position, any case. That is
    how Alaska's at-large race — which has a real, $1.3M-raised candidate
    named BILL HILL — collected "The Hill" plus "I'm just a bill, sittin
    here on Capitol Hill" as full-name coverage, and then posted about it
    nine times. Measured across all 7,471 stored full_name matches, 25.1%
    were incidental in exactly this way: "Cameron Hamilton to lead FEMA"
    for Daniel Cameron, "Adam Driver will play Mister Sinister" for Adam
    Delgado, "Warner Bros. bid" for William Todd Warner.

    Up to two intervening tokens carry real middle names and initials
    ("Robert F. Kennedy"). Compiled case-INSENSITIVELY, with
    capitalisation verified on the matched text by the caller, for the
    same reason _matches_as_a_name documents: building "Mcconnell" to
    compare case-sensitively rejects every real "McConnell".
    """
    f, ln = re.escape(first), re.escape(surname)
    return re.compile(
        rf"\b{f}\b(?:\s+[A-Za-z][\w.'’-]*){{0,2}}\s+\b{ln}\b"
        rf"|\b{ln}\b,?\s+\b{f}\b",
        re.IGNORECASE,
    )


def _matches_full_name(pattern: "re.Pattern[str]", text: str) -> bool:
    """True where first and last name occur together, both capitalised."""
    for m in pattern.finditer(text):
        tokens = [t for t in re.split(r"[\s,]+", m.group(0)) if t]
        if len(tokens) >= 2 and tokens[0][:1].isupper() and tokens[-1][:1].isupper():
            return True
    return False


def _matches_as_a_name(pattern: "re.Pattern[str]", text: str) -> bool:
    """True only where the word occurs CAPITALISED — i.e. as a name.

    Plenty of real candidate surnames are ordinary English nouns, and
    case-insensitive matching attached their races to any article using
    the word. Measured against the 554 live coverage items: HAND matched
    "born without a right hand", REGISTER matched "deciding to register",
    GREEN matched "waves the green flag", PEOPLE matched "describes
    people who move somewhere", plus ELSE, CASE, LONG, DREW, LIGHT,
    MILLION, MARKS, DRIVER.

    Requiring a capital costs 6.3% of stored items (35 of 554) and every
    one of the dozen sampled was a false positive — none was real
    coverage. It needs no curated stop-word list, and it keeps intercaps
    names, which is why the match is found case-insensitively and only
    the matched TEXT is checked: lower-casing the tail to build
    "Mcconnell" would reject every real "McConnell" (that error is what
    a first, wrong measurement of this rule reported as an 18.8% cost).

    ALL-CAPS headlines still qualify — the first character is a capital.
    """
    return any(m.group(0)[:1].isupper() for m in pattern.finditer(text))


def _state_name_pattern(state_name: str) -> "re.Pattern[str]":
    """Like _word_pattern, but refuses a match enclosed in a longer state name.

    Word boundaries are not enough here: "Virginia" word-matches inside
    "West Virginia", so every West Virginia story corroborated every
    Virginia candidate whose surname appeared anywhere in it. Found live
    — a story about a former WEST Virginia senator "deciding to register
    as an independent" attached to VA-5 because the roster has a
    candidate surnamed REGISTER.

    Virginia/West Virginia is the ONLY such pair among the fifty (checked
    exhaustively, not assumed: no other state name word-matches inside
    another), but the guard is derived from STATE_NAMES rather than
    hardcoded so it stays correct if that map ever changes.
    """
    guards = ""
    for other in STATE_NAMES.values():
        if other == state_name:
            continue
        m = re.search(r"\b" + re.escape(state_name) + r"\b", other, re.IGNORECASE)
        if m:
            # Fixed-width lookbehind ("West "), which Python's re requires.
            guards += f"(?<!{re.escape(other[:m.start()])})"
    return re.compile(guards + r"\b" + re.escape(state_name) + r"\b", re.IGNORECASE)


@dataclass
class CandidateMatcher:
    """Compiled match predicates for one candidate."""
    candidate_id: str
    race_id: str
    state: str
    surname_re: "re.Pattern[str]"
    full_name_re: "re.Pattern[str] | None"
    state_re: "re.Pattern[str]"

    def match_basis(self, text: str) -> str | None:
        """"full_name" / "surname_context" / None — see module docstring."""
        if not _matches_as_a_name(self.surname_re, text):
            return None
        if self.full_name_re is not None and _matches_full_name(self.full_name_re, text):
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
            full_name_re=_full_name_pattern(first, surname) if first else None,
            state_re=_state_name_pattern(state_name),
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


def _title_key(title: str | None) -> str:
    """The identity of a story, independent of who republished it."""
    return re.sub(r"\s+", " ", (title or "")).strip().lower()


def _same_story_already_on_race(db: Session, race_id: str, title: str | None) -> bool:
    """True when this race already carries this story under another URL.

    The URL check above cannot see syndication. States Newsroom
    distributes one piece to its whole network, so "Flock surveillance
    cameras raise constitutional questions" arrived from Oklahoma Voice,
    Ohio Capital Journal, Kentucky Lantern, Florida Phoenix, Nevada
    Current and seventeen more — every one a distinct, legitimate URL
    from a distinct, legitimate outlet. Measured on the live database:
    MO-6 carried 25 items that were 4 stories, and one race held 22
    copies of that single headline.

    Deduping on the headline keeps the first outlet to run it and drops
    the reprints. Across the whole corpus this removes 75 rows and
    leaves every race that had coverage still having coverage — no race
    is emptied, because a reprint never carries information the original
    did not.
    """
    key = _title_key(title)
    if not key:
        return False
    return (
        db.query(RaceCoverageItem.id)
        .filter(
            RaceCoverageItem.race_id == race_id,
            func.lower(func.trim(RaceCoverageItem.title)) == key,
        )
        .first()
        is not None
    )


def _drop_syndicated_reprints(db: Session) -> int:
    """Retroactively collapse stories a race already carries twice.

    Same reasoning as _drop_items_the_matcher_would_now_reject: nothing
    ever deletes a RaceCoverageItem, so a rule added today has to reach
    backwards or the stored data and the rules drift apart permanently.
    The oldest row wins — it is the outlet that ran the story first.
    """
    doomed: list[int] = []
    kept: set[tuple[str, str]] = set()
    rows = (
        db.query(RaceCoverageItem.id, RaceCoverageItem.race_id, RaceCoverageItem.title)
        .order_by(RaceCoverageItem.id)
        .all()
    )
    for item_id, race_id, title in rows:
        key = _title_key(title)
        if not key:
            continue
        if (race_id, key) in kept:
            doomed.append(item_id)
        else:
            kept.add((race_id, key))

    if not doomed:
        return 0
    for i in range(0, len(doomed), 500):
        (db.query(RaceCoverageItem)
           .filter(RaceCoverageItem.id.in_(doomed[i:i + 500]))
           .delete(synchronize_session=False))
    db.commit()
    logger.info("Dropped %d syndicated reprints already covered on their race", len(doomed))
    return len(doomed)


def _drop_items_the_matcher_would_now_reject(
    db: Session, matchers: list[CandidateMatcher],
) -> int:
    """Re-validate stored coverage against the CURRENT matching rules.

    Nothing ever deletes a RaceCoverageItem — there is no retention
    sweep — so an item attached by a rule later found to be wrong stays
    in the database for good. The API orders newest-first under a cap,
    which pushes old items off a busy race's feed, but a quiet race
    keeps showing its false positive indefinitely: NE-3's was an article
    about a government shutdown, matched because a candidate there is
    surnamed ELSE.

    So every tightening of the matcher has to apply retroactively, or
    the rules and the stored data drift apart permanently.

    Deliberately keyed on the item's OWN matched candidate rather than
    re-running the whole race: if that candidate has since left the
    roster there is nothing to re-validate against, and dropping the
    item would delete real coverage every time the roster churns. Those
    are left alone.
    """
    by_candidate = {m.candidate_id: m for m in matchers}
    if not by_candidate:
        return 0  # never prune against an empty roster

    doomed: list[int] = []
    for item in db.query(RaceCoverageItem).all():
        matcher = by_candidate.get(item.matched_candidate_id)
        if matcher is None:
            continue
        if matcher.match_basis(f"{item.title or ''} {item.summary or ''}") is None:
            doomed.append(item.id)

    if not doomed:
        return 0
    for i in range(0, len(doomed), 500):
        (db.query(RaceCoverageItem)
           .filter(RaceCoverageItem.id.in_(doomed[i:i + 500]))
           .delete(synchronize_session=False))
    db.commit()
    logger.info(
        "Dropped %d stored coverage items the current matcher rejects", len(doomed))
    return len(doomed)


def _corroboration_is_vacuous(source_name: str | None, match_basis: str | None) -> bool:
    """True where "surname_context" corroborated nothing.

    A bare surname is not identifying — this module's docstring explains
    why — so a surname match needs corroboration, and "the candidate's
    state name appears in the same text" was a sound one while every
    feed was national. Adding 41 per-state outlets broke that
    assumption: the Kentucky Lantern says "Kentucky" in virtually every
    article it publishes, so the corroboration fires on all of them and
    surname_context silently degenerates into exactly the bare-surname
    match it exists to prevent.

    Measured over the live corpus, the interaction is unambiguous —
    full_name is unaffected by which feed an item came from, while
    surname_context degrades 2.6x:

        national / full_name        n=351  mean 0.301   6% below 0.1
        national / surname_context  n=112  mean 0.258  14% below 0.1
        state    / full_name        n= 30  mean 0.295   3% below 0.1
        state    / surname_context  n=128  mean 0.166  37% below 0.1

    That 37% is "Williams sisters, at 44 and 46, reunite their iconic
    doubles team" on OH-9, a Minnesota salmonella outbreak on SEN-MN,
    and "A Kentucky man with a meat allergy was craving a burger" on
    KY-1 — each one a different person who happens to share a surname
    with a candidate, in an article whose outlet names the state by
    definition.

    A filter's correct case and its blind spot look identical in code;
    what separated them here was measuring the same rule against the
    two populations it now spans.
    """
    return bool(source_name) and source_name in STATE_OUTLET_NAMES and match_basis == "surname_context"


def _drop_vacuously_corroborated(db: Session) -> int:
    """Hold items whose corroboration was vacuous to the relevance bar.

    Dropping them outright was measured first and costs too much: 128
    items and 12 emptied races, including "Poll finds opposition to
    Maryland redistricting ballot question" (0.426) and "WA voters of
    color more likely to have ballots challenged" (0.427). Those are
    real coverage that merely lacked a first name. 37% of the group is
    junk, not all of it.

    So the weaker match is not rejected, it is made to earn its place
    semantically — and on the threshold race_relevance already derives
    by Otsu from the corpus, not a number typed here. Applied to the
    live corpus this keeps 504 of 621 items across 165 of 174 races,
    removes every one of the 47 items scoring below 0.1, and retains
    the Maryland, Washington, West Virginia and Georgia ballot stories
    above.

    An UNSCORED item is left alone rather than dropped. score_unscored_items
    is capped at 500 per pass, so a backlog leaves real coverage with a
    null relevance, and treating null as "scored badly" would delete it
    for being new. A missing score is not a low score; those items wait
    for the next pass and are judged once they have one.

    Note this deliberately does NOT gate the whole news feed on
    relevance. Doing so was measured and rejected: at the same derived
    threshold it emptied 64 of 174 races, discarding "Massie says he
    hid Hegseth impeachment resolution from GOP leaders" along with the
    tennis. Otsu splits horserace coverage from other political
    coverage, which is not the same cut as real from junk — deriving a
    number is not a reason to adopt it. The gate applies only where the
    corroboration is independently known to be vacuous.
    """
    bar = race_relevance.threshold(db)
    doomed = [
        item.id
        for item in db.query(RaceCoverageItem).filter(
            RaceCoverageItem.match_basis == "surname_context").all()
        if _corroboration_is_vacuous(item.source_name, item.match_basis)
        and item.relevance is not None
        and item.relevance < bar
    ]
    if not doomed:
        return 0
    for i in range(0, len(doomed), 500):
        (db.query(RaceCoverageItem)
           .filter(RaceCoverageItem.id.in_(doomed[i:i + 500]))
           .delete(synchronize_session=False))
    db.commit()
    logger.info(
        "Dropped %d state-outlet surname matches below the relevance bar %.4f",
        len(doomed), bar)
    return len(doomed)


def _store_if_new(
    db: Session, race_id: str, seen: set[tuple[str, str]], **fields,
) -> bool:
    """Store one coverage item, skipping anything already ingested.

    `seen` carries the rows added earlier in THIS pass, and it is not
    redundant with the query: SessionLocal is built with
    autoflush=False, so a pending row is invisible to
    _already_ingested until the pass commits. Both halves are needed —
    the query for previous passes, the set for this one.

    Without it a pass that legitimately attaches one item to a race
    twice raises IntegrityError on
    uq_race_coverage_race_url at commit, losing the ENTIRE pass
    including the news items. That is not hypothetical: one Bluesky
    post naming two rivals in the same primary resolves to the same
    race twice, which is exactly the case this module's own docstring
    calls fine ("the attachment target is the race"). It stayed hidden
    only because Bluesky search had been returning nothing at all.
    """
    url_key = ("url", race_id, fields["url"])
    if url_key in seen or _already_ingested(db, race_id, fields["url"]):
        return False
    # Same story, different outlet — see _same_story_already_on_race.
    title_key = ("title", race_id, _title_key(fields.get("title")))
    if title_key[2]:
        if title_key in seen or _same_story_already_on_race(db, race_id, fields.get("title")):
            return False
        seen.add(title_key)
    seen.add(url_key)
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

    # Before ingesting, reconcile what is already stored with the rules
    # as they stand now. A no-op once the corpus is clean.
    _drop_items_the_matcher_would_now_reject(db, matchers)
    _drop_syndicated_reprints(db)

    ingested = 0
    # Rows added in THIS pass, invisible to _already_ingested because
    # SessionLocal sets autoflush=False. Shared by both loops: a news
    # article and a Bluesky post can resolve to the same race+url.
    seen: set[tuple[str, str, str]] = set()

    # ── News: the national feeds the Action Center already fetched
    # (cheap/idempotent), PLUS the per-state political outlets.
    #
    # The national eight cannot cover 50 states' House and Senate races,
    # and that gap is what the open Bluesky name search was filling with
    # 94% noise. Widening the SOURCES is the fix that filtering was
    # standing in for. State outlets are read here and not by the Action
    # Center, so national issue ranking is untouched.
    articles = fetch_news_articles() + fetch_state_news_articles()
    for article in articles:
        haystack = f"{article.title} {article.summary}"
        resolved = resolve_item_race(matchers, haystack)
        if resolved is None:
            continue
        matcher, basis = resolved
        if _store_if_new(
            db, matcher.race_id, seen,
            source_type="news", source_name=article.source_name,
            title=(article.title or "")[:500], url=article.url,
            summary=article.summary,
            published_at=_to_naive_utc(article.published),
            matched_candidate_id=matcher.candidate_id, match_basis=basis,
        ):
            ingested += 1

    searched = 0
    # ── Bluesky candidate-name search: DISABLED 2026-09-24 ──
    #
    # An open keyword search of the whole network for a candidate's name
    # produced 7,740 of the 8,239 stored coverage items — 94% — and the
    # content was not coverage. Minnesota's page carried "Dave Hughes
    # still a whiny cunt", and directly beneath it a post about the
    # AUSTRALIAN comedian of the same name defending Pauline Hanson's One
    # Nation, filed as MN-7 election coverage.
    #
    # Four successive filters were built against this feed and each
    # failed in a different direction: source-type discarded real local
    # newsrooms; relevance admitted campaign material (maximally on-topic
    # for a campaign); no-advocacy still admitted mockery and a Celtic
    # football post; and the domain-handle rule — shipped the same day —
    # does not catch @crowbar.wtf, which is a domain.
    #
    # The signal being searched for is not there. A name mention is not
    # coverage, four filters could not make it one, and every hour this
    # ran it added more rows nobody should see. The search module and its
    # matcher are kept intact for a future use with a real source list;
    # what is removed is pointing it at the open network.
    for cand in []:
        first = _first_name(cand.name or "")
        surname = _surname(cand.name or "")
        if not first or len(surname) < MIN_SURNAME_LENGTH:
            # Without a usable first name the search query would degrade
            # to the bare surname — exactly the noise source the matcher
            # exists to reject; skip the search (the news path still
            # covers this candidate via corroborated matching). The
            # watermark still advances: this candidate is unsearchable by
            # name every run, so leaving it would wedge the queue's head.
            cand.last_coverage_search = utcnow()
            continue

        posts = await search_posts(client, f"{first} {surname}")

        # An unavailable SOURCE is not a finding of no coverage. Bluesky
        # withdrew unauthenticated searchPosts in 2026-09 and every call
        # 403'd for weeks, while this loop kept stamping the watermark —
        # so candidates were rotated past as "searched" having never been
        # searched. Stop the pass instead, leaving the watermark untouched
        # so the same candidates come back first once the source returns.
        if not search_is_available():
            logger.warning(
                "Bluesky search unavailable — ending this pass after %d "
                "candidates; watermarks left unchanged so none are skipped",
                searched,
            )
            break

        cand.last_coverage_search = utcnow()
        searched += 1
        for post in posts:
            resolved = resolve_item_race(matchers, post.text)
            if resolved is None:
                continue
            post_matcher, basis = resolved
            if _store_if_new(
                db, post_matcher.race_id, seen,
                source_type="bluesky", source_name=f"@{post.author_handle}",
                title=post.text[:200], url=post.url,
                summary=post.text, author=post.author_handle,
                published_at=_to_naive_utc(post.published),
                matched_candidate_id=post_matcher.candidate_id, match_basis=basis,
            ):
                ingested += 1

    db.commit()
    score_unscored_items(db)
    _drop_vacuously_corroborated(db)
    return ingested


def score_unscored_items(db: Session, batch: int = 500) -> int:
    """Fill in relevance/has_advocacy for items that have never been scored.

    Done at INGEST rather than per request because the feed cannot embed
    itself on every page load, and in one batched pass because encoding
    500 texts together costs far less than 500 separate calls.

    Two different questions get asked, because they have different
    answers: relevance is "is this about the race" (semantic, embedding)
    and has_advocacy is "does this tell a reader how to vote"
    (structural, and absolute regardless of relevance). Measured over
    1,500 real Bluesky items: 31.5% clear relevance and 7% of those are
    campaign advocacy — a feed gated on relevance alone would carry
    "Elect Jonathan Nez to Congress!" as race coverage.
    """
    from app.pipeline.analyze.grounding import electioneering_language
    from app.pipeline.analyze.race_relevance import (
        item_text, race_descriptor, score_pairs,
    )

    rows = (
        db.query(RaceCoverageItem)
        .filter(RaceCoverageItem.relevance.is_(None))
        .limit(batch)
        .all()
    )
    if not rows:
        return 0
    races = {r.id: r for r in db.query(Race).filter(
        Race.id.in_({r.race_id for r in rows})).all()}
    pairs = [(it, races[it.race_id]) for it in rows if it.race_id in races and item_text(it)]
    if not pairs:
        return 0

    try:
        scores = score_pairs(
            [item_text(it) for it, _ in pairs],
            [race_descriptor(r) for _, r in pairs],
        )
    except Exception:
        logger.exception("Coverage relevance scoring failed — items stay unscored")
        return 0

    for (item, _), score in zip(pairs, scores):
        item.relevance = float(score)
        item.has_advocacy = bool(electioneering_language(item_text(item)))
    db.commit()
    logger.info("Scored %d coverage items for relevance", len(pairs))
    return len(pairs)
