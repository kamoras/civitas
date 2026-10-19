"""Action Center analysis — turns news articles into ranked civic action items.

Flow:
  1. Fetch RSS articles from low-bias sources (news_feeds.py)
  2. Embed article titles+summaries, filter for US policy relevance
  3. Fetch trending topics from social media (Google Trends, Reddit)
  4. Cluster related articles by cosine similarity
  5. Rank clusters by 0.40 civic actionability + 0.35 coverage breadth
     + 0.25 trending relevance (see _rank_clusters) so actionable,
     broadly-covered issues outrank editorial selection alone
  6. Use LLM to generate factual summary, key facts, and citizen actions
  7. Cross-reference with explore documents via embedding search
  8. Persist as ActionIssue rows for the current date
"""

import asyncio
import itertools
import json
import logging
import math
import pathlib
import re
import threading
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Iterator, NamedTuple
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, object_session

from app.contact import BOT_USER_AGENT
from app.config import settings
from app.config_definitions import RECENT_FEED_MAX_LIMIT, RECENT_FEED_POOL_MULTIPLIER
from app.database import SessionLocal
from app.election_phase import active_election
from app.state_names import STATE_NAMES
from app.models import (
    ActionIssue,
    ActionIssueStatus,
    Candidate,
    ExploreDocument,
    FeedArticle,
    Justice,
    LlmGenerationSample,
    MonitorStatus,
    MonitorUpdate,
    NationalMonitor,
    President,
    Race,
    Representative,
    Senator,
)
from app.pipeline.analyze import action_metrics
from app.committee_routing import route_committees
from app import roll_call_links
from app.person_names import (
    APOSTROPHES as _APOSTROPHES,
    RecordName as _RecordName,
    record_name as _record_name,
    strip_accents as _strip_accents,
)
from app.pipeline.analyze.bluesky_spotlight import post_daily_spotlight
from app.pipeline.analyze.early_signal import (
    CONFIRMATION_WINDOW_HOURS,
    ROLL_CALL_SOURCES,
    check_federal_register_signals,
    check_roll_call_signals,
    covering_issue,
    expire_stale_developing_issues,
    retire_covered_developing_issues,
    retire_stale_vote_drafts,
    reword_retired_drafts,
)
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
    log_intensifier_usage,
    proposal_stated_as_fact,
)
from app.pipeline.analyze import claims as claim_layer
from app import policy_relevance
from app.pipeline.analyze import story_actor
from app.pipeline.analyze.post_composer import CLAIM_TASK, compose, refit_actor_threshold
from app.pipeline.analyze.ollama_client import call_llm, extract_json
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.pipeline import lease
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress import congress_gov_url_for_bill_id
from app.pipeline.fetch.fr_rulemaking import RULE_DOC_TYPES, fetch_fr_metadata
from app.http_client import make_async_client
from app.pipeline.fetch.news_digest import (
    _POSSESSIVE_SUFFIX_RE,
    _entity_items,
    _issue_signature,
    _split_body_items,
    digest_reason,
)
from app.pipeline.fetch.news_feeds import (
    MAX_ARTICLE_AGE_HOURS,
    NewsArticle,
    fetch_news_articles,
)
from app.pipeline.fetch.trending import TrendingTopic, fetch_trending_topics
from app.fact_diff import stored_new_facts
from app.pipeline.vector_store import (
    get_embedding_model,
    get_similarity_model,
    search_explore_documents,
)
from app.pipeline.analyze.lobbying_records import bill_mentions, names_bill
from app.services.bill_service import bill_title_lists, names_phrase, short_title_index
from app.services.explore_keys import keys_for_ids, public_key
from app.time_utils import comment_period_today, eastern_date, utcnow
from app.timeline_entries import without_repeat_leads

_US_EAST = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory refresh state — read by the admin API for live progress display
# ---------------------------------------------------------------------------
_refresh_state: dict = {
    "is_running": False,
    "stage": None,
    "stage_detail": None,
    "started_at": None,
    "last_completed_at": None,
    "last_issues_created": 0,
    "last_issues_retired": 0,
    "last_stories_generated": 0,
    "last_bsky_posted": 0,
    "last_elapsed": 0.0,
}
_refresh_state_lock = threading.Lock()


def get_action_refresh_state() -> dict:
    with _refresh_state_lock:
        return dict(_refresh_state)


def _set_refresh_state(**kwargs) -> None:
    with _refresh_state_lock:
        _refresh_state.update(kwargs)


_POLICY_PROTOTYPES = [
    # US government & law
    "Congressional legislation, bill, act, law, regulation, government policy",
    "Federal budget, government spending, appropriations, fiscal policy",
    "Supreme Court ruling, judicial decision, constitutional law",
    "Executive order, presidential action, White House policy",
    "Election, voting rights, campaign, democracy, ballot measure",
    "Healthcare policy, Medicare, Medicaid, insurance regulation",
    "Immigration law, border policy, visa, asylum, deportation",
    "Tax reform, tax policy, IRS, tax cuts, tax increases",
    "Military, defense spending, veterans, national security",
    "Climate policy, environmental regulation, energy policy, clean energy",
    "Education policy, student loans, public schools, higher education",
    "Civil rights, discrimination, equality, justice reform",
    "Trade policy, tariffs, sanctions, international agreements",
    "Gun legislation, firearms regulation, Second Amendment",
    "Abortion policy, reproductive rights, contraception, IVF access, court rulings on abortion",
    "Labor policy, minimum wage, unions, workers rights, employment",
    "Housing policy, rent, mortgage, homelessness, affordable housing",
    "Social Security, retirement, pension, entitlements",
    "Technology regulation, privacy law, antitrust, AI policy",
    # International civic & political (relevant to global audiences)
    "International politics, foreign government, elections, democratic movements",
    "Human rights, civil liberties, political protest, LGBTQ rights, freedoms",
    "War, armed conflict, diplomacy, foreign policy, international relations",
    "Extreme weather, climate disaster, heatwave, flooding, environmental crisis",
    "Global economy, inflation, recession, central bank, financial markets",
]

# Gates TRENDING TOPICS only (_compute_trending_boost), since 2026-10-10.
# It used to gate feed articles too, and there it was measured against 602
# hand-labelled feed articles (2026-10-10, label = "a U.S. policy,
# legislation, government or civic story a reader of this site would
# want"): precision 0.62 [0.56, 0.67], recall 0.80 [0.75, 0.85] (95%
# bootstrap CIs). The old comment's "civic 0.354-0.583, non-civic
# 0.027-0.053" was a handful of hand-picked headlines; on real feeds the
# two score distributions overlap (median 0.27 relevant vs 0.17 not,
# interquartile 0.21-0.34 vs 0.11-0.25), so pure disaster coverage (the
# "Extreme weather" prototype), foreign politics, crime and markets stories
# passed while campaign coverage fell under the bar. No threshold fixes
# that: precision passes 0.70 only once recall is below 0.46. Articles now
# go through the kNN vote below. Trending topics are short search phrases,
# a distribution the labelled examples don't cover, so this pair stays
# there, unmeasured. See docs/research/action-center-relevance.md.
POLICY_RELEVANCE_THRESHOLD = 0.20

# The article relevance gate lives in policy_relevance.py: a kNN vote over
# feed articles labelled by their publishers' own filing, refit daily, never
# by hand (see its docstring).
#
# The relevance seed's categories (app/data/policy_relevance_examples.json)
# still label the principal-actor class vote (story_actor, _principal_actor).
_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / "data"
_STORY_CLASS_EXAMPLES_PATH = _DATA_DIR / "policy_relevance_examples.json"
_relevance_examples_cache: list[dict] | None = None
_relevance_reference_cache: dict[int, np.ndarray] = {}


def relevance_examples() -> list[dict]:
    """The seed's rows, for story_actor's class vote (their `category`)."""
    global _relevance_examples_cache
    if _relevance_examples_cache is None:
        _relevance_examples_cache = json.loads(_STORY_CLASS_EXAMPLES_PATH.read_text())["examples"]
    return _relevance_examples_cache


def _relevance_reference() -> np.ndarray:
    """The seed rows' embeddings, once per similarity model."""
    key = id(get_similarity_model())
    if key not in _relevance_reference_cache:
        _relevance_reference_cache[key] = _embed_texts_sim([e["text"] for e in relevance_examples()])
    return _relevance_reference_cache[key]


# Two similarity cuts the Action Center decides with, refit once a UTC day
# from its own stored data (refit_cuts, stored in api_cache), never from
# hand labels:
#
#   - clustering (_cluster_articles): the complete-linkage cut on day-
#     centred retrieval-model cosine of headline and description start
#     (cluster_text);
#   - restatement (claims.distinct_claims): the similarity-model cosine at
#     which a second claim restates an earlier one.
#
# Each is fitted the same way: the lowest cut (0.01 grid) at which no pair
# the data itself says is NOT one story (or one fact) would merge. Those are
# pairs that share a distinctive entity (_shares_distinctive_entity, the
# monitors' test) but whose stated occasions differ: for articles, the days
# their own text dates the event to (stated_days: a weekday read against
# the publication date, or a month and day) at least STATED_DAY_GAP apart;
# for claims, lines of two issues published within a day of each other as
# separate stories, leaving out any pair that quotes one sentence. A wrong
# merge publishes a chimera, and a wrong restatement call drops a real
# fact, so both cuts are precision-first, and both carry CUT_MARGIN. Where a fit has fewer than
# MIN_HARD_NEGATIVES such pairs it keeps the stored cut, or the bundled one
# (app/data/cluster_pair_calibration.json, claim_restatement_calibration.json,
# written by scripts/calibrate_action_cuts.py with the same fit).
#
# Clustering also refuses outright to join two articles whose stated days
# differ by STATED_DAY_GAP or more (_cluster_articles), whatever their
# cosine: the same actor two days apart is two occasions.
#
# Until 2026-10-16 both cuts were fitted to hand-judged pairs (564 article
# pairs, 204 claim pairs, one labeller): docs/research/action-center-relevance.md
# has the comparison.
STATED_DAY_GAP = 2
MIN_HARD_NEGATIVES = 30
# How far back each fit reads: about four weeks of national-feed articles
# (feed_articles), and three weeks of published issues.
CLUSTER_REFIT_DAYS = 28
RESTATEMENT_REFIT_DAYS = 21
# What clustering embeds after the headline. Chosen 2026-10-15 against the
# description lengths 200, 300 and 500, as the shortest that lets the
# description's first sentence say who did what.
CLUSTER_DESCRIPTION_CHARS = 200
# A margin above both fitted cuts, as a decision. The pairs each fit reads
# are the side of "another story" or "another fact" the data can name: two
# articles about one actor dated days apart, two issues published as
# separate stories. What no stated fact labels sits closer: the same actor's
# different acts on the same day (two polls of one race, a statement and
# the letter that followed it), and two facts of one event. On the
# 2026-09-28..10-15 articles the clustering fit gives 0.60, and 0.60 joins
# pairs of exactly that kind; on the issues since 2026-09-24 the
# restatement fit gives 0.69, and its top pairs are already the same fact
# published twice. 0.10 puts the cuts at 0.70 and 0.79, where the
# hand-judged pairs of 2026-10-15 put clustering (0.71) and a little above
# where they put restatement (0.75): a choice, made so the change of labels
# loosens neither, reported beside the fit in every refit and never fitted.
CUT_MARGIN = 0.10
# Words two claim lines must share in a row to be one sentence quoted
# twice (left out of the restatement fit's negatives).
SAME_SENTENCE_RUN = 5

_CUTS_TIER = "action-cuts"
_CUTS_TTL_HOURS = 24 * 400
_CLUSTER_CALIBRATION_PATH = _DATA_DIR / "cluster_pair_calibration.json"
_cuts_cache: dict = {}

_WORD_RE = re.compile(r"[a-z0-9']+")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_MONTH_ABBREVIATIONS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.IGNORECASE)


def stated_days(text: str, published: date | None) -> frozenset[date]:
    """The days TEXT dates what it reports to: each weekday it names, read
    as the most recent such day on or before `published`, and each month
    and day it names, in `published`'s year. Empty without a publication
    date to read them against."""
    if published is None:
        return frozenset()
    days: set[date] = set()
    for m in _WEEKDAY_RE.finditer(text or ""):
        back = (published.weekday() - _WEEKDAYS.index(m.group(1).lower())) % 7
        days.add(published - timedelta(days=back))
    for m in _MONTH_DAY_RE.finditer(text or ""):
        try:
            days.add(date(published.year, _MONTH_ABBREVIATIONS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))))
        except ValueError:
            pass
    return frozenset(days)


def days_disagree(a: frozenset[date], b: frozenset[date]) -> bool:
    """Both state a day, and none is within STATED_DAY_GAP of the other's."""
    return bool(a and b) and min(abs((x - y).days) for x in a for y in b) >= STATED_DAY_GAP


def cluster_text(title: str, summary: str | None, chars: int = CLUSTER_DESCRIPTION_CHARS) -> str:
    """What clustering embeds for an article: its headline and the first
    `chars` characters of its description. Headlines alone can't tell a
    second outlet's account of an event from another event on the same
    theme; the description's first sentence names who did what."""
    return f"{title}. {(summary or '')[:chars]}"


def _zero_merge_cut(negative_scores: Sequence[float]) -> float:
    """The lowest 0.01 cut no negative reaches."""
    return round(math.floor(max(negative_scores) * 100 + 1) / 100, 2)


def fit_cluster_cut(articles: Sequence[tuple[str, str, datetime]]) -> dict | None:
    """The clustering cut for (title, description, published) articles
    (module comment above STATED_DAY_GAP): each pair published within one
    run's window scored on the run's day-centred embeddings, the fitted cut
    the lowest no stated-day-disagreeing pair with a shared distinctive
    entity reaches, plus CUT_MARGIN."""
    if len(articles) < 2:
        return None
    embeddings = _embed_texts([cluster_text(t, d) for t, d, _ in articles])
    when = [p for _, _, p in articles]
    days = [stated_days(f"{t} {d}", p.date()) for t, d, p in articles]
    signatures = [_issue_signature(t, [d]) for t, d, _ in articles]
    df: Counter = Counter()
    for s in signatures:
        df.update(s)
    window = timedelta(hours=MAX_ARTICLE_AGE_HOURS)
    order = sorted(range(len(articles)), key=lambda n: when[n])
    negatives = []
    for a, i in enumerate(order):
        for j in order[a + 1:]:
            if when[j] - when[i] > window:
                break
            if not days_disagree(days[i], days[j]):
                continue
            if not _shares_distinctive_entity(signatures[i], signatures[j], df, len(articles)):
                continue
            # Centred on the pool one run holding both would have seen.
            pool = [n for n in order if when[j] - window < when[n] <= when[j]]
            centred = _center_titles(embeddings[[i, j]], embeddings[pool].mean(axis=0))
            negatives.append(float(centred[0] @ centred[1]))
    if len(negatives) < MIN_HARD_NEGATIVES:
        return None
    fitted = _zero_merge_cut(negatives)
    return {"threshold": round(min(fitted + CUT_MARGIN, 0.99), 2), "fitted": fitted,
            "margin": CUT_MARGIN, "negatives": len(negatives), "articles": len(articles)}


def _shared_run(a: str, b: str) -> int:
    """The longest run of words two lines share, verbatim."""
    x, y = _WORD_RE.findall(a.lower()), _WORD_RE.findall(b.lower())
    return SequenceMatcher(None, x, y, autojunk=False).find_longest_match(0, len(x), 0, len(y)).size


def fit_restatement_cut(issues: Sequence[tuple[int, str, list[str]]]) -> dict | None:
    """The restatement cut for (issue id, date, lines) issues: the lowest
    cosine no pair of lines from two different issues published within a
    day of each other that share a distinctive entity reaches, plus
    CUT_MARGIN. The pipeline published them as two stories, so they are two
    facts; a pair sharing SAME_SENTENCE_RUN words verbatim is left out (one
    sentence quoted twice is one fact however the issues split)."""
    lines = [(iid, date.fromisoformat(d[:10]), line)
             for iid, d, ls in issues for line in ls if line and line.strip()]
    if len(lines) < 2:
        return None
    signatures = [_issue_signature(line, []) for _, _, line in lines]
    df: Counter = Counter()
    for s in signatures:
        df.update(s)
    pairs = [(i, j) for i, j in itertools.combinations(range(len(lines)), 2)
             if lines[i][0] != lines[j][0] and abs((lines[i][1] - lines[j][1]).days) <= 1
             and _shares_distinctive_entity(signatures[i], signatures[j], df, len(lines))
             and _shared_run(lines[i][2], lines[j][2]) < SAME_SENTENCE_RUN]
    if len(pairs) < MIN_HARD_NEGATIVES:
        return None
    vectors = _embed_texts_sim([line for _, _, line in lines])
    fitted = _zero_merge_cut([float(vectors[i] @ vectors[j]) for i, j in pairs])
    return {"threshold": round(min(fitted + CUT_MARGIN, 0.99), 2), "fitted": fitted, "margin": CUT_MARGIN,
            "negatives": len(pairs), "lines": len(lines)}


def refit_cuts(db: Session) -> None:
    """Refit both cuts on the first refresh of each UTC day, inside a
    savepoint of the refresh's session (it commits). A fit with too few
    pairs leaves the stored cut in place. The new cuts are used in this
    process at once."""
    today = utcnow().date().isoformat()
    if _cuts_cache.get("refit_on") == today:
        return
    _cuts_cache["refit_on"] = today
    since = utcnow() - timedelta(days=CLUSTER_REFIT_DAYS)
    articles = [(r.title, r.description, r.published or r.seen_at) for r in
                db.query(FeedArticle).filter(FeedArticle.kept.is_(True), FeedArticle.seen_at >= since)]
    issue_since = (utcnow() - timedelta(days=RESTATEMENT_REFIT_DAYS)).date().isoformat()
    issues = []
    for row in db.query(ActionIssue).filter(ActionIssue.source_type.is_(None), ActionIssue.date >= issue_since):
        try:
            facts = json.loads(row.facts or "[]")
        except ValueError:
            facts = []
        issues.append((row.id, row.date, [row.summary or "", *facts]))
    fits = {"cluster": fit_cluster_cut(articles), "restatement": fit_restatement_cut(issues)}
    for key, fitted in fits.items():
        if fitted is None:
            logger.info("Cut refit (%s): too few pairs the data labels; the stored cut stands", key)
            continue
        fitted["date"] = today
        try:
            with db.begin_nested():
                api_cache_set(db, _CUTS_TIER, key, fitted, normal_ttl_hours=_CUTS_TTL_HOURS, commit=False)
        except Exception:
            logger.warning("Cut refit (%s) not stored; used in this process only", key, exc_info=True)
        _cuts_cache[key] = (fitted, utcnow())
        logger.info("Cut refit (%s): %s", key, fitted)
    if fits["restatement"] is not None:
        claim_layer.set_restatement_calibration(fits["restatement"])


def stored_cut(key: str, bundled: pathlib.Path) -> dict:
    """The day's refit of `key` (re-read hourly), else the bundled one."""
    cached = _cuts_cache.get(key)
    if cached is not None and utcnow() - cached[1] < timedelta(hours=1):
        return cached[0]
    stored = None
    try:
        with SessionLocal() as session:
            stored = api_cache_get(session, _CUTS_TIER, key, max_age_hours=_CUTS_TTL_HOURS)
    except Exception:
        logger.warning("Stored %s cut unreadable; using the bundled one", key, exc_info=True)
    value = stored or json.loads(bundled.read_text())
    _cuts_cache[key] = (value, utcnow())
    return value


def cluster_calibration() -> dict:
    """The clustering cut in use ({"threshold": ...}): stored_cut."""
    return stored_cut("cluster", _CLUSTER_CALIBRATION_PATH)


# How many candidate clusters get an LLM generation attempt per hourly run.
# 2026-08: lowered from 4 to 2 as a deliberate capacity choice, not a
# recalibrated quality threshold — there's a real difference between the
# two. _rank_clusters' combined score is normalized against that day's OWN
# maximum, so "top 4" always fills all 4 slots even on a quiet day where
# only 1-2 clusters are genuinely newsworthy; the 3rd/4th ranked cluster
# only has to beat its equally-weak peers, not clear any absolute bar. No
# real per-signal absolute floor exists to calibrate against yet (that
# would need the same measure-first approach as DEDUP_THRESHOLD/
# SOURCE_SIM_FLOOR above), but a smaller quota directly does what was
# asked for regardless: fewer issues attempted per run means only the
# strongest candidates by the existing weighted ranking get a shot, at the
# direct cost of quantity. Revisit the number itself once real per-cluster
# score distributions are being logged (see _rank_clusters).
MAX_ISSUES = 2

# How many ranked clusters a run may TRY to fill MAX_ISSUES slots. Separate
# from MAX_ISSUES since extraction (2026-09-24): a cluster now publishes
# only if the model locates at least two verbatim, attributed claims and
# the issue anchors to a bill or a named official, so a top cluster can
# fail — about one in four yields no claim at all (see the loop below).
# Trying only the top two meant a run where both failed published nothing,
# and the next hour ranked the same two clusters first and failed them
# again: the Action Center went from five issues on 2026-09-23 to none on
# 2026-09-26. Still at most MAX_ISSUES publish, and still in rank order,
# so the strongest cluster that passes every gate wins — only a failure
# now falls through to the next candidate instead of ending the run.
# Three candidates per slot; a cluster that is tried costs one extraction
# pass, and a run stops trying the moment both slots are filled.
CANDIDATE_POOL = MAX_ISSUES * 3

# Fact rule (6) guards against cross-topic contamination: facts pulled
# from an unrelated article that survived cluster-coherence filtering. A
# mechanical per-fact check was evaluated and rejected on measurement —
# fact-vs-title cosine on the production model doesn't separate
# contaminants from legitimate facts (contaminants scored 0.72-0.76,
# legitimate facts 0.73-0.97, overlapping ranges) — so this class is
# addressed at the prompt only, and disclosed as not mechanically enforced.
ACTION_CENTER_PROMPT_VERSION = "action-v21"

# Minimum age (from created_at, i.e. first_surfaced) before an issue is
# eligible for retirement just for not re-matching this run — see the
# retirement pass in _run_refresh for the full reasoning.
_RETIREMENT_GRACE_HOURS = 24

# Where title cosine alone decides "same story": hand-measured 0.92 on 120
# issue pairs (2026-07), bundled with its provenance in
# app/data/action_thresholds.json. Not fitted: its only automatic label was
# the signature test, which on 2026-10-08 called 65 issue pairs the same
# story when 16 were.
_NEAR_IDENTICAL_TITLE_THRESHOLD = json.loads((_DATA_DIR / "action_thresholds.json").read_text())["values"]["near_identical"]


def _full_story_should_invalidate(
    old_title: str, old_facts: str, new_title: str, new_facts: str,
) -> bool:
    """True if an issue's title/facts changed enough that its cached
    full_story (if any) now describes the wrong event and must be
    regenerated rather than left stale.

    A match can still land on a substantively different story sharing a
    category — e.g. two different
    senators' health events both matching "ailing senior senator". full_story
    is only ever generated once per issue (Stage 4 filters on
    ``full_story IS NULL``), so if the row's content is silently replaced
    without also clearing full_story, the page keeps showing old text about
    a different event indefinitely.
    """
    return old_title != new_title or old_facts != new_facts


# Words that mark a change in a story's STATE rather than the arrival of a new
# participant. The signature check below sees only capitalized entities and
# digits, so the developments that carry a story forward without introducing a
# new name or number were invisible to it: "the president vetoed the measure",
# "a federal judge blocked the order", "the override attempt failed" each add
# no signature token at all (and their one capitalized word — President, House,
# Senate — is stripped as generic civic vocabulary, correctly, since that list
# is calibrated for telling stories APART, where those words are pure noise).
# Restricted to outcome/state-change verbs; weak reporting verbs ("announced",
# "said", "reported") are deliberately excluded because every recap has them.
# Checked differentially — a reword that says "passed" both times adds nothing
# — and the near-duplicate text gate in bluesky_poster.py is the backstop if a
# synonym swap ("approved" for "passed") slips one through here.
#
# This list needs to stay broad: a new lawsuit being filed or a court
# order being lifted is genuinely new information even when it introduces
# no new named entity or number, so an incomplete marker set silently
# suppresses updates a reader should see. Extend it whenever a real
# outcome verb turns out to be missing.
_DEVELOPMENT_MARKERS = frozenset({
    # Executive and administrative action
    "veto", "vetoed", "vetoes", "override", "overrode", "overridden",
    "signed", "enacted", "repealed", "revoked", "reinstated", "suspended",
    "restored", "halted", "paused", "resumed", "extended", "expired",
    # Legislative outcome
    "passed", "failed", "rejected", "defeated", "approved", "confirmed",
    "advanced", "tabled", "postponed", "delayed", "withdrew", "withdrawn",
    # Judicial
    "blocked", "enjoined", "upheld", "overturned", "reversed", "struck",
    "dismissed", "ruled", "indicted", "charged", "convicted", "acquitted",
    "sentenced", "settled", "appealed", "subpoenaed", "lifted", "filed", "sued",
    # Personnel and status
    "resigned", "fired", "ousted", "nominated", "sworn", "replaced",
    # Story resolution
    "dropped", "ended", "resolved", "recalled", "expanded", "escalated",
    "arrested", "released", "admitted", "denied", "declined", "agreed",
})

_MARKER_WORD_RE = re.compile(r"[a-z]+")


def _development_markers(facts: list[str]) -> set[str]:
    """State-change words present in ``facts`` (see _DEVELOPMENT_MARKERS)."""
    return set(_MARKER_WORD_RE.findall(" ".join(facts).lower())) & _DEVELOPMENT_MARKERS


def _bsky_repost_has_new_information(old_facts_json: str, new_facts: list[str]) -> bool:
    """True if the new facts introduce at least one specific named entity or
    figure — or one story development — the old ones didn't have.

    A matched issue can requalify for a Bluesky repost purely because its
    primary_article_date advanced, but ongoing/recap coverage of the same
    story often just rewords the same names and numbers under a fresher
    timestamp — reposting on that basis alone says nothing new. Reuses the
    same entity/number signature _issue_signature already computes for
    story-identity matching, but over FACTS ONLY — not title, which is
    regenerated fresh by the LLM every run and can shift wording for the
    exact same underlying facts. A repost is only warranted when the new
    facts add something the old ones lacked, not just a later publish date
    or a reworded headline.

    "Something" is a new signature token (name, figure) OR a new development
    marker — the signature alone answers "is a new PARTICIPANT involved?"
    when the real question is "did anything HAPPEN?", so state changes that
    name nobody new (a veto, a court blocking an order, a failed override)
    would otherwise read as pure rewords. See _DEVELOPMENT_MARKERS.

    A two-word capitalized phrase in the new text where one word is already
    known and the other is new (e.g. a country name spelled out more fully
    than before) is filtered out before deciding — _issue_signature
    tokenizes word-by-word, so an expanded reference to an already-known
    entity would otherwise register as a brand-new participant.
    """
    old_facts = json.loads(old_facts_json or "[]")
    old_sig = _issue_signature("", old_facts)
    new_tokens = _issue_signature("", new_facts) - old_sig
    if new_tokens:
        for m in re.finditer(r"\b([A-Z][a-zA-Z'\-]{2,})\s+([A-Z][a-zA-Z'\-]{2,})\b", " ".join(new_facts)):
            w1, w2 = m.group(1).lower(), m.group(2).lower()
            if w1 in new_tokens and w2 in old_sig:
                new_tokens.discard(w1)
            if w2 in new_tokens and w1 in old_sig:
                new_tokens.discard(w2)
    if new_tokens:
        return True
    return bool(_development_markers(new_facts) - _development_markers(old_facts))


def _retire_untouched_issues(
    all_current: list[ActionIssue], matched_issue_ids: set[int], grace_cutoff: datetime,
) -> tuple[int, int]:
    """Flip is_current=False on rows this run didn't match, EXCEPT ones
    still younger than grace_cutoff — returns (n_retired, n_graced).

    Extracted from _run_refresh for direct testability, same precedent as
    _apply_matched_issue_update/_retry_until_grounded — this is the exact
    mechanic _RETIREMENT_GRACE_HOURS controls, and a change to that
    constant deserves a real test pinning what it actually does rather
    than only a comment asserting it.

    Records issues_retired/issues_graced on action_metrics like every
    other pipeline gate does, so a future change to _RETIREMENT_GRACE_HOURS
    has real history to validate against instead of only free-text log
    lines.
    """

    n_retired = n_graced = 0
    for row in all_current:
        if row.id not in matched_issue_ids:
            if row.created_at and row.created_at > grace_cutoff:
                # Too young to retire — give it another run to prove itself.
                n_graced += 1
            else:
                row.is_current = False
                n_retired += 1
    action_metrics.increment("issues_retired", n_retired)
    action_metrics.increment("issues_graced", n_graced)
    return n_retired, n_graced


def _record_generation_sample(
    db: "Session",
    task: str,
    rank: int,
    attempt: int,
    input_text: str,
    output: dict,
    passed: bool,
    violations: list[str] | None = None,
    once_per_day: bool = False,
) -> None:
    """Persist one LLM generation attempt as future fine-tuning data.

    ``once_per_day`` skips a row whose task and input were already recorded
    today (UTC): call_llm caches a day's answer per input, so a later hourly
    run asking the same thing gets the cached answer back, and recording it
    again would count one generation many times.

    2026-08 research finding: this pipeline's mechanical grounding/role
    checks already work as a free, automatic labeling function — every
    (input, output, pass/fail, why) triple they produce was previously
    computed fresh on every run and thrown away, only ever
    `logger.warning`'d. A rejected-then-corrected pair is the single most
    valuable training signal this pipeline generates; a first-try pass is
    a positive example. Best-effort: a failure here must never break
    issue generation itself, since this table only feeds a future
    training run, not anything the site serves today.

    Written and committed on its own short-lived session, never flushed
    into the caller's. The caller is the refresh loop, whose next commit
    comes only after every remaining cluster's embedding and LLM work: a
    flush there opened SQLite's single write transaction and held it for
    ~15 minutes a run (2026-09-27), so every other writer — the refresh
    lock's own heartbeat, the election coverage refresh — waited out its
    30s busy timeout and failed. The heartbeat failing is what let the
    lease go stale while its holder was still running.
    """
    sample_db = Session(bind=db.get_bind())
    try:
        if once_per_day and sample_db.query(LlmGenerationSample.id).filter(
            LlmGenerationSample.task == task,
            LlmGenerationSample.input_text == input_text,
            LlmGenerationSample.created_at >= datetime.combine(utcnow().date(), datetime.min.time()),
        ).first():
            return
        sample_db.add(LlmGenerationSample(
            task=task,
            rank=rank,
            attempt=attempt,
            input_text=input_text,
            output_json=json.dumps(output),
            passed=passed,
            violations=json.dumps(violations) if violations else None,
        ))
        sample_db.commit()
    except Exception:
        logger.exception("Failed to record LLM generation sample (task=%s, rank=%d)", task, rank)
        sample_db.rollback()
    finally:
        sample_db.close()




# Attributes copied from a fresh cluster pass onto a matched existing
# ActionIssue row (_apply_matched_issue_update) — a fixed set regardless of
# which cluster is being processed, so this lives at module scope rather
# than being rebuilt on every loop iteration in _run_refresh.
_ISSUE_UPDATE_ATTRS = (
    "title", "summary", "facts", "actions", "source_urls",
    "source_names", "policy_areas", "related_bill_ids",
    "related_explore_ids", "related_explore_keys", "related_senators", "related_officials",
    "primary_article_date", "image_url", "image_alt", "image_credit",
)


def _apply_matched_issue_update(
    match: ActionIssue, new_values: dict, rank: int, today: str,
    primary_article_date: str, facts: list[str], title: str,
) -> bool:
    """Update a matched ActionIssue row with this run's fresh cluster data,
    and decide whether to allow a Bluesky repost. Returns True if a repost
    was allowed.

    Extracted from _run_refresh (2026-07) for direct testability, matching
    the same reasoning that already extracted _full_story_should_invalidate/
    _issue_signature/_bsky_repost_has_new_information out of this same
    loop: _run_refresh as a whole fetches real articles, runs an embedding
    model, and calls an LLM, so it can't reasonably be driven end-to-end in
    a unit test.
    """

    has_new_articles = primary_article_date > (match.primary_article_date or "1970-01-01")
    # A newer article date alone doesn't mean there's something NEW to
    # report — see _bsky_repost_has_new_information's docstring.
    #
    # Compare against the facts as of the LAST POST, not last run.
    # primary_article_date is day-granular, so has_new_articles is an edge
    # that fires on exactly one of the ~24 hourly runs per day, while the
    # `facts` column below is rewritten on all 24. Baselining on match.facts
    # meant a development the LLM surfaced on any of the other 23 runs was
    # absorbed into the baseline silently, and then read as "already known"
    # on the one run that could actually have reposted it — the update was
    # lost for good. bsky_posted_facts only moves when we post (or when the
    # poster suppresses a near-duplicate, which is also a decision that
    # these facts had nothing new to say). NULL for rows last posted before
    # that column existed; those fall back to the old behavior once.
    repost_baseline = match.bsky_posted_facts or match.facts
    suppressed_no_new_info = (
        has_new_articles and not _bsky_repost_has_new_information(repost_baseline, facts)
    )
    if suppressed_no_new_info:
        has_new_articles = False
    invalidate_story = _full_story_should_invalidate(
        match.title, match.facts, new_values["title"], new_values["facts"],
    )

    # Snapshot the OUTGOING facts as the new "previous" baseline, but only
    # when they're actually about to change — not on every hourly touch,
    # or the "new" marker would light up on facts that are word-for-word
    # what a reader already saw. Set comparison, not string equality: the
    # LLM does not promise a stable order between runs, and this cares
    # about content, not sequence (app/fact_diff.py's new_facts_since
    # feels the same way about the read side).
    facts_changed = set(json.loads(match.facts or "[]")) != set(json.loads(new_values["facts"] or "[]"))
    if facts_changed:
        match.previous_facts = match.facts

    match.rank = rank
    match.date = today
    match.is_current = True
    for attr in _ISSUE_UPDATE_ATTRS:
        setattr(match, attr, new_values[attr])
    if facts_changed or match.new_facts is None:
        match.new_facts = stored_new_facts(match.facts, match.previous_facts)
    if invalidate_story:
        match.full_story = None

    if has_new_articles:
        # New articles arrived — allow the Bluesky poster to post an update.
        match.bsky_posted_at = None
        match.bsky_posted_rank = None
        action_metrics.increment("bsky_reposts_allowed")
        logger.info(
            "Rank %d '%s': new articles (article_date=%s) — updating and allowing repost",
            rank, title[:60], primary_article_date,
        )
    elif suppressed_no_new_info:
        # Distinct from the no-newer-article case below: both used to log the
        # same "no new articles" line, so the share of reposts this gate was
        # actually suppressing was unmeasurable in production.
        action_metrics.increment("bsky_reposts_suppressed_no_new_information")
        logger.info(
            "Rank %d '%s': newer article (article_date=%s) but no new "
            "information vs last post — rank updated, no repost",
            rank, title[:60], primary_article_date,
        )
    else:
        logger.info(
            "Rank %d '%s': no new articles (article_date=%s) — rank updated, no repost",
            rank, title[:60], primary_article_date,
        )
    return has_new_articles


def _promote_developing_issue(
    match: ActionIssue, new_values: dict, rank: int, today: str,
    primary_article_date: str, facts: list, title: str,
) -> bool:
    """Promote a DEVELOPING (primary-source-only) issue to CONFIRMED once
    a fresh news cluster matches it — a full content swap (the hedged
    primary-source draft is superseded by real reporting), not a merge.

    Delegates rank/date/is_current/previous_facts-snapshot/full-story-
    invalidation/Bluesky-repost-eligibility bookkeeping to the existing
    _apply_matched_issue_update rather than re-deriving any of it — one
    function owns that logic, not two.
    """
    lead_hours = (
        (utcnow() - match.created_at).total_seconds() / 3600
        if match.created_at else 0.0
    )
    match.status = ActionIssueStatus.CONFIRMED
    match.confirmed_at = utcnow()
    match.confirmation_deadline = None
    source_type = match.source_type or "unknown"
    action_metrics.increment("early_signal_confirmed")
    action_metrics.increment(f"early_signal_confirmed_{source_type}")
    # Bucketed as a fraction of the confirmation window used, not raw
    # hours — decile_bucket expects a 0-1 ratio, and "how much of the
    # window elapsed before confirmation" is itself the more useful
    # success metric (did press catch up quickly or right at the wire).
    action_metrics.increment_bucket(
        "early_signal_lead_time_fraction",
        min(lead_hours / CONFIRMATION_WINDOW_HOURS, 1.0),
    )
    logger.info(
        "Promoted developing issue %d to confirmed after %.1fh: '%s'",
        match.id, lead_hours, title[:60],
    )
    return _apply_matched_issue_update(
        match, new_values, rank, today, primary_article_date, facts, title,
    )


# Candidate floor for topic matching by title similarity — a compute
# guard, not a decider (_same_story decides; see _run_refresh's matching
# loop). Measured under the similarity model on real production titles:
# same-story pairs scored as low as +0.134 while a different-story
# same-vocabulary pair outscored them at +0.552. The floor only excludes
# clearly-unrelated candidates from the same-story comparison.
_TOPIC_MATCH_CANDIDATE_FLOOR = 0.10


def _is_exact_content_duplicate(
    title: str, facts: list, cand_title: str, cand_facts: list,
) -> bool:
    """Byte-identical title+facts always means the same issue, whatever
    the title similarity: the same source article reprocessed later must
    match its existing row, never create a duplicate."""
    return title == cand_title and facts == cand_facts


def _same_story(sim: float, title: str, facts: list, cand_title: str, cand_facts: list) -> bool:
    """The "is this the same real-world story" decision from the text, shared
    by _find_matching_issue (write time) and the feed's duplicate pass
    (_near_identical_clusters) so the two can't drift apart: the same
    content, or a title near-identical to the other's. A shared source
    article, the strongest evidence, is checked by each caller first.

    Shared names and numbers (_issue_signature overlap) decided it until
    2026-10-08, when 65 pairs of issues two days apart or less that the
    test called the same story were read by their titles: 16 were. Two
    stories about one senator, or two about Iran, share two names. Above
    any rarity weighting of the shared names the best was 3 of 6, and rows
    updated by a match had swapped to another story in about 10 of 45.
    Merging two stories hides one, and a write-time merge replaces a row's
    story under its earlier posts; a missed match shows a second card.
    """
    return _is_exact_content_duplicate(title, facts, cand_title, cand_facts) or (
        sim >= _NEAR_IDENTICAL_TITLE_THRESHOLD
    )


# A shared source article makes two rows the same story only when their
# titles also agree this far (similarity model). Measured 2026-10-08 on all
# 94 pairs of stored rows within REMATCH_WINDOW_DAYS that share a source,
# each read by its titles: every pair at 0.70 or above was the same story,
# the highest different-story pair scored 0.615 (both citing one roundup
# article), and below that the two kinds mix. A shared source alone had
# merged 45 different-story pairs (an AI debate with an interview on AI
# regulation, a chip maker's lobbying with an NIH grants plan). Set in the
# gap: no false merges on the 94, 11 same-story pairs left as two cards,
# since merging two stories hides one and a missed match only shows a
# second card.
SHARED_SOURCE_TITLE_SIM = 0.62


def _shared_source_title_sim(sim: float, title_a: str, title_b: str) -> float:
    """The title agreement SHARED_SOURCE_TITLE_SIM is read against: the
    titles' similarity `sim`, or, when either is a live blog's headline
    listing several stories (_story_titles), the best agreement of any one
    of its stories with the other title. Issues 805/806 (2026-10-16): a
    live blog's headline over a press secretary's exit and an unrelated
    campaign rally shared its source with the report of the exit at
    0.557, the rally pulling the whole headline away; the exit alone
    agreed at 0.672. The same shape with invented names ("Live updates:
    Press secretary Morrow exits; Varga rallies for Brandt at Dunmore
    county fair" against "Press secretary Dana Morrow leaving the job
    after Varga casts doubt"): 0.606 whole, 0.679 for the first story
    (test_a_live_blog_title_agrees_on_its_shared_story)."""
    stories_a, stories_b = _story_titles(title_a), _story_titles(title_b)
    if len(stories_a) == len(stories_b) == 1:
        return sim
    return float((_embed_texts_sim(stories_a) @ _embed_texts_sim(stories_b).T).max())


# How far back (days, by issue date) a new story is matched against
# existing rows, and the widest gap between two rows the feed's duplicate
# pass (_near_identical_clusters) compares: one window, so the two passes
# can't disagree about which rows are candidates.
REMATCH_WINDOW_DAYS = 2


def _issue_day(issue) -> "date | None":
    try:
        return date.fromisoformat((issue.date or "")[:10])
    except ValueError:
        return None


def _near_identical_clusters(issues: list["ActionIssue"]) -> dict[int, int]:
    """Index -> index of its cluster's representative, over clusters of
    near-identical titles (same
    _NEAR_IDENTICAL_TITLE_THRESHOLD/_is_exact_content_duplicate logic
    _find_matching_issue uses), keeping whichever cluster member has the
    latest created_at. Relative order of the kept issues is preserved
    from the input.

    Retiring a duplicate only removes it from is_current — the homepage's
    recent-issues endpoint deliberately does NOT filter on is_current (so a
    retired-for-real row doesn't vanish from the record), so a row retired
    specifically for BEING a duplicate would otherwise resurface there
    anyway. Recorded in its own column (mark_recent_duplicates), not a
    second is_current flip, because "duplicate of something else" and "no
    longer current" are different facts about a row that shouldn't be
    conflated into one flag.

    Calls the same _same_story predicate _find_matching_issue calls,
    covering the same shared-source-URL and signature-overlap signals, not
    just near-identical title/exact content — rather than re-implementing
    the decision, so the two passes can't drift apart.

    Clustering is complete-linkage (a cluster only forms when every pair
    inside it matches directly), not single-linkage union-find. Union-find
    would let one strong match (A-B, e.g. shared URL) and one unrelated
    weak match (B-C, e.g. a thin signature overlap) transitively merge A
    and C even though A-C never matched anything — silently dropping A (or
    C) from the feed as a "duplicate" of a story it was never actually
    shown to resemble. _find_matching_issue doesn't have this risk (it
    matches one new issue against existing rows one at a time, never
    clusters several issues against each other), so only this pass needs it.
    """
    embs = np.array(_embed_texts_sim([i.title or "" for i in issues]))
    sims = embs @ embs.T

    # Precomputed once per issue (not per pair, O(n) not O(n^2)).
    facts_list: list[list] = []
    urls_list: list[set] = []
    for issue in issues:
        try:
            facts = json.loads(issue.facts or "[]")
        except (ValueError, TypeError):
            facts = []
        try:
            urls = set(json.loads(issue.source_urls or "[]"))
        except (ValueError, TypeError):
            urls = set()
        facts_list.append(facts)
        urls_list.append(urls)

    n = len(issues)
    days = [_issue_day(issue) for issue in issues]
    same_story = [[False] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = issues[i], issues[j]
            # Only rows the write-time matcher could also have compared
            # (REMATCH_WINDOW_DAYS): further apart, the matcher already
            # kept them as separate developments, and on 2026-10-08 most
            # rows this pass had marked duplicates were 6 to 24 days from
            # the row they "duplicated" (one candidate's two unrelated
            # stories, Ukraine talks and a Russian vote).
            if days[i] is None or days[j] is None or abs((days[i] - days[j]).days) > REMATCH_WINDOW_DAYS:
                continue
            if a.source_type or b.source_type:
                # A vote draft or rule is its record, written from a fixed
                # template: two House votes matched on "Passed" and
                # "Representatives". Only the same content or source is the
                # same item; a draft a news story covers is decided by its
                # bill (covering_issue, mark_recent_duplicates).
                m = bool(urls_list[i] & urls_list[j]) or _is_exact_content_duplicate(
                    a.title or "", facts_list[i], b.title or "", facts_list[j],
                )
            else:
                m = (bool(urls_list[i] & urls_list[j]) and _shared_source_title_sim(
                    float(sims[i, j]), a.title or "", b.title or "",
                ) >= SHARED_SOURCE_TITLE_SIM) or _same_story(
                    float(sims[i, j]), a.title or "", facts_list[i], b.title or "", facts_list[j],
                )
            same_story[i][j] = same_story[j][i] = m

    # Complete-linkage: only merge two clusters when every issue in one
    # matches every issue in the other, so a single weak edge can never
    # bridge an unrelated cluster in (see docstring above). n is at most
    # the feed's pool (RECENT_FEED_MAX_LIMIT * RECENT_FEED_POOL_MULTIPLIER,
    # 90), and real news-cluster match graphs are sparse (most issues share
    # no signal with most others), so the polynomial worst case doesn't
    # bite in practice. Revisit if the pool grows substantially.
    clusters: list[list[int]] = [[i] for i in range(n)]
    merged = True
    while merged:
        merged = False
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                if all(same_story[x][y] for x in clusters[a] for y in clusters[b]):
                    clusters[a] = clusters[a] + clusters[b]
                    del clusters[b]
                    merged = True
                    break
            if merged:
                break

    representative: dict[int, int] = {}
    for members in clusters:
        # A row titled by a live blog's several-story headline never
        # represents its story while a row with a single story's headline
        # can (_multi_story_headline: 806 gives way to 805). Then (has a
        # real timestamp, the timestamp itself, id) — a plain
        # `created_at or id` fallback would compare a datetime against an
        # int the moment one cluster member lacks a timestamp and another
        # doesn't, which TypeErrors.
        best = max(
            members,
            key=lambda idx: (
                not _multi_story_headline(issues[idx].title or ""),
                issues[idx].created_at is not None, issues[idx].created_at, issues[idx].id,
            ),
        )
        for idx in members:
            representative[idx] = best
    return representative


def dedupe_near_identical_issues(issues: list["ActionIssue"]) -> list["ActionIssue"]:
    """One representative per cluster of near-identical issues
    (_near_identical_clusters), in input order."""
    if len(issues) < 2:
        return list(issues)
    representative = _near_identical_clusters(issues)
    return [issue for idx, issue in enumerate(issues) if representative[idx] == idx]


# Rows filled per refresh: the first refreshes after the column was added
# work through the older rows a batch at a time instead of stalling one run.
_NEW_FACTS_BACKFILL_BATCH = 200


def backfill_new_facts(db: "Session") -> int:
    """Fill ActionIssue.new_facts for rows written before the column
    existed, newest first, so the API can read it instead of running the
    embedding model per request. Rows with no previous facts get "[]"
    without touching the model."""
    rows = (
        db.query(ActionIssue)
        .filter(ActionIssue.new_facts.is_(None))
        .order_by(ActionIssue.date.desc(), ActionIssue.id.desc())
        .limit(_NEW_FACTS_BACKFILL_BATCH)
        .all()
    )
    for row in rows:
        row.new_facts = stored_new_facts(row.facts, row.previous_facts)
    return len(rows)


def mark_recent_duplicates(db: "Session") -> int:
    """Record, on each of the newest rows the homepage's feed can read,
    which row it duplicates (duplicate_of_id; None for a representative).

    The feed deliberately ignores is_current (a retired story stays in the
    record), so a row retired for BEING a duplicate would resurface there.
    It used to be deduped on every request, which ran the embedding model
    on a public GET inside the event loop — the read path must never load a
    model (AGENTS.md). The same clustering runs here, in the hourly
    refresh that has the model loaded, and the feed reads the result.
    Returns how many rows are marked duplicates."""
    pool = (
        db.query(ActionIssue)
        .order_by(ActionIssue.date.desc(), ActionIssue.rank.asc())
        .limit(RECENT_FEED_MAX_LIMIT * RECENT_FEED_POOL_MULTIPLIER)
        .all()
    )
    if not pool:
        return 0
    representative = _near_identical_clusters(pool) if len(pool) > 1 else {0: 0}
    marked = 0
    for idx, issue in enumerate(pool):
        rep = representative[idx]
        issue.duplicate_of_id = None if rep == idx else pool[rep].id
        # A current story duplicating another current one leaves the Action
        # Center too, as a draft a report covers does (early_signal): the
        # Today tab reads is_current, not this mark, so both cards stayed
        # up (805/806, 2026-10-16). Its page links the row it duplicates.
        if rep != idx and issue.is_current and pool[rep].is_current and not issue.source_type:
            issue.is_current = False
            action_metrics.increment("issues_retired_duplicate")
            logger.info("Retired issue %d: a duplicate of current issue %d", issue.id, pool[rep].id)
        # A vote draft whose bill a reported issue covers duplicates that
        # issue, though the titles differ too much to cluster ("Senate vote
        # on S. 4668" / "The Senate passes the Protect College Sports Act").
        # Decided here, from the pool, not stored by the retirement pass:
        # this loop resets every mark each run, and a covering issue that
        # has since left the Action Center still covers the vote.
        if issue.duplicate_of_id is None and issue.status == ActionIssueStatus.DEVELOPING \
                and issue.source_type in ROLL_CALL_SOURCES:
            covering = covering_issue(db, issue, pool)
            issue.duplicate_of_id = covering.id if covering else None
        marked += issue.duplicate_of_id is not None
    db.commit()
    return marked


_SYSTEM_PROMPT = """\
You are a nonpartisan civic information analyst. You present facts without \
opinion and help citizens engage with their government regardless of their \
political position. Never advocate for or against any policy. Present all \
sides neutrally. Each issue you analyze is a SEPARATE topic — never mix \
information from one issue into another. Report directly, in your own \
words — never attribute what happened to "reports," "coverage," or \
"sources" (write what happened, not that something was reported). Never \
state or imply that an action was warranted, justified, or reasonable, and \
never repeat an actor's stated rationale for an action as though it were \
established fact."""

# Locate one assertion; do not write anything.
#
# This replaces a 4,361-character instruction blob carrying 16 separate
# "never / do not" clauses, nearly every one a fossilised bug ("never
# write 'reports say'", "double-check the direction of every action",
# "never state 'found guilty' unless THAT SPECIFIC PERSON", "never write
# X surpasses X"). Those rules are not gone — they are now properties of
# the shape rather than things a 1.2B model is asked to remember, which
# this module's own comment concedes it does not reliably do. Each has a
# test in tests/test_claims.py instead of a clause here.
_CLAIM_PROMPT_TEMPLATE = """\
Copy the single most newsworthy fact out of this article. Do NOT \
summarise it in your own words — locate it and copy it.

EXAMPLE
Source: "DDHQ shifts Florida governor race to toss-up. Decision Desk HQ \
(DDHQ) shifted the Florida governor's race to a toss-up on Tuesday."
Answer: {{"actor": "Decision Desk HQ (DDHQ)", "predicate": "shifted the \
Florida governor's race to a toss-up on Tuesday"}}

NOW DO THIS ONE
Source:
{source}

Rules:
- Both spans COPIED EXACTLY from the Source, character for character.
- "actor" is who did or said it: a named person, body or organisation.
- "predicate" starts with the verb and runs to the END of the phrase, \
including its object.
- The Source must say that predicate OF that actor, IN THE SAME SENTENCE.
- If the Source names no one doing anything, answer with two empty \
strings. That is a correct and common answer.

Return JSON: {{"actor": "<exact span>", "predicate": "<exact span>"}}"""


def locate_claim(
    source_material: str,
    *,
    db,
    today,
    rank: int,
) -> dict | None:
    """Ask the model to point at one assertion in `source_material`.

    Module-level rather than nested inside `_run_refresh` so a harness can
    import the REAL extraction path. Every measurement of this redesign
    had to re-implement it while it was a closure, and twice that
    re-implementation silently diverged from what ships — once testing
    unwritten code and reporting success. An unimportable function is not
    a private one, it is one whose verification has to be guessed at.
    """
    user_prompt = _CLAIM_PROMPT_TEMPLATE.format(source=source_material)
    located = call_llm(
        prompt_version=ACTION_CENTER_PROMPT_VERSION,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        cache_key={"date": today, "rank": rank, "src": source_material[:200]},
        db_session=db,
        max_tokens=200,
        num_ctx=2048,
    )
    if isinstance(located, str):
        located = extract_json(located)
    located = located if isinstance(located, dict) else None
    # The training row is this call: the prompt as sent, the model's spans,
    # and whether compose() verifies them in the source (the check
    # claims._extract applies). Until 2026-10-10 the only row was the
    # composed issue, stored against the unfilled template.
    verified = bool(located) and bool(compose(
        str(located.get("actor") or ""), str(located.get("predicate") or ""), source_material,
    ))
    _record_generation_sample(
        db, CLAIM_TASK, rank, 1, user_prompt, located or {},
        passed=verified,
        violations=None if verified else ["spans not found verbatim and together in the source"],
        once_per_day=True,
    )
    return located




def _build_actions_from_data(
    title: str,
    resolved_bills: list[dict],
    source_urls: list[str],
    source_names: list[str],
    related_senators: list[dict],
    policy_areas: list[str] | None = None,
    committees: list[dict] | None = None,
    comment_docs: list[dict] | None = None,
) -> list[dict]:
    """Build action items from real data — no LLM hallucinations.

    Senator contact is handled by the frontend SenatorChips component when
    senators are named. Here we only emit actions we have real URLs for:
    every text is a fixed template around a stored name, date or number.
    """
    actions: list[dict] = []

    # An open comment period on a rule about this story (_open_comment_docs).
    # The API serves it only while the period is open (api/action.py).
    for doc in comment_docs or []:
        close = date.fromisoformat(doc["comments_close_on"])
        actions.append({
            "text": f"Comment by {close.strftime('%B')} {close.day}",
            "type": "comment_period",
            "url": f"/explore/{doc['key']}#comment",
            "docId": doc["id"],
            # The permanent key: the row id can be replaced (services/explore_keys.py).
            "docKey": doc["key"],
            "closesOn": doc["comments_close_on"],
        })

    # The committee with jurisdiction in each chamber (committee_routing).
    # Its chair and ranking member are looked up when the issue is served,
    # so they are the current ones.
    for committee in committees or []:
        actions.append({
            "text": f"Contact the {committee['name']}",
            "type": "contact_committee",
            "url": committee["url"],
            "committee": {k: committee[k] for k in ("code", "name", "chamber")},
        })

    # Bill tracking — only when we have a real Congress.gov URL. The text
    # deliberately doesn't name a destination: the frontend links these to
    # our internal bill page when we host the bill, congress.gov otherwise.
    for bill in resolved_bills[:2]:
        if bill.get("url"):
            actions.append({
                "text": f"Track {bill['name']}",
                "type": "track_legislation",
                "url": bill["url"],
            })

    # Generic contact fallback — only emitted when no named senators found,
    # so the frontend has something to show in that case, and only for a
    # story in a federal policy area or about a bill. Emitted for every
    # issue, it asked readers to contact their senators about a French
    # protest, a Brazilian election and a campaign's fundraising total
    # (2026-10-08: the issues with no policy area were those stories).
    # Not when a committee or a comment period gives something specific.
    if not related_senators and not (committees or comment_docs) and (policy_areas or resolved_bills):
        actions.append({
            "text": f"Contact your senators or representative about {title}",
            "type": "contact_senator",
            "url": "https://www.senate.gov/senators/senators-contact.htm",
        })

    return actions


def _embed_texts(texts: list[str]) -> np.ndarray:
    model = get_embedding_model()
    return model.encode(texts, show_progress_bar=False, normalize_embeddings=True)


def _embed_texts_sim(texts: list[str]) -> np.ndarray:
    """Embeddings for the SYMMETRIC-similarity gates that were re-measured
    under the similarity model (see vector_store.get_similarity_model's
    docstring for scope discipline): policy relevance, trending mask,
    explore-doc re-rank, topic-candidate/title-dedup sims. Centered-space
    clustering gates and disambiguation stay on _embed_texts until their
    own measurement pass."""
    from app.pipeline.vector_store import get_similarity_model

    model = get_similarity_model()
    return model.encode(texts, show_progress_bar=False, normalize_embeddings=True)


def _log_summary_source_consistency(summary: str, source_text: str) -> None:
    """Automated, non-blocking semantic-consistency signal (2026-08 AI/ML
    audit): every check in grounding.py is regex/keyword-based and only
    catches a failure class after a human has already seen it in
    production. This adds one automated second opinion — cosine
    similarity between the generated summary and its source cluster text
    on the similarity model (see _embed_texts_sim) — for every generated
    issue, with no reject/retry attached.

    Bucketed into deciles (not a single low/high split) and recorded via
    the same action_metrics counter every other validator uses, so the
    persisted per-run history builds a real distribution over time. Every
    other similarity threshold in this codebase (_NEAR_IDENTICAL_TITLE_THRESHOLD,
    TOPIC_CHANGE_THRESHOLD, etc.) was set only after measuring that
    distribution against real failure cases — this collects the
    equivalent data for summary/source consistency instead of guessing a
    cutoff today. Never blocks publishing; failures here are swallowed.
    """

    try:
        summary_emb, source_emb = _embed_texts_sim([summary, source_text[:3000]])
        similarity = float(summary_emb @ source_emb)
    except Exception:
        logger.exception("Summary/source consistency check failed (non-blocking)")
        return
    action_metrics.increment_bucket("summary_source_similarity_bucket", similarity)


def _resolve_url(url: str, timeout: float = 6.0) -> str:
    """Follow Google News RSS redirect to the actual article URL.

    Google News RSS article links are opaque `news.google.com/rss/articles/CBMi...`
    redirects that don't reveal the destination until followed. A stock-market
    Reuters article may end up in a cluster about a military strike when the
    redirect is stored un-resolved. This function resolves those redirects so
    we store and display the real article URL.
    """
    if "news.google.com/rss/articles/" not in url:
        return url
    try:
        resp = httpx.head(url, follow_redirects=True, timeout=timeout, headers={
            "User-Agent": BOT_USER_AGENT,
        })
        final = str(resp.url)
        if "google.com" not in final:
            logger.debug("Resolved Google News URL → %s", final[:100])
            return final
    except Exception:
        logger.debug("Google News URL resolution (HEAD) failed for %s", url[:100], exc_info=True)
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=timeout, headers={
            "User-Agent": BOT_USER_AGENT,
        })
        final = str(resp.url)
        if "google.com" not in final:
            logger.debug("Resolved Google News URL (GET) → %s", final[:100])
            return final
    except Exception:
        logger.debug("Google News URL resolution (GET) failed for %s", url[:100], exc_info=True)
    return url


def _multi_story_headline(title: str) -> bool:
    """A live blog's headline: the outlet's kicker (its label before the
    first ": ", _headline_kicker) over two or more items naming disjoint
    entities ("Live updates: Press secretary Morrow exits; Varga rallies
    for Brandt at Dunmore county fair"). Such a page tells several stories, so its
    headline neither titles an issue nor lends its headline claim to one,
    and it is compared story by story (_story_titles).

    The kicker is what makes the disjoint-items shape precise. Measured
    2026-10-17 on 2,642 distinct headlines (national feed articles, race
    coverage, stored issues and every composed issue title): 48 split into
    disjoint items; the 20 with a kicker were all multi-story live blogs,
    and of the 28 without one, 12 were single stories (a photo column
    "<column name> | <caption>", two-sentence headlines about one event, a
    debate between two candidates; _parallel_stories' docstring). Missed: 5
    live blog headlines whose items share a name (the president's) or whose
    second item names nobody ("candidates tangle in midterm debates"); they behave as
    before. Structure only, no phrase list: any outlet's kicker counts."""
    if not _headline_kicker(title):
        return False
    items = _entity_items(title.partition(": ")[2].translate(_APOSTROPHES))
    return len(items) >= 2 and not any(a & b for i, a in enumerate(items) for b in items[i + 1:])


def _story_titles(title: str) -> list[str]:
    """The stories a headline tells: its items for a multi-story headline
    (_multi_story_headline), else the headline itself."""
    if not _multi_story_headline(title):
        return [title]
    rest = title.partition(": ")[2]
    return [item.strip() for item, _ in _split_body_items(rest) if item.strip()]


# Livestream announcements ("WATCH LIVE: The president speaks at a midterm rally
# in Ohio") are pages for watching an event, not reports of it.
# Two became confirmed issue titles in two days (2026-10-12/13), one third
# on the homepage record, and a permanent issue page kept saying "Watch
# live" after the event. The page is marked by the headline's kicker, the
# outlet's label before its first ": " (a news-headline convention like the
# digest titles' product names above), and the kicker is classified by
# embedding margin, not matched against a phrase list: the margin covers
# outlets that file their livestreams as ordinary articles (PBS puts them
# under /newshour/politics/ with no category).
#
# The cut is fitted on labels the publishers supply, never hand labels:
# among the stored feed articles (FeedArticle), an item a publisher files
# under a video section of its site (a path section naming video: The
# Hill's /video-clips/, BBC's /news/videos/) against the articles the same
# feeds file elsewhere, both with a kicker. The cut maximises F1 (ties to
# the higher cut). The labels are noisy (The Hill files some livestreams
# under /homenews/), so the cut is not "the lowest no article reaches".
# Measured on the 90 days of national-feed items stored to 2026-10-16: 18
# filed-video kickers and 126 article kickers; cut +0.120, 18 of 18 caught,
# the 2 articles past it both Hill livestreams filed as news. Replayed on
# the 1,441 headlines production composed or stored: 20 flagged, 10
# livestreams and 9 recorded "WATCH:" clips (the publishers' video class),
# and one Civitas vote-draft title that never passes through here; no feed
# article's headline. The whole headline does not separate: its topic
# outweighs the kicker.
_BROADCAST_PROTOTYPES = (
    ["tune in to the live stream now", "live broadcast airing now"],
    ["a news report", "a recorded video clip", "a live blog of updates"],
)
# Filed-video kickers needed before a cut is fitted (a convention): with
# fewer, nothing is held back. 18 were stored on 2026-10-16.
_BROADCAST_MIN_FILED = 10
_broadcast_cache: dict[str, object] = {}


def _headline_kicker(title: str) -> str | None:
    head, sep, _ = (title or "").partition(": ")
    return (head.strip() or None) if sep else None


def _filed_as_video(url: str) -> bool:
    """The publisher filed the item under a video section of its site: a
    path section (not the item's own slug) naming video."""
    return any("video" in section for section in urlparse(url or "").path.lower().split("/")[:-1])


def _broadcast_margins(kickers: list[str]) -> np.ndarray:
    broadcast, other = _BROADCAST_PROTOTYPES
    vecs = _embed_texts_sim([*kickers, *broadcast, *other])
    k, b = vecs[:len(kickers)], vecs[len(kickers):len(kickers) + len(broadcast)]
    o = vecs[len(kickers) + len(broadcast):]
    return (k @ b.T).max(axis=1) - (k @ o.T).max(axis=1)


def fit_broadcast_cut(labelled: list[tuple[str, bool]]) -> float | None:
    """The F1-maximising cut (ties to the higher) over (kicker, filed as
    video) pairs, or None with fewer than _BROADCAST_MIN_FILED filed-video
    kickers or no article ones."""
    filed = sum(v for _, v in labelled)
    if filed < _BROADCAST_MIN_FILED or filed == len(labelled):
        return None
    x = _broadcast_margins([k for k, _ in labelled])
    y = np.array([v for _, v in labelled])
    values = np.unique(x)
    cuts = (values[1:] + values[:-1]) / 2

    def f1(cut: float) -> float:
        tp = int((x[y] > cut).sum())
        return 2 * tp / (tp + int((x > cut).sum()) + int(y.sum()))

    return float(max(cuts, key=lambda c: (f1(c), c))) if len(cuts) else None


def _broadcast_threshold(db: Session) -> float | None:
    """Today's cut, fitted once per UTC day on the stored feed articles
    (fit_broadcast_cut). None, and nothing held back, until the store has
    enough filed-video items: a report wrongly held back hides a story, a
    livestream title only reads badly."""
    today = utcnow().date().isoformat()
    if _broadcast_cache.get("date") != today:
        rows = db.query(FeedArticle.source_name, FeedArticle.url, FeedArticle.title).all()
        video_sources = {r.source_name for r in rows if _filed_as_video(r.url)}
        labelled = [(k, _filed_as_video(r.url)) for r in rows if r.source_name in video_sources
                    for k in [_headline_kicker(r.title)] if k]
        cut = fit_broadcast_cut(labelled)
        logger.info("Broadcast kicker cut: %s (%d filed-video of %d labelled kickers)",
                    cut, sum(v for _, v in labelled), len(labelled))
        _broadcast_cache.update(date=today, cut=cut)
    return _broadcast_cache["cut"]


def announces_broadcast(titles: list[str], db: Session) -> list[bool]:
    """Per headline: is it a livestream or video page (its kicker scores
    past today's cut)? A headline with no kicker never is."""
    kickers = [_headline_kicker(t) for t in titles]
    present = [k for k in kickers if k]
    cut = _broadcast_threshold(db) if present else None
    if cut is None:
        return [False] * len(titles)
    scores = iter(_broadcast_margins(present))
    return [bool(k) and float(next(scores)) > cut for k in kickers]


def _filter_policy_relevant(db: Session, articles: list[NewsArticle]) -> list[tuple[NewsArticle, np.ndarray]]:
    """Keep only articles about US policy/legislation (policy_relevance.judge);
    drop digest articles."""
    if not articles:
        return []

    # Drop multi-story digest articles before embedding — they cover several
    # unrelated topics and contaminate cluster centroids, producing catch-all
    # issues whose summary presents separate events as one story.
    specific = []
    for a in articles:
        reason = digest_reason(a)
        if reason:
            logger.debug("Filtered digest article (%s): %s", reason, a.title[:80])
        else:
            specific.append(a)

    n_digests = len(articles) - len(specific)
    if n_digests:
        # Counted, not just logged: this filter drops whole articles, so
        # "is it over-firing?" needs a number that survives a deploy (see
        # action_metrics' module docstring). Incremented before the
        # everything-was-a-digest early return, which is precisely the run
        # the counter has to explain.

        action_metrics.increment("articles_dropped_digest", n_digests)

    if not specific:
        return []

    judged, fitted = policy_relevance.judge(db, specific)
    relevant = [(article, embedding) for article, embedding, _ in judged]
    logger.info(
        "Policy relevance filter: %d/%d articles passed "
        "(%d digests dropped, kNN k=%s, vote threshold=%s)",
        len(relevant), len(articles), n_digests, fitted.get("k"), fitted.get("threshold"),
    )
    return relevant


def _center_titles(embeddings: np.ndarray, mean: np.ndarray) -> np.ndarray:
    """Unit title vectors with the day's generic-headline direction removed.

    `mean` must be the whole day's, as _cluster_articles computes it — never
    a single cluster's (see the coherence filter in _run_refresh for what
    that did).
    """
    centered = embeddings - mean
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    return centered / np.where(norms < 1e-9, 1.0, norms)


def _cluster_articles(
    items: list[tuple[NewsArticle, np.ndarray]],
) -> list[list[NewsArticle]]:
    """Group articles reporting the same event: complete linkage on
    day-centered embeddings of headline and description start
    (cluster_text), so EVERY pair in a cluster clears the calibrated cut
    (cluster_calibration()["threshold"]).

    Single linkage asked only that each article resemble ONE other, and a
    second pass then merged whole clusters whose centroids scored as low as
    0.20, to join different-angle coverage of one event. Both chained
    stories that merely share a theme: issue 759 (2026-09-27) was titled
    for one city's floods, led with a hurricane elsewhere and listed facts
    about a nor'easter and an epidemic abroad. Complete linkage fixed the
    chaining; what remains is where the cut sits and what is compared.

    Headlines alone can't separate "same event" from "same theme". On 530
    hand-judged pairs of feed articles (2026-09-28 to 10-15,
    app/data/cluster_pair_judgments.json), no title-cosine cut merged more
    than 6 of 114 same-event pairs without also merging a different-event
    one, and the 0.57 a daily self-labelled fit had settled on merged 38
    same-event pairs and 22 different-event ones: two polls of one race, one
    official's rallies in two states, a statement and the letter that
    followed it. That fit was labelled by shared names and numbers, which
    call two stories about one person the same story. Adding the first 200
    characters of the description lets the description's first sentence
    say who did what; at the cut where it merges no judged different-event
    pair it merges 17 same-event ones. Requiring a shared distinctive
    entity as well changed nothing: every pair above the cut shares one.
    The cut is precision-first because a wrong merge publishes a chimera,
    while a missed merge costs breadth — the thinner cluster is dropped by
    _deduplicate_top_clusters or matched to the same issue by
    _find_matching_issue.
    """
    if not items:
        return []
    if len(items) == 1:
        return [[items[0][0]]]

    # Centered because the embedding model puts every English headline in a
    # tight band (~0.74 median cosine); removing the day's mean leaves the
    # topic-specific directions.
    embeddings = _embed_texts([cluster_text(a.title, a.summary) for a, _ in items])
    centered = _center_titles(embeddings, embeddings.mean(axis=0))
    distance = np.clip(1.0 - centered @ centered.T, 0.0, None)
    # Two articles whose own text dates the event to days STATED_DAY_GAP or
    # more apart are two occasions: put them past any cut, so complete
    # linkage never joins them (or any cluster holding both).
    days = [stated_days(f"{a.title} {a.summary or ''}", a.published.date() if a.published else None)
            for a, _ in items]
    for i, j in itertools.combinations(range(len(items)), 2):
        if days_disagree(days[i], days[j]):
            distance[i, j] = distance[j, i] = 2.0
    threshold = float(cluster_calibration()["threshold"])
    labels = fcluster(
        linkage(squareform(distance, checks=False), method="complete"),
        t=1.0 - threshold, criterion="distance",
    )
    groups: dict[int, list[NewsArticle]] = {}
    for (article, _), label in zip(items, labels):
        groups.setdefault(int(label), []).append(article)
    logger.info(
        "Clustering (complete linkage, threshold=%.2f): %d articles → %d clusters",
        threshold, len(items), len(groups),
    )
    return list(groups.values())


def _compute_trending_boost(
    clusters: list[list[NewsArticle]],
    trending: list[TrendingTopic],
) -> list[float]:
    """Compute a trending relevance score for each cluster.

    Embeds trending topic titles and each cluster's article titles, then
    takes the max cosine similarity between each cluster centroid and
    trending topics. Higher = more aligned with public discourse.
    """
    if not trending or not clusters:
        if not trending:
            logger.warning(
                "Trending data unavailable — ranking by coverage only (degraded mode)"
            )
        return [0.0] * len(clusters)

    # Filter trending topics to US policy before computing boost so sports/
    # entertainment topics can't inflate scores for unrelated clusters.
    prototype_embeddings = _embed_texts_sim(_POLICY_PROTOTYPES)
    trending_texts_all = [t.title for t in trending]
    trending_embeddings_all = _embed_texts_sim(trending_texts_all)
    policy_scores = (trending_embeddings_all @ prototype_embeddings.T).max(axis=1)
    policy_mask = policy_scores >= POLICY_RELEVANCE_THRESHOLD
    policy_trending = [t for t, keep in zip(trending, policy_mask) if keep]
    if not policy_trending:
        logger.info("No policy-relevant trending topics found — ranking by coverage only")
        return [0.0] * len(clusters)
    logger.info(
        "Trending filter: %d/%d topics are policy-relevant",
        len(policy_trending), len(trending),
    )
    trending_texts = [t.title for t in policy_trending]
    trending_embeddings = _embed_texts_sim(trending_texts)

    boosts: list[float] = []
    for cluster in clusters:
        cluster_texts = [f"{a.title}. {a.summary[:100]}" for a in cluster]
        cluster_embeddings = _embed_texts_sim(cluster_texts)
        centroid = cluster_embeddings.mean(axis=0)
        centroid /= np.linalg.norm(centroid)

        similarities = trending_embeddings @ centroid
        max_sim = float(np.max(similarities))
        top_k_mean = float(np.mean(np.sort(similarities)[-3:])) if len(similarities) >= 3 else max_sim
        boosts.append(top_k_mean)

    logger.info(
        "Trending boosts: %s",
        ", ".join(f"{b:.3f}" for b in boosts[:8]),
    )
    return boosts


_TITLED_SURNAME_RE = re.compile(
    r"\b(?:Sen(?:ator)?s?|Rep(?:resentative)?s?|Congress(?:man|woman)|Speaker)"
    r"\.?\s+([A-Z][a-zA-Z'\-]+)"
)


def _load_official_names(db: "Session") -> dict:
    """Names of officials the platform tracks, for deterministic mention counts.

    Everything here comes from the platform's own member tables — no
    hand-authored keyword lists. Returns lowercase full names and surnames
    for sitting members of Congress, plus the current president's names.
    """
    member_full: list[str] = []
    member_last: list[str] = []
    for model in (Senator, Representative):
        for (name,) in db.query(model.name).all():
            if name and len(name.split()) >= 2:
                member_full.append(name.lower())
                member_last.append(name.split()[-1].lower())

    president = (
        db.query(President).filter(President.is_current == True).first()  # noqa: E712
    )
    return {
        "member_full": member_full,
        "member_last": member_last,
        "president_full": president.name.lower() if president else "",
        "president_last": president.name.split()[-1].lower() if president else "",
    }


@lru_cache(maxsize=1024)
def _full_name_pattern(full_name: str) -> "re.Pattern[str]":
    return re.compile(
        r"(?<!\w)" + re.escape(full_name) + r"(?!\w)", re.IGNORECASE
    )


def _mentions_full_name(
    text: str, full_name: str, text_lower: str | None = None
) -> bool:
    r"""True when ``full_name`` appears in ``text`` as whole words.

    A plain ``full_name.lower() in text.lower()`` looks equivalent and is not:
    a substring match lets a name straddle word boundaries, so it re-admits
    exactly the false positives the bare-surname path is hardened against by
    _COMMON_WORD_SURNAMES. Measured against the current roster:

      "reported cases" contains "ed case"    -> Rep. Ed Case (D-HI)
      "several green energy jobs" contains "al green" -> Rep. Al Green (D-TX)

    Both surnames are on the common-word stoplist, so the surname pass
    correctly refuses them — and the full-name pass then tagged the member
    anyway, with the *higher* confidence "named in coverage" reason.

    Anchored with lookarounds rather than \b, because \b is a *transition*
    assertion: for a name ending in a non-word character it asserts the
    opposite of what is wanted. "Angus King Jr." ends in ".", and
    r"...jr\.\b" demands a word character right after that period — which
    "Jr. voted" does not have, so every member carrying a Jr./Sr. suffix would
    silently stop matching.

    The literal pre-check is load-bearing, not a micro-optimisation. A
    *leading lookbehind defeats CPython's literal-prefix scan*, so the bare
    regex restarts from every position in the text, for every one of ~535
    members, on every cluster. That took the backend test run from 1m48s to
    past a 15-minute CI timeout. Rejecting on a C-level substring scan first
    leaves the regex to run only on the rare genuine hit. Callers holding an
    already-lowercased copy should pass it as ``text_lower`` rather than make
    this lowercase a cluster-sized string once per member.
    """
    if not full_name or not text:
        return False
    name = full_name.strip()
    if not name:
        return False
    haystack = text_lower if text_lower is not None else text.lower()
    if name.lower() not in haystack:
        return False
    return _full_name_pattern(name).search(text) is not None


def _count_official_mentions(cluster_text: str, officials: dict) -> int:
    """Count distinct tracked officials named in a cluster's coverage.

    A member counts on a full-name match, or on a titled surname match
    ("Sen. Pryor", "Rep. Okafor") — the title disambiguates surnames
    that are also common words, so no stoplist is needed. The current
    president counts on a bare surname (word-boundary) match.
    """
    text_lower = cluster_text.lower()
    titled_surnames = {
        m.group(1).lower() for m in _TITLED_SURNAME_RE.finditer(cluster_text)
    }

    count = 0
    for full, last in zip(officials["member_full"], officials["member_last"]):
        if last in titled_surnames or _mentions_full_name(
            text_lower, full, text_lower
        ):
            count += 1

    if officials["president_last"]:
        if re.search(
            r"\b" + re.escape(officials["president_last"]) + r"\b", text_lower
        ) or _mentions_full_name(
            text_lower, officials["president_full"], text_lower
        ):
            count += 1

    return count


def _compute_action_link_boost(
    clusters: list[list[NewsArticle]],
    db: "Session",
) -> list[float]:
    """Score each cluster by its measurable action surface in platform data.

    The platform exists so citizens can act — contact a member, track a
    bill, respond to an executive action. Rather than scoring "civic-ness"
    against hand-authored prototype sentences (whose absolute cosine
    thresholds sat entirely inside the embedding model's ~0.55-0.87
    same-register noise floor, passing 125/125 articles and scoring a
    music performance 0.78 — 2026-07 audit), this measures two signals
    derived only from data the platform already ingests:

      1. Officials named (50%): distinct tracked officials (sitting
         members, the president) named in the cluster's coverage,
         deterministic string matching against the member tables,
         capped at 3.

      2. Civic-document similarity (50%): top-k cosine similarity between
         the cluster and the platform's own explore corpus (executive
         orders, federal rules, bills — refreshed continuously), computed
         in batch-centered embedding space. Centering subtracts the mean
         article embedding of the run, removing the shared "news
         register" component that makes raw similarities uninformative;
         the same technique clustering already uses. Measured on live
         articles: after centering, federal-policy stories score
         0.3-0.5 against the corpus while celebrity/sports/lifestyle
         stories score 0.07-0.26.
    """
    if not clusters:
        return []

    officials = _load_official_names(db)

    doc_rows = (
        db.query(ExploreDocument.title, ExploreDocument.summary)
        .order_by(ExploreDocument.date.desc())
        .limit(400)
        .all()
    )
    doc_texts = [
        f"{r.title}. {(r.summary or '')[:200]}" for r in doc_rows if r.title
    ]

    cluster_texts = [
        [f"{a.title}. {a.summary[:200]}" for a in cluster] for cluster in clusters
    ]
    flat_texts = [t for texts in cluster_texts for t in texts]
    flat_embs = _embed_texts(flat_texts)
    batch_mean = flat_embs.mean(axis=0, keepdims=True)

    def _center(embs: np.ndarray) -> np.ndarray:
        centered = embs - batch_mean
        norms = np.linalg.norm(centered, axis=1, keepdims=True)
        return centered / np.where(norms < 1e-9, 1.0, norms)

    doc_scores: list[float] = [0.0] * len(clusters)
    if doc_texts:
        doc_embs = _center(_embed_texts(doc_texts))
        flat_centered = _center(flat_embs)
        offset = 0
        for ci, texts in enumerate(cluster_texts):
            centroid = flat_centered[offset:offset + len(texts)].mean(axis=0)
            offset += len(texts)
            norm = float(np.linalg.norm(centroid))
            if norm > 1e-9:
                centroid = centroid / norm
            sims = doc_embs @ centroid
            top_k = min(10, len(sims))
            doc_scores[ci] = max(0.0, float(np.sort(sims)[-top_k:].mean()))

    max_doc = max(doc_scores) if max(doc_scores, default=0.0) > 0 else 1.0

    boosts: list[float] = []
    for ci, cluster in enumerate(clusters):
        combined_text = " ".join(f"{a.title}. {a.summary[:200]}" for a in cluster)
        n_officials = _count_official_mentions(combined_text, officials)
        official_score = min(n_officials, 3) / 3.0
        boosts.append(0.5 * official_score + 0.5 * doc_scores[ci] / max_doc)

    logger.info(
        "Action-link boosts: %s",
        ", ".join(f"{b:.3f}" for b in boosts[:8]),
    )
    return boosts


def _rank_clusters(
    clusters: list[list[NewsArticle]],
    trending: list[TrendingTopic],
    db: "Session",
) -> tuple[list[list[NewsArticle]], list[float]]:
    """Rank clusters by action surface, coverage breadth, and trending.

    Final score = 0.40 * action_link + 0.35 * coverage + 0.25 * trending.
    Actionability leads: the platform exists so citizens can act (contact
    Congress, track legislation, respond to executive action), so a story
    with a direct US action surface outranks a better-covered story
    without one. Actionability is measured from platform data (officials
    named, similarity to the ingested civic-document corpus — see
    _compute_action_link_boost), not hand-authored prototypes. Coverage
    is the stability anchor (source count doesn't change hourly);
    trending is weighted least because it is the most volatile signal
    (Bluesky/Google shift every run) and would otherwise churn the top
    issues hour to hour.

    Returns (ranked_clusters, ranked_scores) — the combined score for
    ranked_clusters[i] is ranked_scores[i], needed by
    _deduplicate_top_clusters to log the real selected/rejected score
    boundary, since dedup — not raw rank — decides final selection.
    """
    if not clusters:
        return [], []

    coverage_scores = [len({a.source_name for a in c}) for c in clusters]
    max_cov = max(coverage_scores) if coverage_scores else 1.0
    norm_coverage = [s / max_cov for s in coverage_scores]

    trending_boosts = _compute_trending_boost(clusters, trending)
    max_trend = max(trending_boosts) if trending_boosts and max(trending_boosts) > 0 else 1.0
    norm_trending = [s / max_trend for s in trending_boosts]

    us_civic_boosts = _compute_action_link_boost(clusters, db)
    max_civic = max(us_civic_boosts) if us_civic_boosts and max(us_civic_boosts) > 0 else 1.0
    norm_us_civic = [s / max_civic for s in us_civic_boosts]

    COVERAGE_WEIGHT = 0.35
    TRENDING_WEIGHT = 0.25
    US_CIVIC_WEIGHT = 0.40

    combined = [
        COVERAGE_WEIGHT * cov + TRENDING_WEIGHT * trend + US_CIVIC_WEIGHT * civic
        for cov, trend, civic in zip(norm_coverage, norm_trending, norm_us_civic)
    ]

    ranked_indices = sorted(range(len(clusters)), key=lambda i: combined[i], reverse=True)

    for i, idx in enumerate(ranked_indices[:6]):
        c = clusters[idx]
        titles = c[0].title[:60]
        logger.info(
            "  Rank %d: score=%.3f (cov=%.2f trend=%.2f civic=%.2f) sources=%d \"%s...\"",
            i + 1, combined[idx], norm_coverage[idx], norm_trending[idx], norm_us_civic[idx],
            len({a.source_name for a in c}), titles,
        )

    # Combined scores travel alongside the reordered clusters so
    # _deduplicate_top_clusters — the function that actually decides what
    # gets published, since it can skip a higher-ranked cluster as a
    # near-duplicate and promote a lower-ranked one instead — can log the
    # real selected/rejected score boundary. Logging it here instead would
    # label by raw rank position, which doesn't match final selection
    # whenever dedup reorders things.
    return [clusters[i] for i in ranked_indices], [combined[i] for i in ranked_indices]


def _deduplicate_top_clusters(
    ranked_clusters: list[list[NewsArticle]],
    ranked_scores: list[float],
    max_candidates: int,
    published: list[int],
) -> Iterator[tuple[int, list[NewsArticle]]]:
    """Yield (index, cluster) candidates in rank order, skipping any cluster
    that covers the same story as one that PUBLISHED this run.

    ``published`` is the caller's list of yielded indices that became
    issues. It is read at each step, so a cluster is judged against what
    the run has actually published so far, not against what it merely
    tried. Comparing against every earlier candidate dropped stories for
    nothing: on 2026-10-08 a live blog whose headline named two stories (a
    work-visa penalty, a court order on a press ban) ranked first, both
    stories' own clusters were dropped as its duplicates (sim 0.50 and
    0.42), and the live blog itself then failed the two-claim gate, so
    neither story had a chance in either of two consecutive runs.

    A duplicate is dropped, not merged into the cluster it resembles. It
    used to be appended to it, which is single linkage again one step
    later: on 2026-09-27's feed, once _cluster_articles stopped chaining,
    this step folded Hurricane Nolo into the nor'easter, an AI-fund tax
    story into an AI-hacking one and a voter-database ruling into the
    Missouri map. Title similarity can't tell those from same-story pairs
    (see _cluster_articles), and the published cluster already carries
    the story. The cluster_dedup_merged_* counters keep their name for the
    series; they count clusters judged duplicates.

    ranked_scores[i] is ranked_clusters[i]'s combined _rank_clusters score,
    logged here as selected (tried) or rejected (a duplicate) because this
    function decides which clusters are tried.
    """
    if not ranked_clusters:
        return

    # Threshold in normalized-centered-embedding space, above which two
    # top-ranked clusters are treated as the same story.
    #
    # THE DATA THIS ASKED FOR HAS NOW ACCUMULATED, and it says the gate
    # was unreachable. Across 1,039 persisted runs the bucketed counters
    # below recorded 496 keep decisions and ZERO merges: the highest
    # cluster-pair similarity ever observed falls in the 0.30-0.39
    # bucket, so a 0.50 floor could never fire. Two top clusters
    # covering one story both became issues, which with MAX_ISSUES at 2
    # meant a day's entire Action Center could be the same event twice.
    #
    # 0.35 is the top of the observed range rather than a guess: 493 of
    # 496 observations sit below it, so it merges only the pairs that
    # actually reach the tail the old constant was aiming at. It stays
    # logged, so if the distribution shifts this is re-derivable the
    # same way instead of re-guessed.
    DEDUP_THRESHOLD = 0.35

    cluster_texts = [
        " ".join(a.title for a in cluster[:5])
        for cluster in ranked_clusters
    ]
    raw_embs = _embed_texts(cluster_texts)
    mean_emb = raw_embs.mean(axis=0, keepdims=True)
    centered = raw_embs - mean_emb
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    embeddings = centered / np.where(norms < 1e-9, 1.0, norms)

    tried = 0
    for i, cluster in enumerate(ranked_clusters):
        if tried >= max_candidates:
            return
        action_metrics.increment("clusters_considered")

        merged_into = None
        best_sim = -1.0
        for j in published:
            sim = float(embeddings[i] @ embeddings[j])
            best_sim = max(best_sim, sim)
            if sim >= DEDUP_THRESHOLD:
                merged_into = j
                break

        if published:
            outcome = "merged" if merged_into is not None else "kept"
            action_metrics.increment_bucket(f"cluster_dedup_{outcome}_sim_bucket", best_sim)

        if merged_into is not None:
            logger.info(
                "Dropped cluster '%s...' as a duplicate of '%s...' (sim=%.3f)",
                cluster[0].title[:40],
                ranked_clusters[merged_into][0].title[:40],
                float(embeddings[i] @ embeddings[merged_into]),
            )
            action_metrics.increment_bucket("cluster_rank_score_rejected", ranked_scores[i])
            continue

        action_metrics.increment_bucket("cluster_rank_score_selected", ranked_scores[i])
        tried += 1
        yield i, cluster


# Last names that are also common English words — require a full-name match
# only; a bare last-name hit for these is nearly always a false positive
# (e.g. "justice" in "Department of Justice", "congress" in any legislative
# text, "banks" in finance news, "figures" in "the data figures show...").
# Shared by _find_related_senators (senators/reps) and _find_related_officials
# (justices) — same failure mode, same fix, one list. Hand-curated against the
# real 2026-07 member/justice roster rather than a general English dictionary:
# the entity set here is small and bounded (~545 people total), so a reviewed
# exclusion list is the right tool, not a wordlist dependency — but it must be
# reviewed comprehensively against real names when problems are found, not
# extended one name at a time reactively (that's how "figures" — Rep. Shomari
# Figures, seated Jan 2025 — went unnoticed until it misfired in production).
_COMMON_WORD_SURNAMES = {
    "justice", "congress", "banks", "young", "price", "bush", "king",
    "reed", "hunt", "law", "case", "judge", "bond",
    # Added 2026-07-21 after Figures false-positive audit — reviewed the
    # full current senator/representative/justice roster for the same
    # pattern rather than only fixing the one reported name.
    "figures", "bean", "bell", "bishop", "brown", "cloud", "cotton",
    "crane", "crow", "dean", "drew", "fields", "fine", "flood", "foster",
    "gray", "green", "guest", "hill", "mace", "marshall", "mills", "moody",
    "rose", "self", "sessions", "strong", "waters", "jackson",
}


# Honorifics/titles that legitimately precede a member's bare surname —
# a capitalized token from this set before the surname means the mention
# is (or may be) the member, not somebody else's full name.
_NAME_PRECEDING_TITLES = {
    "senator", "sen", "representative", "rep", "congressman", "congresswoman",
    "speaker", "chairman", "chairwoman", "chair", "leader", "whip",
    "secretary", "governor", "gov", "president", "justice", "judge",
    "dr", "mr", "mrs", "ms",
}


def _surname_owned_by_other_name(text: str, match: "re.Match", member_name: str) -> bool:
    """True when this surname occurrence is part of a DIFFERENT person's
    full name — i.e. immediately preceded by a capitalized given name that
    is neither one of the member's own name tokens nor a title/honorific.

    "featuring Ferran Torres' late goal" → the "Torres" is owned by
    "Ferran", who is not Rep. Ritchie Torres → True.
    "Sen. Torres said" / "... change. Torres said" → False (title-prefixed
    or sentence-initial — could genuinely be the member).
    """
    preceding = text[: match.start()].rstrip()
    m = re.search(r"([A-Za-z'\-]+[\.!?:;]?)$", preceding)
    if not m:
        return False
    raw = m.group(1)
    if raw[-1] in ".!?:;":
        # Sentence boundary (or an abbreviation/initial) — the capitalized
        # word before it belongs to the previous sentence, not this name.
        return False
    prev = raw.rstrip("'")
    if not prev or not prev[0].isupper() or len(prev) < 2:
        return False  # lowercase word or bare initial — not a claiming name
    prev_lower = prev.lower()
    if prev_lower in _NAME_PRECEDING_TITLES:
        return False
    member_tokens = {t.lower().rstrip(".") for t in member_name.split()}
    return prev_lower not in member_tokens


def _find_related_senators(
    title: str,
    summary: str,
    facts: list[str],
    db: Session,
    article_texts: list[str] = (),
) -> list[dict]:
    """Members of Congress the issue names: by full name, or by a surname
    only one current member has, used as no one else's name.

    `article_texts` (each on-topic article's headline and description) are
    searched for full names only ("named in the reporting"). The issue's own
    text is a headline and one or two quoted lines, which name a member far
    less often than the articles they come from; bills were already resolved
    from the articles (_resolve_bills) while members were looked for only in
    the issue's text. A bare surname is not looked for there: a longer text
    holds more namesakes.

    A surname two or more members share names none of them. On 2026-10-08,
    69 of the 120 links made from a bare surname were such surnames: one
    story about the Speaker linked five Johnsons, the Kennedy Center three
    Kennedys. The embedding check that was meant to settle it scored real
    and unrelated contexts in overlapping ranges (measured, see below), so
    it could not pick one; it is gone. The same rule governs naming a
    candidate on election night: a surname alone never picks between
    namesakes.
    """
    senators = db.query(
        Senator.id, Senator.name, Senator.state, Senator.party,
        Senator.score_funding_independence, Senator.score_promise_persistence,
        Senator.score_constituent_alignment, Senator.score_funding_diversity,
        Senator.score_legislative_effectiveness,
        Senator.leadership_score, Senator.contact_form_url, Senator.website_url,
    ).all()

    representatives = db.query(
        Representative.id, Representative.name, Representative.state, Representative.party,
        Representative.score_funding_independence, Representative.score_promise_persistence,
        Representative.score_constituent_alignment, Representative.score_funding_diversity,
        Representative.score_legislative_effectiveness,
        Representative.leadership_score, Representative.contact_form_url, Representative.website_url,
    ).all()

    if not senators and not representatives:
        return []

    issue_text = f"{title}. {summary}. {' '.join(facts)}"
    issue_text_lower = issue_text.lower()

    matched: dict[str, dict] = {}

    def _make_entry(s, chamber: str = "senate", match_reason: str | None = None) -> dict:
        overall = compute_overall_score(s)
        return {
            "id": s.id, "name": s.name, "state": s.state,
            "party": s.party, "overall_score": overall,
            "leadership_score": round(s.leadership_score * 100) if s.leadership_score is not None else None,
            "chamber": chamber,
            "match_reason": match_reason,
            "contact_form_url": getattr(s, "contact_form_url", "") or "",
            "website_url": getattr(s, "website_url", "") or "",
        }

    all_members = [(s, "senate") for s in senators] + [(r, "house") for r in representatives]

    # Full-name matches first, over every member, before any last-name-only
    # candidacy is considered. The roster has real surname collisions (Smith,
    # Carver, Moore, Whitfield all appear multiple times) — checking each
    # member independently for a last-name match risks fuzzy-matching one
    # member's story onto a different person who happens to share the same
    # surname. Collecting every confirmed full-name match FIRST means a
    # shared surname is only ever a live disambiguation candidate for
    # whichever single member wasn't already explained by a more specific
    # match.
    full_name_matched_last_names: set[str] = set()
    reporting = " ".join(article_texts)
    reporting_lower = reporting.lower()
    for s, chamber in all_members:
        if s.name and _mentions_full_name(issue_text, s.name, issue_text_lower):
            matched[s.id] = _make_entry(s, chamber, match_reason="named in coverage")
            full_name_matched_last_names.add(s.name.split()[-1].lower())
        elif s.name and reporting and _mentions_full_name(reporting, s.name, reporting_lower):
            matched[s.id] = _make_entry(s, chamber, match_reason="named in the reporting")
            full_name_matched_last_names.add(s.name.split()[-1].lower())

    surname_holders = Counter(s.name.split()[-1].lower() for s, _ in all_members if s.name)
    for s, chamber in all_members:
        if s.id in matched:
            continue

        last_name = s.name.split()[-1].lower() if s.name else ""
        if surname_holders[last_name] > 1:
            continue

        if len(last_name) < 4:
            continue

        # Last names that are common institutional words skip bare last-name matching
        # entirely — they require the full name to appear in text.
        if last_name in _COMMON_WORD_SURNAMES:
            continue

        # A different member with this exact surname was already confirmed
        # by full-name match above — every occurrence of the surname in
        # this text is already accounted for by that more specific match,
        # so this member isn't a live candidate at all (see the note above
        # Pass 1 for the Whitfield/Whitfield case this fixes).
        if last_name in full_name_matched_last_names:
            continue

        # Last-name-only match needs word-boundary + disambiguation
        pattern = re.compile(r"\b" + re.escape(last_name) + r"\b", re.IGNORECASE)
        occurrences = list(pattern.finditer(issue_text))
        if occurrences and all(
            _surname_owned_by_other_name(issue_text, m, s.name) for m in occurrences
        ):
            # Every occurrence of this surname is part of a DIFFERENT
            # person's full name — generalizes the full-name-matched-member
            # exclusion above to people the platform doesn't track at all
            # (e.g. an athlete sharing a surname with a member of Congress).
            # An embedding check of the context couldn't tell these apart:
            # measured against real cases, an unrelated context scored
            # 0.78-0.80 against the "Representative X from NY" style
            # prototypes while genuine civic references scored 0.77-0.85.
            continue
        if occurrences:
            matched[s.id] = _make_entry(s, chamber, match_reason="referenced in coverage")

    result = list(matched.values())
    if result:
        logger.info("Found %d related senators for '%s': %s",
                     len(result), title[:50],
                     ", ".join(
                         f"{m['name']} ({m['match_reason']})" for m in result
                     ))
    return result


def _find_related_officials(
    title: str,
    summary: str,
    facts: list[str],
    db: Session,
) -> list[dict]:
    """Extend related_senators to cover the current president and active justices.

    Returns a combined list of all matched officials (senators, reps, president,
    justices) with a 'branch' key added to each entry. Senators/reps are detected
    by _find_related_senators; president and justices use the same substring +
    embedding disambiguation pattern.
    """
    combined: list[dict] = []

    # Senators + reps (existing logic, backward compat)
    for entry in _find_related_senators(title, summary, facts, db):
        combined.append({**entry, "branch": entry.get("chamber", "senate")})

    text = f"{title} {summary} {' '.join(facts)}"

    # President detection — simple name-forms check, no embedding needed
    current_president = db.query(President).filter(President.is_current == True).first()  # noqa: E712
    if current_president:
        last_name = current_president.name.split()[-1]
        president_patterns = [
            last_name,
            current_president.name,
            "the president",
            "the white house",
            "executive order",
        ]
        if any(p.lower() in text.lower() for p in president_patterns):
            if not any(e["id"] == current_president.id for e in combined):
                combined.append({
                    "id": current_president.id,
                    "name": current_president.name,
                    "party": current_president.party,
                    "branch": "president",
                    "match_reason": "named in coverage",
                })

    # Justices: by full name, or by a surname no member or other justice
    # holds and no one else's name uses (the rules _find_related_senators
    # documents).
    justices = db.query(
        Justice.id, Justice.name, Justice.appointing_party,
    ).filter(Justice.is_active == True).all()  # noqa: E712

    if justices:
        matched_justices: dict[str, dict] = {}
        member_names = [n for (n,) in db.query(Senator.name).all()] + [n for (n,) in db.query(Representative.name).all()]
        surname_holders = Counter(n.split()[-1].lower() for n in [*member_names, *(j.name for j in justices)] if n)
        for j in justices:
            last = j.name.split()[-1]
            if _mentions_full_name(text, j.name):
                reason = "named in coverage"
            elif (
                len(last) < 4 or last.lower() in _COMMON_WORD_SURNAMES
                or surname_holders[last.lower()] > 1
            ):
                continue
            else:
                occurrences = list(re.finditer(r"\b" + re.escape(last) + r"\b", text, re.IGNORECASE))
                if not occurrences or all(_surname_owned_by_other_name(text, m, j.name) for m in occurrences):
                    continue
                reason = "referenced in coverage"
            matched_justices[j.id] = {
                "id": j.id, "name": j.name, "party": j.appointing_party or "",
                "branch": "scotus", "match_reason": reason,
            }

        for entry in matched_justices.values():
            if not any(e["id"] == entry["id"] for e in combined):
                combined.append(entry)

    return combined


def _classify_issue_policy_areas(title: str, summary: str) -> list[str]:
    """Classify an action center issue into its policy area using embeddings.

    Uses the same embedding-based classifier as bills (tier 2) rather than
    relying on the LLM, which inconsistently returns empty or wrong labels.
    Single-area only — see classify_policy_areas_multi's docstring for why
    secondary-area detection was removed: raw cosine similarity across the
    category anchors can't distinguish genuine secondary relevance from
    noise for this embedding model.
    """
    from app.pipeline.analyze.bill_analyzer import classify_policy_areas_multi

    text = f"{title}. {summary}"
    try:
        areas = classify_policy_areas_multi(text)
        result = [
            a["area"] for a in areas
            if a["area"] != "PROCEDURAL" and a.get("confidence", 0) > 0.15
        ]
        if result:
            logger.debug("Policy area for '%s': %s", title[:50], result)
            return result
    except Exception as e:
        logger.warning("Policy area classification failed: %s", e)
    return []


# Calibrated against real, hand-labeled Action Center issues: both
# sqlite-vec L2 distance (gate 1, full document text) and title-only
# cosine similarity (gate 2, see below) against retrieved candidates were
# measured against a genuine-match/not-a-match labeling. Genuinely
# unrelated topics cluster at distance > 0.87; the best real match found
# in the sample sat at 0.80. Tightened to 0.85 rather than exactly at
# that boundary, since a small sample isn't enough to trust to the second
# decimal — a real improvement, not a perfect fix (see
# _ADMINISTRATIVE_NOTICE_TITLE_RE below for the one false-positive
# pattern tight enough to survive this threshold).
_EXPLORE_DOC_MAX_DISTANCE = 0.85

# Title-to-title similarity (all-MiniLM-L6-v2) a document needs to an
# issue's title to be linked to it (_find_related_explore_docs,
# _open_comment_docs). Min-error on the 299 links stored on issues since
# 2026-09-01, each read and labelled by hand (2026-10-08): 0.49. The
# earlier 0.33 came
# from a sample whose unrelated pairs all scored 0.13-0.18 (floor
# speeches), but the federal register supplies unrelated documents
# that score well above that — Coast Guard safety zones in Miami on a
# story about a Florida golf club (0.34-0.38), an executive order on
# "Made in America" advertising on a story about political ads
# (0.43-0.45). 189 of the 299 stored links were unrelated; at 0.49, 6
# unrelated are kept and 58 related are lost. The loss is the cheap
# side: a missing link shows nothing, a wrong one misleads, and two
# wrong ones also passed the publication gate below as an anchor.
EXPLORE_TITLE_MIN_SIM = 0.49

# Paperwork Reduction Act information-collection notices and Federal
# Advisory Committee Act meeting notices use fixed, legally-templated
# title phrasing (5 CFR 1320 / 41 CFR 102-3) and are — by the nature of
# what they announce (a routine data-collection renewal, an upcoming
# committee meeting) — never substantively about any particular news
# story, regardless of how their titles happen to embed. This is the one
# false-positive pattern that survives the distance/similarity tightening
# above: this kind of notice reliably scores well inside the "genuine
# match" bands for unrelated issues. Matching the fixed template phrasing
# (not a list of specific bad titles — every notice using this
# legally-mandated language is generic) is the same "measure the real
# property, don't guess" principle as GENERIC_TITLE_REPEAT_THRESHOLD
# above, just for a structural pattern repeat-counting can't catch since
# each notice is uniquely titled. Extend the phrasing list with real
# PRA/FACA variants as they're found, rather than guessing ahead of time.
_ADMINISTRATIVE_NOTICE_TITLE_RE = re.compile(
    r"information collection|submission for omb review|proposed collection.*comment request|"
    r"notice of (public )?meeting|open meeting of|stakeholder consultation meeting|"
    r"solicitation of nominations",
    re.IGNORECASE,
)


def _find_related_explore_docs(
    title: str,
    summary: str,
    policy_areas: list[str],
    db: Session,
    max_docs: int = 3,
) -> list[dict]:
    """Find explore documents genuinely related to this issue.

    Uses a two-gate approach:
      1. sqlite-vec L2 distance must be below ``_EXPLORE_DOC_MAX_DISTANCE``
      2. Reciprocal similarity: the candidate doc title is embedded against
         the issue title alone (not summary), and only kept if it clears the
         fixed ``min_sim`` bar below and lands in the top ``max_docs``. (This
         was an adaptive median-of-the-pool threshold once; a median floats
         with whatever noise the retrieval returned, so an all-noise pool
         still promoted its own upper half.)

    Using title-only for re-ranking avoids false matches caused by generic
    words in the summary (e.g. "Pentagon", "supply chain") that overlap with
    unrelated government documents.
    """
    query = f"{title} {' '.join(policy_areas)}"
    try:
        results = search_explore_documents(query=query, n_results=max_docs * 8)
    except Exception as e:
        logger.warning("Explore doc search failed: %s", e)
        return []

    if not results:
        return []

    passed = [
        r for r in results
        if r.get("id") and r.get("distance", 999) < _EXPLORE_DOC_MAX_DISTANCE
    ]

    if not passed:
        return []

    doc_texts = [r.get("title", "") for r in passed]
    try:
        all_embs = _embed_texts_sim([title] + doc_texts)
        title_emb = all_embs[0]
        doc_embs = all_embs[1:]
        sims = np.array([float(np.dot(title_emb, d)) for d in doc_embs])
    except Exception:
        sims = np.zeros(len(passed))

    min_sim = EXPLORE_TITLE_MIN_SIM

    scored = sorted(
        zip(passed, sims),
        key=lambda x: x[1],
        reverse=True,
    )

    all_candidate_ids = [r["id"] for r, _ in scored]
    docs_by_id = {
        d.id: d
        for d in db.query(
            ExploreDocument.id, ExploreDocument.title,
            ExploreDocument.doc_type, ExploreDocument.date, ExploreDocument.url,
        ).filter(ExploreDocument.id.in_(all_candidate_ids)).all()
    }

    # Boilerplate-titled documents (e.g. "LEGISLATIVE SESSION", used by
    # hundreds of Senate floor-speech records covering completely
    # different bills) match almost anything above min_sim, since the
    # title-only re-ranking this function deliberately uses (see docstring)
    # can't discriminate documents whose title carries no topic-specific
    # signal — confirmed live 2026-07: "LEGISLATIVE SESSION" (real title,
    # real content "Mr. President, I move to proceed to Calendar No. X")
    # linked to both a Ukraine aid story and a budget-resolution story on
    # the same day. Measure title genericness directly (how many
    # ExploreDocument rows share this exact title) rather than hardcoding
    # a list of known-generic titles — a title repeated 5+ times is
    # structurally uninformative regardless of what it says.
    GENERIC_TITLE_REPEAT_THRESHOLD = 5
    candidate_titles = {d.title for d in docs_by_id.values()}
    title_counts = dict(
        db.query(ExploreDocument.title, func.count(ExploreDocument.id))
        .filter(ExploreDocument.title.in_(candidate_titles))
        .group_by(ExploreDocument.title)
        .all()
    ) if candidate_titles else {}

    seen_titles: set[str] = set()
    unique: list[dict] = []
    for r, sim in scored:
        if float(sim) < min_sim:
            logger.debug(
                "Explore doc rejected (sim=%.3f < %.2f): '%s'",
                sim, min_sim, r.get("title", "")[:60],
            )
            continue
        d = docs_by_id.get(r["id"])
        if not d:
            continue
        if title_counts.get(d.title, 1) >= GENERIC_TITLE_REPEAT_THRESHOLD:
            logger.debug(
                "Explore doc rejected (boilerplate title used by %d docs): '%s'",
                title_counts[d.title], d.title[:60],
            )
            continue
        if _ADMINISTRATIVE_NOTICE_TITLE_RE.search(d.title):
            logger.debug(
                "Explore doc rejected (administrative notice template): '%s'",
                d.title[:60],
            )
            continue
        key = d.title.strip().lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        logger.info("Explore doc linked: sim=%.3f '%s'", sim, d.title[:60])
        unique.append(
            {"id": d.id, "title": d.title, "doc_type": d.doc_type,
             "date": d.date, "url": d.url}
        )
        if len(unique) >= max_docs:
            break

    return unique


MAX_COMMENT_ACTIONS = 2
_open_rule_title_embs: dict[int, np.ndarray] = {}


def open_comment_filter():
    """The #903 rule for "open for comment": a comment link, and a closing
    date on or after today in the deadline's zone."""
    return (
        (ExploreDocument.comment_url.isnot(None))
        & (ExploreDocument.comment_url != "")
        & (ExploreDocument.comments_close_on >= comment_period_today())
    )


def _open_comment_docs(title: str, related_docs: list[dict], db: Session) -> list[dict]:
    """Rules open for comment that are about this issue, as
    {"id", "key", "title", "comments_close_on"}: the issue's related documents that
    are open, then open rules whose titles clear the same title-similarity
    bar related documents do (EXPLORE_TITLE_MIN_SIM). The related-document
    search ranks every document type, so an open rule rarely reaches its top
    three on its own."""
    rows = db.query(
        ExploreDocument.id, ExploreDocument.title, ExploreDocument.comments_close_on, ExploreDocument.doc_type,
        ExploreDocument.external_id,
    ).filter(open_comment_filter()).all()
    open_by_id = {r.id: r for r in rows}
    picked = [open_by_id[d["id"]] for d in related_docs if d["id"] in open_by_id]
    picked_ids = {r.id for r in picked}
    rules = [r for r in rows if r.doc_type in RULE_DOC_TYPES and r.id not in picked_ids]
    if rules and len(picked) < MAX_COMMENT_ACTIONS:
        # Embeddings of open rules' titles outlive a run (titles don't
        # change); dropped once a rule closes.
        for stale in set(_open_rule_title_embs) - {r.id for r in rules}:
            del _open_rule_title_embs[stale]
        missing = [r for r in rules if r.id not in _open_rule_title_embs]
        if missing:
            for r, emb in zip(missing, _embed_texts_sim([r.title for r in missing])):
                _open_rule_title_embs[r.id] = emb
        title_emb = _embed_texts_sim([title])[0]
        scored = sorted(((float(title_emb @ _open_rule_title_embs[r.id]), r) for r in rules),
                        key=lambda x: -x[0])
        for sim, r in scored:
            if sim < EXPLORE_TITLE_MIN_SIM or len(picked) >= MAX_COMMENT_ACTIONS:
                break
            logger.info("Open comment period linked: sim=%.3f '%s'", sim, r.title[:60])
            picked.append(r)
    return [{"id": r.id, "key": public_key(r.external_id, r.id), "title": r.title,
             "comments_close_on": r.comments_close_on}
            for r in picked[:MAX_COMMENT_ACTIONS]]


# The Today tab ranks open comment periods by how many people have read
# them (api/action.get_open_comments), from the Federal Register's own
# counter. Read here, once a day, rather than in the nightly Explore
# refresh: that module is inside the analysis-code fingerprint, and adding a
# field there would clear the learning store for a display ranking.
_FR_VIEWS_CACHE_TIER = "action"
_FR_VIEWS_CACHE_KEY = "fr_page_views_refreshed"
FR_VIEWS_REFRESH_HOURS = 24


def _refresh_open_comment_views(db: Session) -> int:
    """Store the Register's reader count and table-of-contents subject on
    every open Federal Register document, at most once per
    FR_VIEWS_REFRESH_HOURS. Returns how many rows changed. A failed request
    changes nothing and is retried next hour. The subject is what tells a
    paperwork notice from other notices (app/comment_periods.py); read here
    rather than at ingest, whose module is fingerprinted."""
    if api_cache_get(db, _FR_VIEWS_CACHE_TIER, _FR_VIEWS_CACHE_KEY, max_age_hours=FR_VIEWS_REFRESH_HOURS):
        return 0
    rows = db.query(ExploreDocument).filter(
        open_comment_filter(), ExploreDocument.external_id.like("fr-reg-%"),
    ).all()
    by_number = {r.external_id.removeprefix("fr-reg-"): r for r in rows}

    async def _fetch():
        async with make_async_client(headers={"User-Agent": BOT_USER_AGENT}) as client:
            return await fetch_fr_metadata(
                client, list(by_number), ["document_number", "page_views", "toc_subject", "toc_doc"],
            )

    got = asyncio.run(_fetch()) if by_number else ({}, set())
    if got is None:
        return 0
    changed = 0
    for number, doc in got[0].items():
        row = by_number.get(number)
        if row is None:
            continue
        views = (doc.get("page_views") or {}).get("count")
        # The Register gives toc_doc alone when the subject has no sub-entry.
        subject = doc.get("toc_subject") or doc.get("toc_doc") or None
        fresh = {"fr_page_views": views if isinstance(views, int) else row.fr_page_views, "fr_toc_subject": subject}
        moved = {k: v for k, v in fresh.items() if getattr(row, k) != v}
        for k, v in moved.items():
            setattr(row, k, v)
        changed += bool(moved)
    db.commit()
    api_cache_set(db, _FR_VIEWS_CACHE_TIER, _FR_VIEWS_CACHE_KEY, {"documents": len(by_number), "changed": changed},
                  normal_ttl_hours=FR_VIEWS_REFRESH_HOURS)
    return changed


CONGRESS_API_BASE = "https://api.congress.gov/v3"


def _resolve_bills(
    article_texts: list[str],
    titles: dict[str, set[str]] | None = None,
    bill_titles: dict[str, list[str]] | None = None,
) -> list[dict]:
    """The bills the articles name, as {"name", "id", "url", "congress"}.

    A number counts only when the words around it name that bill
    (lobbying_records.bill_mentions and names_bill, against `bill_titles`,
    {bill id: its titles}, the Congress's bills the site holds). A bare
    pattern match took any "s 2026" for a Senate bill: on 2026-10-08, most
    Senate bills on recent issues were "S.2024", "S.2025", "S.2026", "S.50"
    from text like "Trump's 2026". And a number alone doesn't identify a
    bill: news still cites the last Congress's numbers.

    A bill the articles name by its short title (`titles`,
    bill_service.short_title_index) counts too: news names a bill that way
    far more often than by number ("the Protect College Sports Act",
    2026-09-28, never "S. 4668"). A title two bills share (House and Senate
    companions) names neither.
    """
    combined_text = " ".join(article_texts)
    ids: list[str] = []
    for bill_id, before, after in bill_mentions(combined_text):
        bill_title_list = (bill_titles or {}).get(bill_id)
        if bill_id not in ids and bill_title_list and names_bill(before, after, bill_title_list, bill_id, None):
            ids.append(bill_id)

    combined_text_lower = combined_text.lower()
    for name, named in (titles or {}).items():
        if len(named) == 1 and names_phrase(combined_text_lower, name):
            (bill_id,) = named
            if bill_id not in ids:
                ids.append(bill_id)

    if ids:
        logger.info("Bills named: %s", ", ".join(ids))
    resolved: list[dict] = []
    for bill_id in ids:
        if url := congress_gov_url_for_bill_id(bill_id, settings.CURRENT_CONGRESS):
            resolved.append({
                "name": bill_id, "id": bill_id, "url": url,
                "congress": settings.CURRENT_CONGRESS,
            })
    return resolved[:5]




_PERIOD_REVIEW_SYSTEM = (
    "You are a nonpartisan civic analyst. Summarize the period's top civic issues "
    "factually and briefly. Never advocate for any position."
)

_PERIOD_REVIEW_PROMPT = """\
Summarize the top U.S. civic issues from {label}.

Top stories from this period:
{entries_text}

Produce a JSON object:
{{
  "summary": "2-3 sentences summarizing the dominant civic themes and why they mattered.",
  "topAreas": ["area1", "area2", "area3"]
}}
Use only information from the stories above. Be factual and neutral."""


def _generate_period_summary(label: str, entries: list, cache_key: dict, db: "Session") -> dict:
    """LLM-generate a summary for a week/month/year period."""

    entries_text = "\n".join(
        f"- [{e.date}] {e.title}: {e.summary[:120]}"
        for e in entries[:30]
    )
    user_prompt = _PERIOD_REVIEW_PROMPT.format(label=label, entries_text=entries_text)

    result = call_llm(
        prompt_version="period-review-v1",
        system_prompt=_PERIOD_REVIEW_SYSTEM,
        user_prompt=user_prompt,
        cache_key=cache_key,
        db_session=db,
        max_tokens=400,
        num_ctx=2048,
    )
    if isinstance(result, str):
        result = extract_json(result)
    if not isinstance(result, dict):
        return {"summary": "", "topAreas": []}

    summary = str(result.get("summary", ""))
    # This is published as a WeekSummary/MonthSummary and ran NO
    # mechanical check at all — the third such gap found in the
    # 2026-09-23 audit, after justice_pipeline and monitor metadata. The
    # entries block it was generated from is its source material. An
    # empty summary is a normal outcome the caller already handles, so
    # failing closed costs a period review, not a crash.
    if summary:
        reasons = grounding_violations(summary, entries_text)
        reasons += hedge_and_editorializing_violations(summary)
        if reasons:
            logger.warning(
                "Period summary for %s failed grounding (%s) — publishing no summary",
                label, "; ".join(reasons)[:200],
            )
            action_metrics.increment("period_summary_ungrounded")
            summary = ""

    return {
        "summary": summary,
        "topAreas": [str(a) for a in result.get("topAreas", [])[:5]],
    }


def week_span_label(start: date, end: date) -> str:
    """ "September 21–27, 2026", "September 28–October 4, 2026",
    "December 28, 2026–January 3, 2027". Formatting the end as a bare day
    gave a week across a month boundary as "September 28–4, 2026", which
    the model then copied into the week's summary."""
    if start.year != end.year:
        return f"{start.strftime('%B %-d, %Y')}–{end.strftime('%B %-d, %Y')}"
    if start.month != end.month:
        return f"{start.strftime('%B %-d')}–{end.strftime('%B %-d, %Y')}"
    return f"{start.strftime('%B %-d')}–{end.strftime('%-d, %Y')}"


def generate_period_summaries(today_str: str, db: "Session") -> None:
    """Generate missing week/month/year summaries for all completed periods.

    Called after each action center refresh. Generates at most a handful of
    LLM calls (one per newly-completed period) so it does not add much time.
    """
    import json as _json
    from datetime import timedelta
    from app.models import WeekSummary, MonthSummary, YearSummary, TimelineEntry

    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    current_year = today.year
    current_month = today.month

    # ISO week containing today (week starts Monday)
    current_week_num = today.isocalendar()[1]

    # --- Week summaries ---
    # For each distinct ISO week in the DB that has ended (Sunday < today), ensure a WeekSummary exists.
    # From the start of LAST year, not this one: December is complete only
    # once January has begun, and the week spanning New Year belongs to
    # both. Read from January 1st alone, December never got a month summary
    # and that week's was written from its January days only. Periods
    # already summarized are skipped below, so the wider read costs no LLM
    # call.
    recent_entries = (
        db.query(TimelineEntry)
        .filter(TimelineEntry.date >= f"{current_year - 1}-01-01",
                TimelineEntry.date <= today_str)
        .order_by(TimelineEntry.date)
        .all()
    )
    recent_entries = without_repeat_leads(recent_entries)

    # Group by the full ISO (year, week) pair, not week number alone:
    # late-December dates can belong to ISO week 1 of the NEXT year (and
    # early January to week 52/53 of the previous one), so keying on week
    # number collided the year-end week with January's row of the same
    # number — the year-end week was then silently never summarized.
    weeks: dict[tuple[int, int], list] = {}
    for e in recent_entries:
        d = datetime.strptime(e.date, "%Y-%m-%d").date()
        iso = d.isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append(e)

    current_iso_key = (today.isocalendar()[0], current_week_num)
    for (wyear, wnum), week_entries in weeks.items():
        if (wyear, wnum) == current_iso_key:
            continue  # current week is not complete yet
        # Compute Monday/Sunday for this week
        first_entry_date = datetime.strptime(week_entries[0].date, "%Y-%m-%d").date()
        monday = first_entry_date - timedelta(days=first_entry_date.weekday())
        sunday = monday + timedelta(days=6)
        if sunday >= today:
            continue  # week hasn't fully ended yet

        existing = (
            db.query(WeekSummary)
            .filter(WeekSummary.year == wyear, WeekSummary.week_num == wnum)
            .first()
        )
        if existing:
            continue

        label = f"the week of {week_span_label(monday, sunday)}"
        top_areas: dict[str, int] = {}
        for e in week_entries:
            for area in _json.loads(e.policy_areas or "[]"):
                top_areas[area] = top_areas.get(area, 0) + 1
        computed_areas = [a for a, _ in sorted(top_areas.items(), key=lambda x: -x[1])[:5]]

        llm = _generate_period_summary(
            label=label,
            entries=week_entries,
            cache_key={"period": "week", "year": wyear, "week": wnum},
            db=db,
        )
        db.add(WeekSummary(
            year=wyear,
            week_num=wnum,
            start_date=monday.strftime("%Y-%m-%d"),
            end_date=sunday.strftime("%Y-%m-%d"),
            summary=llm["summary"],
            top_policy_areas=_json.dumps(llm["topAreas"] or computed_areas),
            entry_count=len(week_entries),
        ))
        db.commit()
        logger.info("Generated week-in-review for %s W%d", wyear, wnum)

    # --- Month summaries ---
    # For each completed month (not the current one), this year or last
    months_done: dict[tuple[int, int], list] = {}
    for e in recent_entries:
        months_done.setdefault((int(e.date[:4]), int(e.date[5:7])), []).append(e)

    for (myear, mnum), month_entries in months_done.items():
        if (myear, mnum) >= (current_year, current_month):
            continue  # current month not complete
        existing = (
            db.query(MonthSummary)
            .filter(MonthSummary.year == myear, MonthSummary.month == mnum)
            .first()
        )
        if existing:
            continue

        month_name = date(myear, mnum, 1).strftime("%B %Y")
        top_areas: dict[str, int] = {}
        for e in month_entries:
            for area in _json.loads(e.policy_areas or "[]"):
                top_areas[area] = top_areas.get(area, 0) + 1
        computed_areas = [a for a, _ in sorted(top_areas.items(), key=lambda x: -x[1])[:5]]

        llm = _generate_period_summary(
            label=month_name,
            entries=month_entries,
            cache_key={"period": "month", "year": myear, "month": mnum},
            db=db,
        )
        db.add(MonthSummary(
            year=myear,
            month=mnum,
            summary=llm["summary"],
            top_policy_areas=_json.dumps(llm["topAreas"] or computed_areas),
            entry_count=len(month_entries),
        ))
        db.commit()
        logger.info("Generated month-in-review for %s %d", myear, mnum)

    # --- Year summaries ---
    # For each year < current_year that has timeline entries
    past_year_entries = (
        db.query(TimelineEntry)
        .filter(TimelineEntry.date < f"{current_year}-01-01")
        .order_by(TimelineEntry.date)
        .all()
    )
    past_year_entries = without_repeat_leads(past_year_entries)
    past_years: dict[int, list] = {}
    for e in past_year_entries:
        yr = int(e.date[:4])
        past_years.setdefault(yr, []).append(e)

    for yr, year_entries in past_years.items():
        existing = db.query(YearSummary).filter(YearSummary.year == yr).first()
        if existing:
            continue
        top_areas: dict[str, int] = {}
        for e in year_entries:
            for area in _json.loads(e.policy_areas or "[]"):
                top_areas[area] = top_areas.get(area, 0) + 1
        computed_areas = [a for a, _ in sorted(top_areas.items(), key=lambda x: -x[1])[:5]]
        llm = _generate_period_summary(
            label=str(yr),
            entries=year_entries,
            cache_key={"period": "year", "year": yr},
            db=db,
        )
        db.add(YearSummary(
            year=yr,
            summary=llm["summary"],
            top_policy_areas=_json.dumps(llm["topAreas"] or computed_areas),
            entry_count=len(year_entries),
        ))
        db.commit()
        logger.info("Generated year-in-review for %d", yr)


def _build_full_story(
    filtered_cluster: list, headline_claims: list,
    summary: str, facts: list[str], source_text: str, locate,
) -> str | None:
    """The issue page's full story, from verified claims only (see
    claims.build_story). Each article is asked once more, about its body,
    so the story carries what the headline claims do not; the same topic
    check and the same backstop as the issue's own text then apply, and a
    story failing either is not published (fail closed, like the issue)."""
    body_claims = claim_layer.extract_body_claims(filtered_cluster, locate)
    claims = claim_layer.distinct_claims(claim_layer.on_topic(
        claim_layer.dedupe_claims(headline_claims + body_claims), filtered_cluster,
    ))
    story = claim_layer.build_story(claims, [summary, *facts])
    if story is None:
        return None
    reasons = grounding_violations(story, source_text)
    reasons += hedge_and_editorializing_violations(story)
    if reasons:
        logger.warning("Full story failed the backstop (%s) — none published", "; ".join(reasons)[:200])
        action_metrics.increment("stories_skipped_grounding")
        return None
    return story


def _save_timeline_entry(today: str, db: Session) -> None:
    """Preserve today's #1 issue as a permanent timeline entry."""
    from app.models import TimelineEntry

    top_issue = (
        db.query(ActionIssue)
        .filter(ActionIssue.date == today, ActionIssue.rank == 1,
                ActionIssue.is_current == True)  # noqa: E712
        .first()
    )
    if not top_issue:
        return

    source_urls = json.loads(top_issue.source_urls or "[]")
    source_names = json.loads(top_issue.source_names or "[]")
    policy_areas = top_issue.policy_areas or "[]"
    monitor_slugs = json.loads(
        getattr(top_issue, "related_monitor_slugs", "[]") or "[]"
    )

    existing = db.query(TimelineEntry).filter(TimelineEntry.date == today).first()
    # A story that leads two days running is one entry, on the day it first
    # led: the year in review listed three stories twice in a row
    # (2026-10-01..08), each time under the same source.
    lead_url = source_urls[0] if source_urls else None
    before = (
        db.query(TimelineEntry).filter(TimelineEntry.date < today)
        .order_by(TimelineEntry.date.desc()).first()
    )
    if lead_url and before is not None and before.source_url == lead_url:
        if existing:
            db.delete(existing)
            db.commit()
        return
    if existing:
        existing.title = top_issue.title
        existing.summary = top_issue.summary[:500]
        existing.policy_areas = policy_areas
        existing.source_url = source_urls[0] if source_urls else None
        existing.source_name = source_names[0] if source_names else None
        existing.monitor_slug = monitor_slugs[0] if monitor_slugs else None
    else:
        db.add(TimelineEntry(
            date=today,
            title=top_issue.title,
            summary=top_issue.summary[:500],
            policy_areas=policy_areas,
            source_url=source_urls[0] if source_urls else None,
            source_name=source_names[0] if source_names else None,
            monitor_slug=monitor_slugs[0] if monitor_slugs else None,
        ))
    db.commit()
    logger.info("Timeline entry saved for %s: %s", today, top_issue.title[:60])


# Measured 2026-10-08 on the 453 updates the one monitor then held (each
# labelled on- or off-topic by what it names): issue title against the
# monitor's title and description on the classification model (get_embedding_model, the model this gate
# scores with; a replay on it reproduces the measured counts), the value that
# misclassifies fewest. The floor it replaced (0.70, calibrated against the
# LLM gate, which approved 81 of 88) sat below most off-topic updates, and
# a title-only floor of 0.62 was below the off-topic median (0.78).
#
# This floor is the whole gate. Matches between it and 0.80 used to go to an
# LLM yes/no verdict, removed 2026-10-09 because it rejected almost nothing.
# Replayed offline on the production model, quantization, prompt and
# decoding against the monitor's 330 hand-labelled updates, it said yes to
# 323, and in the band let through 17 of 18 off-topic updates (1 of 9
# rejected after 2026-07-15). Nothing tried in its place separated the two
# without rejecting on-topic updates too: asking for the article's main
# subject, the monitor's description and recent updates as context, a bare
# constrained true/false, reason before verdict, a two-step subject check,
# multiple choice, majority or unanimity over 5 samples, the true/false
# log-probability margin, and the model's extracted subject phrase scored on
# this model against the description and as kNN over earlier updates.
_MONITOR_ISSUE_SIM = 0.71
# Merging deletes the absorbed monitor, so it needs certainty. On the
# similarity model's title similarity no two updates about different topics
# (Iran, Canada trade, Korea drills, Ukraine, Gaza, a Saudi deal) reached
# 0.75 (the highest was 0.746). The retrieval-model floor of 0.55 this
# replaced merged every pair of different topics: the site had one monitor,
# "U.S.-Iran Conflict", holding the Canada, Korea and Ukraine stories.
_MONITOR_MERGE_TITLE_SIM = 0.75
# The second half of the issue gate: an issue joins a monitor only if it also
# names something the monitor's own text or updates name, and that something
# is not in most stories. Inverse document frequency over every stored
# issue's entity signature; a shared entity must score above this. Measured
# 2026-10-14 on the 312 stored updates (the two live monitors, each read
# on- or off-topic): the cosine floor alone let 3 of the 4 updates after the
# Russian-diesel monitor opened in (a Trump rally in Alaska, the Supreme
# Court's DOGE records ruling, taxpayer-funded ads), each sharing only
# "trump" with it, the one entity in more than 7% of issues (32%, IDF 1.15;
# next "iran", 6.9%, IDF 2.67). Any floor from 1.2 to 2.6 dropped all 3 and
# none of the 291 on-topic Iran updates; 2.0 sits mid-gap. Comparing against
# the monitor's founding issues only, instead of everything it has gathered,
# lost 22 on-topic Iran updates (the Strait of Hormuz stories came later);
# a centroid-of-founding cosine floor lost 4.5% of on-topic Iran updates
# before it dropped the diesel three (0.84: 1 of 3 kept, recall 0.955).
_MONITOR_ENTITY_MIN_IDF = 2.0
# Below this many stored issues an entity's document frequency can't tell a
# common name from a rare one (every name is in "most" of three issues), so
# the entity half of the gate waits and the cosine floor decides alone. A
# monitor needs five days of issues to exist at all; production held 551 on
# 2026-10-14.
_MONITOR_ENTITY_MIN_CORPUS = 100
# A new monitor needs today's issue to match earlier issues, title against
# title on the classification model, at this floor. Measured 2026-10-09 by
# replaying creation day by day over every stored issue (2026-06-26 to
# 10-09), each hand-assigned to a story, with no monitor existing at the
# start. A story counts as long-running when its issues span weeks: Iran,
# the 2026 midterms, AI policy, federal funding, immigration enforcement,
# tariffs and trade, Russia-Ukraine. Tuned on the issues to 2026-08-31:
# 0.83 (inherited, never measured) created 5 monitors, 4 of them a real
# long-running story, and back-filled 11 updates about other stories into
# them; 0.85 created 3, all real, back-filling 4. Held out (2026-09-01 on):
# 2 of 3 real at both floors, 4 off-topic back-fills against 6. A pair of
# different stories reaches 0.85 1 time in 300; the same story's issues,
# 1 in 4. Nothing tried found more of the long-running stories without
# also admitting mixed ones (other floors, the similarity model, title and
# summary, fewer or more days).
_MONITOR_HISTORY_SIM = 0.85
# Distinct days among the matching issues' `date`s, plus today. An issue
# keeps one row while its story continues, and `date` moves to the latest
# day, so this counts separate stories on separate days, not days on the
# board: counting every day each matching row was current let one story
# carried for a week open a monitor, and on the same replay took precision
# from 4 in 5 to 6 in 16.
_MONITOR_MIN_DAYS = 5
_MONITOR_MIN_UNIQUE_SOURCES = 3
_MONITOR_LOOKBACK_DAYS = 14
# Over the same history the long-running stories went up to 28 days between
# issues (8 to 28 days, 22 times across the seven) and then came back; none
# went more than 30. So a week without one only marks a monitor "watching"
# (still listed, and the next matching issue makes it active again), and
# closing waits for 30 days.
_MONITOR_DORMANT_DAYS = 7
_MONITOR_CLOSE_DAYS = 30
_MONITOR_MIN_UPDATES_FOR_ARCHIVE = 3


# No example title, no verdict, no category. The version before 2026-10-09
# gave "U.S.-Iran Conflict" as an example, and replayed on the production
# model against the five monitors creation would have opened and 18
# labelled story sets, it named 18 of the 23 "U.S.-Iran Conflict" (AI
# policy, beef prices, a Senate contempt vote...). Nothing checked the
# title against the articles, so each was created under the existing
# monitor's title and merged into it the same run (_merge_similar_monitors:
# identical titles), which is how the one monitor came to hold the Canada,
# Korea, Ukraine and Gaza stories. Its "is_significant" verdict said yes to
# all 23, the 13 that were not a long-running story included, and its category was
# FOREIGN_POLICY for 16. This version named none of them after Iran, and
# every title is still checked against the articles (_ungrounded_title_words).
_MONITOR_METADATA_PROMPT = """\
Below are recent news articles that keep returning to one ongoing subject.

Articles:
{articles}

Respond with a JSON object:
- "title": a short, neutral name (three to six words) for the ongoing subject these articles share. Use only names of people, places and organizations that appear in the articles.
- "description": a factual summary of the ongoing situation in two or three sentences, using only facts stated in the articles.

Respond with ONLY the JSON object."""


def _ungrounded_title_words(title: str, source: str) -> list[str]:
    """Capitalized words of a generated monitor title that appear nowhere in
    its source articles. On the 2026-10-09 replay it turned back 22 of the
    old prompt's 23 titles (every copied "U.S.-Iran Conflict", and each
    "National Monitor: ...") and kept the one real Iran title; on the
    current prompt it turned back 3 of 23, each for a generic
    word ("Debate", "Crisis", "Case"), which falls back to the issue's own
    title. grounding_violations doesn't look at place or topic names. Kept
    here, not in grounding.py, because grounding.py is part of the analysis
    fingerprint and this module is not."""
    words = set(re.findall(r"[a-z0-9]+", source.lower()))
    tokens = {
        _POSSESSIVE_SUFFIX_RE.sub("", m.group(0)).lower()
        for m in re.finditer(r"\b[A-Z][a-zA-Z'’\-]{2,}\b", title.translate(_APOSTROPHES))
    }
    return sorted(
        t for t in tokens
        if not all(part in words for part in re.findall(r"[a-z0-9]+", t))
    )


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug)
    return slug[:200]


def _entity_document_frequency(db: Session) -> tuple[Counter, int]:
    """How many stored issues each entity (_issue_signature, title and
    summary) appears in, and how many issues there are."""
    counts: Counter = Counter()
    n = 0
    for title, summary in db.query(ActionIssue.title, ActionIssue.summary):
        counts.update(_issue_signature(title or "", [summary or ""]))
        n += 1
    return counts, n


def _monitor_reference(monitor: NationalMonitor, db: Session) -> set[str]:
    """The entities a monitor's own title and description and every update
    it holds name: what a new issue must share one of to join it."""
    ref = _issue_signature(monitor.title or "", [monitor.description or ""])
    for title, summary in db.query(MonitorUpdate.article_title, MonitorUpdate.summary).filter(
        MonitorUpdate.monitor_id == monitor.id,
    ):
        ref |= _issue_signature(title or "", [summary or ""])
    return ref


def _same_entity(a: str, b: str) -> bool:
    """Equal, or one a prefix of the other at four letters or more: "iran"
    and "iranian", "russia" and "russian" (a demonym is the same place)."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def _shares_distinctive_entity(
    entities: set[str], reference: set[str], df: Counter, n_docs: int,
) -> bool:
    """Whether `entities` name something in `reference` that is in few
    enough stored issues to tell stories apart (_MONITOR_ENTITY_MIN_IDF).
    Numbers don't count: a figure shared across stories is a coincidence.
    True for every issue while the corpus is too small to say
    (_MONITOR_ENTITY_MIN_CORPUS)."""
    if n_docs < _MONITOR_ENTITY_MIN_CORPUS:
        return True
    return any(
        math.log(n_docs / (1 + df[e])) > _MONITOR_ENTITY_MIN_IDF
        for e in entities
        if not e[0].isdigit() and any(_same_entity(e, r) for r in reference)
    )


# Updates a monitor was opened with are written in one commit; anything
# later than this after the first was matched by the issue gate.
_MONITOR_FOUNDING_WINDOW = timedelta(minutes=1)


def _prune_off_topic_updates(db: Session) -> int:
    """Apply the entity half of the issue gate to the updates a monitor
    already holds, oldest first, as if it had been there all along: an update
    that shares no distinctive entity with the monitor's text and the updates
    before it is removed, and the issue it came from stops naming the
    monitor. The updates it was opened with stay. On 2026-10-14 this removed
    the Russian-diesel monitor's three off-topic updates and none of the Iran
    monitor's 303. Returns how many went."""
    monitors = db.query(NationalMonitor).all()
    if not monitors:
        return 0
    df, n_docs = _entity_document_frequency(db)
    removed = 0
    for monitor in monitors:
        updates = (
            db.query(MonitorUpdate).filter(MonitorUpdate.monitor_id == monitor.id)
            .order_by(MonitorUpdate.created_at, MonitorUpdate.id).all()
        )
        if not updates:
            continue
        founded = updates[0].created_at + _MONITOR_FOUNDING_WINDOW
        reference = _issue_signature(monitor.title or "", [monitor.description or ""])
        for u in updates:
            entities = _issue_signature(u.article_title or "", [u.summary or ""])
            if u.created_at > founded and not _shares_distinctive_entity(entities, reference, df, n_docs):
                logger.info("Monitor '%s': removed off-topic update '%s'", monitor.title, u.article_title[:60])
                for issue in db.query(ActionIssue).filter(
                    ActionIssue.title == u.article_title,
                    ActionIssue.related_monitor_slugs.contains(monitor.slug),
                ):
                    slugs = [s for s in json.loads(issue.related_monitor_slugs or "[]") if s != monitor.slug]
                    issue.related_monitor_slugs = json.dumps(slugs)
                db.delete(u)
                removed += 1
                continue
            reference |= entities
    if removed:
        db.flush()
        action_metrics.increment("monitor_updates_pruned", removed)
    return removed


def _merge_monitors(keep: NationalMonitor, absorb: NationalMonitor,
                    db: Session) -> None:
    """Merge two monitors: move updates from `absorb` into `keep`, delete `absorb`."""
    existing_keys = {
        (u.date, u.source_url)
        for u in db.query(MonitorUpdate).filter(
            MonitorUpdate.monitor_id == keep.id
        ).all()
    }

    for u in db.query(MonitorUpdate).filter(
        MonitorUpdate.monitor_id == absorb.id
    ).all():
        if (u.date, u.source_url) not in existing_keys:
            u.monitor_id = keep.id
        else:
            db.delete(u)

    if absorb.last_article_date and (
        not keep.last_article_date or absorb.last_article_date > keep.last_article_date
    ):
        keep.last_article_date = absorb.last_article_date

    keep_areas = set(json.loads(keep.policy_areas or "[]"))
    absorb_areas = set(json.loads(absorb.policy_areas or "[]"))
    keep.policy_areas = json.dumps(sorted(keep_areas | absorb_areas))

    logger.info("Merged monitor '%s' into '%s'", absorb.title, keep.title)
    db.flush()
    db.query(MonitorUpdate).filter(MonitorUpdate.monitor_id == absorb.id).delete(synchronize_session=False)
    db.delete(absorb)


def _sweep_orphan_updates(db: Session) -> int:
    """Delete updates of a monitor that no longer exists. SQLite doesn't
    enforce the foreign key, and a deleted monitor's updates were left
    behind (52 rows from six monitors, 2026-10-08). Run every refresh."""
    live = [m.id for m in db.query(NationalMonitor).all()]
    swept = db.query(MonitorUpdate).filter(
        ~MonitorUpdate.monitor_id.in_(live),
    ).delete(synchronize_session=False)
    if swept:
        logger.info("Removed %d updates of monitors that no longer exist", swept)
    return swept


def _merge_similar_monitors(monitors: list[NationalMonitor], db: Session) -> bool:
    """Merge monitors whose titles are near-identical on the similarity
    model (_MONITOR_MERGE_TITLE_SIM). Returns True if anything merged, so
    the caller knows to commit.

    _update_national_monitors calls this twice — once for monitors that
    existed before today's new ones are created, once again afterward to
    catch newly-created near-duplicates.
    """
    if len(monitors) < 2:
        return False

    mon_title_embs = np.array(_embed_texts_sim([m.title for m in monitors]))
    merged_ids: set[int] = set()
    for a_idx in range(len(monitors)):
        if monitors[a_idx].id in merged_ids:
            continue
        for b_idx in range(a_idx + 1, len(monitors)):
            if monitors[b_idx].id in merged_ids:
                continue
            if float(mon_title_embs[a_idx] @ mon_title_embs[b_idx]) < _MONITOR_MERGE_TITLE_SIM:
                continue
            keep = monitors[a_idx]
            absorb = monitors[b_idx]
            if len(keep.updates or []) < len(absorb.updates or []):
                keep, absorb = absorb, keep
            _merge_monitors(keep, absorb, db)
            merged_ids.add(absorb.id)
            if absorb is monitors[a_idx]:
                # The swap deleted the OUTER monitor: stop pairing
                # against it. Continuing the inner loop used a deleted
                # row as a merge target — a later match could re-parent
                # a third monitor's updates onto the deleted parent and
                # destroy them via the delete-orphan cascade. (The
                # merged_ids guard only runs at the top of the outer
                # loop.)
                break
    return bool(merged_ids)


def _monitor_article_lines(entries: list[tuple[str, str, str]]) -> list[str]:
    """The articles a monitor's name is written from and checked against:
    (title, source, summary) triples, first 15, one per title."""
    seen: set[str] = set()
    lines: list[str] = []
    for title, source, summary in entries[:15]:
        if title in seen:
            continue
        seen.add(title)
        source_str = f" [{source}]" if source else ""
        lines.append(f"{title}{source_str}\n  {(summary or '')[:200]}")
    return lines


def _issue_article_entry(issue: ActionIssue) -> tuple[str, str, str]:
    sources = json.loads(issue.source_names or "[]")
    return issue.title, sources[0] if sources else "", issue.summary or ""


def _generate_monitor_metadata(articles: list[str], db: Session) -> dict | None:
    """A monitor's title and description, written by the LLM from
    `articles` (_monitor_article_lines) and checked against them, or None
    when the model gave nothing usable.

    The model only writes; it decides nothing. Whether a monitor opens is
    settled before this is called (the day, source and similarity floors),
    and the category is the commonest policy area of its issues, which the
    embedding classifier already gave each one
    (_classify_issue_policy_areas). The model's own verdict and category
    were removed 2026-10-09: see _MONITOR_METADATA_PROMPT.

    None is not "no monitor": the caller names a new monitor after its
    most representative headline (_representative_issue) and a later run
    asks again (_rename_headline_monitors).
    """
    titles = sorted(a.split("\n", 1)[0] for a in articles)
    result = call_llm(
        prompt_version="monitor-metadata-v2",
        system_prompt="You are a civic data analyst. Respond in JSON.",
        user_prompt=_MONITOR_METADATA_PROMPT.format(articles="\n\n".join(articles)),
        cache_key={"type": "monitor_metadata", "titles": titles[:5]},
        db_session=db,
        max_tokens=1024,
    )
    if isinstance(result, str):
        result = extract_json(result)
    if not isinstance(result, dict):
        return None

    title = str(result.get("title") or "")[:500]
    description = str(result.get("description") or "")[:1000]
    if not title or not description:
        return None

    # A National Monitor's title and description are published prose and
    # were, until 2026-09-23, generated with no mechanical check at all —
    # the same gap that let "Iran War Ends Quickly to Lower Prices" onto
    # the site from the issue path next door. The articles are this
    # generation's source material, so they are what it gets checked
    # against.
    source_material = "\n\n".join(articles)
    reasons = grounding_violations(f"{title} {description}", source_material)
    reasons += hedge_and_editorializing_violations(f"{title} {description}")
    reasons += [
        f"title states as done what the source only calls for: {', '.join(inverted)}"
        for inverted in [proposal_stated_as_fact(title, source_material)] if inverted
    ]
    reasons += [
        f"title names what the articles don't: {', '.join(missing)}"
        for missing in [_ungrounded_title_words(title, source_material)] if missing
    ]
    if reasons:
        logger.warning("Monitor name failed its checks (%s)", "; ".join(reasons))
        action_metrics.increment("monitor_metadata_ungrounded")
        return None
    return {"title": title, "description": description}


def _representative_issue(issues: list[ActionIssue], embeddings: np.ndarray) -> ActionIssue:
    """The issue whose title sits nearest the centroid of the issues that
    opened a monitor: its fallback name. Until 2026-10-14 the fallback was
    whichever issue happened to cross the day floor, so a monitor on a
    bipartisan senators' letter about a diesel deal opened as "Democratic
    senator talks Trump's Russian diesel deal" — one interview's headline —
    and kept it for its life."""
    centroid = embeddings.mean(axis=0)
    return issues[int(np.argmax(embeddings @ centroid))]


def _rename_headline_monitors(db: Session) -> bool:
    """Ask the model again for a monitor still named after a headline (its
    title is one of its own updates' article titles: the fallback's mark),
    from its updates. The slug stays: it is the monitor's address, and
    links to it are out there. Returns True when anything was renamed."""
    renamed = False
    for monitor in db.query(NationalMonitor).filter(NationalMonitor.status != MonitorStatus.CLOSED).all():
        updates = (
            db.query(MonitorUpdate)
            .filter(MonitorUpdate.monitor_id == monitor.id)
            .order_by(MonitorUpdate.date.desc(), MonitorUpdate.id.desc())
            .all()
        )
        if monitor.title not in {u.article_title for u in updates}:
            continue
        articles = _monitor_article_lines(
            [(u.article_title, u.source_name, u.summary) for u in updates]
        )
        name = _generate_monitor_metadata(articles, db)
        if name is None:
            continue
        logger.info("Monitor renamed: '%s' -> '%s'", monitor.title, name["title"])
        monitor.title = name["title"]
        monitor.description = name["description"]
        action_metrics.increment("monitor_renamed")
        renamed = True
    return renamed


def _update_national_monitors(today: str, db: Session) -> None:
    """Detect recurring topics and create/update national monitors.

    Uses embedding similarity to match today's issues to existing monitors
    and to detect new recurring topics from past days' issues.
    Every monitor update traces to a specific source article — no LLM-generated
    facts, only condensed summaries of sourced articles.

    Every write here is committed, never just flushed, before the next LLM
    call: a flush opens SQLite's one write transaction, and holding it
    across a model call starves every other writer for the call's length —
    the refresh lock's heartbeat included (see _record_generation_sample).
    """
    try:
        model = get_embedding_model()
    except Exception:
        logger.warning("Could not load embedding model for monitors")
        return

    today_issues = (
        db.query(ActionIssue)
        .filter(
            ActionIssue.date == today,
            # A hedged, unconfirmed, single-source draft must not surface
            # publicly via a monitor update before press corroborates it —
            # same reasoning as excluding it from Bluesky/full-story.
            ActionIssue.status != ActionIssueStatus.DEVELOPING,
        )
        .order_by(ActionIssue.rank)
        .all()
    )
    if not today_issues:
        # A quiet day still ages monitors toward watching and closed.
        _cleanup_monitor_lifecycle(today, db)
        return

    existing_monitors = db.query(NationalMonitor).all()

    today_embeddings = model.encode(
        [i.title for i in today_issues],
        normalize_embeddings=True,
    )

    # Committed even when it swept nothing: its DELETE opens the write
    # transaction whatever it matches, and the rename below asks the model.
    # Left open, that held SQLite's write lock through the LLM call on every
    # refresh while a monitor was still named after a headline, and the
    # nightly Senate run and House sponsorship step it ran beside each died
    # on "database is locked" (2026-10-15, 05:26 and 07:23 UTC).
    _sweep_orphan_updates(db)
    db.commit()
    if _prune_off_topic_updates(db):
        db.commit()

    if _rename_headline_monitors(db):
        db.commit()

    # Step 1: Merge any existing monitors that are too similar to each other.
    _set_refresh_state(stage_detail="1/4 dedup")
    if _merge_similar_monitors(existing_monitors, db):
        db.commit()
        existing_monitors = db.query(NationalMonitor).all()

    # Step 2: Match today's issues to existing monitors and add updates
    _set_refresh_state(stage_detail="2/4 matching")
    matched_issues: set[int] = set()
    issue_monitor_slugs: dict[int, list[str]] = {}

    if existing_monitors:
        monitor_embeddings = model.encode(
            [f"{m.title} {m.description}" for m in existing_monitors],
            normalize_embeddings=True,
        )
        sims = today_embeddings @ monitor_embeddings.T
        entity_df, entity_docs = _entity_document_frequency(db)
        references = {m.id: _monitor_reference(m, db) for m in existing_monitors}

        for i, issue in enumerate(today_issues):
            issue_entities = _issue_signature(issue.title, [issue.summary or ""])
            for j, monitor in enumerate(existing_monitors):
                full_sim = float(sims[i][j])
                if full_sim < _MONITOR_ISSUE_SIM:
                    continue
                if not _shares_distinctive_entity(
                    issue_entities, references[monitor.id], entity_df, entity_docs,
                ):
                    action_metrics.increment("monitor_update_no_shared_entity")
                    continue

                issue_monitor_slugs.setdefault(i, []).append(monitor.slug)

                source_urls = json.loads(issue.source_urls or "[]")
                source_names = json.loads(issue.source_names or "[]")
                if not source_urls:
                    matched_issues.add(i)
                    continue

                # Match against ANY of the issue's source URLs, not just the
                # first: source ordering shifts between hourly runs as new
                # articles arrive. And on any date: an issue that stays
                # current for days added the same article again each day
                # (the monitor showed one story on consecutive dates).
                already_exists = (
                    db.query(MonitorUpdate)
                    .filter(
                        MonitorUpdate.monitor_id == monitor.id,
                        MonitorUpdate.source_url.in_(source_urls),
                    )
                    .first()
                )
                if already_exists:
                    matched_issues.add(i)
                    continue

                db.add(MonitorUpdate(
                    monitor_id=monitor.id,
                    date=today,
                    summary=issue.summary[:500],
                    source_url=source_urls[0],
                    source_name=source_names[0] if source_names else "",
                    article_title=issue.title,
                ))
                monitor.last_article_date = today
                monitor.status = MonitorStatus.ACTIVE
                matched_issues.add(i)
                logger.info("Monitor updated: '%s' <- '%s'",
                            monitor.title, issue.title[:60])


    # Tag issues with their related monitor slugs
    for i, issue in enumerate(today_issues):
        slugs = issue_monitor_slugs.get(i, [])
        if slugs:
            issue.related_monitor_slugs = json.dumps(slugs)

    # Step 3: Detect new recurring topics from unmatched issues
    _set_refresh_state(stage_detail="3/4 new topics")
    cutoff_date = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_MONITOR_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    past_issues = (
        db.query(ActionIssue)
        .filter(
            ActionIssue.date >= cutoff_date, ActionIssue.date < today,
            ActionIssue.status != ActionIssueStatus.DEVELOPING,
        )
        .all()
    )

    if past_issues:
        past_embeddings = model.encode(
            [i.title for i in past_issues],
            normalize_embeddings=True,
        )
        sims = today_embeddings @ past_embeddings.T

        all_monitors = db.query(NationalMonitor).all()
        mon_embs = None
        if all_monitors:
            mon_embs = model.encode(
                [f"{m.title} {m.description}" for m in all_monitors],
                normalize_embeddings=True,
            )

        for i, issue in enumerate(today_issues):
            if existing_monitors and i in matched_issues:
                continue

            matched_dates = {today}
            matched_past: list[ActionIssue] = []
            for j, past_issue in enumerate(past_issues):
                if sims[i][j] >= _MONITOR_HISTORY_SIM:
                    matched_dates.add(past_issue.date)
                    matched_past.append(past_issue)

            if len(matched_dates) < _MONITOR_MIN_DAYS:
                continue

            # Ensure the topic has breadth — if only 1 source (e.g. only AP) 
            # covered it over 5 days, it's persistent but likely too niche
            # for a dedicated National Monitor.
            unique_sources = set(json.loads(issue.source_names or "[]"))
            for p_issue in matched_past:
                unique_sources.update(json.loads(p_issue.source_names or "[]"))

            if len(unique_sources) < _MONITOR_MIN_UNIQUE_SOURCES:
                logger.info(
                    "Monitor creation skipped for '%s': insufficient breadth "
                    "(%d sources over %d days)",
                    issue.title[:50], len(unique_sources), len(matched_dates),
                )
                continue

            if mon_embs is not None:
                dup_sims = today_embeddings[i] @ mon_embs.T
                if float(dup_sims.max()) >= _MONITOR_ISSUE_SIM:
                    continue

            opened_by = [issue] + matched_past
            areas = Counter(
                a for i in opened_by for a in json.loads(i.policy_areas or "[]")
            ).most_common(1)
            category = areas[0][0].lower() if areas else "general"
            metadata = _generate_monitor_metadata(
                _monitor_article_lines([_issue_article_entry(i) for i in opened_by]), db,
            )
            if metadata is None:
                past_index = {id(p): j for j, p in enumerate(past_issues)}
                representative = _representative_issue(
                    opened_by,
                    np.vstack([today_embeddings[i]] + [past_embeddings[past_index[id(p)]] for p in matched_past]),
                )
                metadata = {"title": representative.title[:500],
                            "description": (representative.summary or "")[:1000]}
            metadata["category"] = category
            slug = _slugify(metadata["title"])

            # Ensure unique slug. `int(time.time()) % 1000` (previous
            # suffix) wasn't actually unique: two monitors created in the
            # same wall-clock second — plausible in this tight loop — got
            # the same suffix and the second's write below raised
            # UNIQUE constraint failed: national_monitors.slug, which
            # propagated out of _run_refresh uncaught (see 2026-07-27 fix
            # in refresh_action_issues). uuid4 makes the collision
            # astronomically unlikely instead of merely second-scale.
            existing_slug = db.query(NationalMonitor).filter(NationalMonitor.slug == slug).first()
            if existing_slug:
                slug = f"{slug}-{uuid.uuid4().hex[:6]}"

            monitor = NationalMonitor(
                slug=slug,
                title=metadata["title"],
                description=metadata["description"],
                category=metadata["category"],
                status=MonitorStatus.ACTIVE,
                policy_areas=json.dumps(
                    [] if metadata["category"] == "general" else [metadata["category"].upper()]
                ),
                last_article_date=today,
            )
            db.add(monitor)
            db.commit()

            seen_sources: set[str] = set()
            source_urls = json.loads(issue.source_urls or "[]")
            source_names = json.loads(issue.source_names or "[]")

            for past_issue in matched_past:
                p_urls = json.loads(past_issue.source_urls or "[]")
                p_names = json.loads(past_issue.source_names or "[]")
                if p_urls and p_urls[0] not in seen_sources:
                    db.add(MonitorUpdate(
                        monitor_id=monitor.id,
                        date=past_issue.date,
                        summary=past_issue.summary[:500],
                        source_url=p_urls[0],
                        source_name=p_names[0] if p_names else "",
                        article_title=past_issue.title,
                    ))
                    seen_sources.add(p_urls[0])

            if source_urls and source_urls[0] not in seen_sources:
                db.add(MonitorUpdate(
                    monitor_id=monitor.id,
                    date=today,
                    summary=issue.summary[:500],
                    source_url=source_urls[0],
                    source_name=source_names[0] if source_names else "",
                    article_title=issue.title,
                ))
            db.commit()

            # A second issue on this topic later in the same run is a
            # duplicate of the monitor just opened, not a new one.
            new_emb = model.encode(
                [f"{monitor.title} {monitor.description}"], normalize_embeddings=True,
            )
            mon_embs = new_emb if mon_embs is None else np.vstack([mon_embs, new_emb])

            logger.info("New monitor created: '%s' (%d days)",
                        monitor.title, len(matched_dates))

    # Step 3b: Re-merge after creating new monitors.
    all_monitors = db.query(NationalMonitor).all()
    if _merge_similar_monitors(all_monitors, db):
        db.commit()

    # Step 4: Lifecycle management — watching, closing, and cleaning up
    _set_refresh_state(stage_detail="4/4 lifecycle")
    _cleanup_monitor_lifecycle(today, db)

    db.commit()


def _cleanup_monitor_lifecycle(today: str, db: Session) -> None:
    """Close inactive monitors and delete insignificant ones."""
    dormant_cutoff = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_MONITOR_DORMANT_DAYS)
    ).strftime("%Y-%m-%d")
    close_cutoff = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_MONITOR_CLOSE_DAYS)
    ).strftime("%Y-%m-%d")

    all_monitors = db.query(NationalMonitor).filter(NationalMonitor.status != MonitorStatus.CLOSED).all()
    
    for m in all_monitors:
        # Use created_at if last_article_date is missing (e.g. newly created)
        last_date = m.last_article_date or eastern_date(m.created_at).isoformat()
        update_count = len(m.updates or [])

        # 1. Close monitors inactive for >30 days
        if last_date < close_cutoff:
            # If it never gained enough updates to be considered historically 
            # significant, just delete it to keep the database clean.
            if update_count < _MONITOR_MIN_UPDATES_FOR_ARCHIVE:
                logger.info("Deleting insignificant monitor: '%s' (%d updates)", 
                            m.title, update_count)
                # By query, not the relationship's cascade: rows added by
                # monitor_id after the collection loaded aren't in it.
                db.query(MonitorUpdate).filter(MonitorUpdate.monitor_id == m.id).delete(synchronize_session=False)
                db.delete(m)
            else:
                m.status = MonitorStatus.CLOSED
                logger.info("Closing monitor due to inactivity: '%s'", m.title)

        # 2. Mark active monitors as "watching" if inactive for >7 days
        elif m.status == MonitorStatus.ACTIVE and last_date < dormant_cutoff:
            m.status = MonitorStatus.WATCHING
            logger.info("Monitor set to watching: '%s'", m.title)

    db.commit()






# The refresh lock is a lease (app.pipeline.lease), not a flag. It used to be
# honored for a flat 4 hours from acquisition, and a refresh runs in a
# thread, so a deploy's SIGTERM kills it without its `finally` and leaves the
# row behind: every hourly run for the next four hours then skipped with
# "held by another container" — on a day of steady deploys (2026-09-26: 22
# between 15:08 and 22:50 UTC) that meant no refresh at all, and no new
# issue, for the whole day. It was added (2026-07) because a process-local
# guard let two containers both run the refresh during a blue/green overlap:
# duplicate Bluesky posts and contended SQLite writes.


def refresh_action_issues(db: Session | None = None) -> int:
    """Run the full action center pipeline. Returns number of issues created."""
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        with lease.holding(db, lease.ACTION_REFRESH, yield_to=lease.DATA_RESET) as token:
            if token is None:
                code, why = lease.code_and_refusal(db, lease.ACTION_REFRESH)
                # A reset (or a dead one's lease) holding the refresh off is
                # worth a warning; another container's refresh is routine.
                logger.log(
                    logging.WARNING if code == lease.REFUSED_BY_RESET else logging.INFO,
                    "Action refresh skipped: %s", why,
                )
                return 0
            try:
                return _run_refresh(db)
            except Exception:
                # _run_refresh only clears the in-memory is_running flag on its
                # own explicit return paths. An exception mid-run (e.g. a DB
                # IntegrityError) skips all of those, leaving is_running stuck
                # true — which then blocks every future hourly refresh (see the
                # 4h staleness override above _hourly_action_refresh) and, via
                # /api/admin/pipeline/status, wedges check-and-deploy.sh's busy
                # check indefinitely (found 2026-07-27: a national_monitors.slug
                # collision wedged this for 5+ hours and blocked same-day
                # deploys). Clear it here unconditionally so a crash degrades to
                # "skipped this hour," not "stuck until a container restart."
                _set_refresh_state(is_running=False, stage=None)
                raise
    finally:
        if own_session:
            db.close()


# An election-night DEVELOPING issue (app/live_results/signals.py, source
# type "election_results") is about one race: a seat changing party in the
# live count. Its title and facts are a fixed template — a state, "District",
# a party, vote figures — so signature overlap alone matched news about a
# DIFFERENT race in the same state (a story on GA-6's flip promoting GA-2's
# issue, which the sync then stops updating for good). A news story may
# promote one only when it names that race. This is identity matching on a
# structured identifier (the race id in the issue's own action link), the
# way a bill number is resolved — not a classification.
_ELECTION_RESULTS_SOURCE = "election_results"
_RACE_LINK_RE = re.compile(r"#race-(\d{4})-(SEN|HOUSE)-([A-Z]{2})(?:-(\d+|SPECIAL))?$")


# A story names a race only when the state and the seat appear together in
# one phrase. Testing them independently let a roundup ("Georgia's 6th and
# Florida's 2nd districts") name GA-2, a dateline ("WASHINGTON (AP)") name
# any Washington seat, and "West Virginia" name Virginia. Every state name
# in the story is read longest-first (so "west virginia" is one name, never
# "virginia") and replaced by a marker — _OURS for the issue's state, _OTHER
# for any other — before the phrase patterns below run.
_OURS, _OTHER = "\x01", "\x02"
_POSS = r"(?:['’]s?)?"  # Georgia's / Texas' / bare
# The state as the owner of a seat: "Georgia's", "Texas'", bare "Georgia",
# or "Washington state's" (the apostrophe is required after "state" so
# "Georgia state Senate" never reads as "Georgia's Senate").
_OWNER = rf"{_OURS}(?:\s+state['’]s?|{_POSS})"
_ORD = r"\d+(?:st|nd|rd|th)"
# After a trailing state name, what makes the phrase about the state's
# LEGISLATURE ("the 2nd district in Georgia's state Senate", "the 2nd
# District in Georgia Senate"), or about a district or numbered seat INSIDE
# the state ("the Senate race in Ohio District 5", "… in Georgia House
# district 14"): a U.S. race's own phrase ends at the state.
_NOT_LEGISLATURE = r"(?!(?:['’]s?)?\s+(?:state[-\s]+)?(?:senate|house|assembly|legislature)\b)"
_NO_PLACE_AFTER = r"(?!\s+(?:(?:state|congressional|legislative|senate|house)\s+)*(?:district\b|#?\d))"
# A state named last: never possessive ("… in Georgia's legislature"), never
# followed by a legislature or a district of its own.
_OURS_LAST = rf"{_OURS}(?!['’]){_NOT_LEGISLATURE}{_NO_PLACE_AFTER}"
# After "[State]'s 2nd District" / "[State]'s District 2": a legislature
# seat's own words ("Michigan's 2nd District state House seat", "Georgia
# state's District 14 senate"). A congressional district is never followed
# by "state" or "Senate"; "House" alone is left alone, since "Georgia's 2nd
# District House race" is the congressional one.
_NOT_LEGISLATURE_AFTER_DISTRICT = r"(?!\s+(?:state|senate)\b)"
_HOUSE_PHRASES = (
    # "Georgia's 2nd District", "Georgia's 2nd and 6th congressional districts"
    rf"{_OWNER}\s+(?P<ords>{_ORD}(?:(?:\s*,\s*|\s+and\s+|\s*,\s*and\s+){_ORD})*)"
    r"(?:\s+congressional)?\s+districts?\b" + _NOT_LEGISLATURE_AFTER_DISTRICT,
    # "Georgia's District 2", "Georgia congressional district 2". Bare
    # "Georgia District 2" is also how a commission or school board names
    # its seats ("Georgia District 2 commissioner race"), so without the
    # possessive it needs "congressional".
    rf"(?:{_OURS}(?:\s+state)?['’]s?\s+(?:congressional\s+)?|{_OURS}\s+congressional\s+)"
    r"district\s+(?P<num>\d+)\b" + _NOT_LEGISLATURE_AFTER_DISTRICT,
    # "the 2nd congressional district of Georgia", "2nd District in Georgia"
    # — never "… in Georgia's" something ("the second district in
    # Georgia's school board").
    rf"\b(?P<ord>{_ORD})\s+(?:congressional\s+)?district\s+(?:of|in)\s+{_OURS_LAST}",
    # "Virginia's 2nd seat", "Virginia's 2nd House race", and "Virginia's
    # 2nd" closing a clause ("…leads in Virginia's 2nd."). A bare ordinal
    # followed by any other word is not read as a district: "Virginia's
    # 2nd largest city", "Georgia's 3rd straight win" and a headline verb
    # ("Virginia's 2nd flips") can't be told apart without a word list, so
    # that headline form is left to the story's other text.
    rf"{_OURS}['’]s?\s+(?P<bare>{_ORD})(?:\s+(?:congressional\s+|house\s+)?(?:seat|race|contest)\b|\s*(?:[.;:!?)]|$))",
    # "In California's 45th, Tran pulls ahead": a comma closes the phrase
    # too, but only after "in" — "Georgia's 3rd, and final, win" is not a
    # place. Spelled-out ordinals never reach these two bare forms (they
    # are rewritten only before a district/seat word, below), so "It was
    # Georgia's first." names no district.
    rf"\bin\s+{_OURS}['’]s?\s+(?P<bare>{_ORD})\s*,",
)
_AT_LARGE_PHRASES = (
    # Never "Alaska's at-large state Senate seat" / "… Assembly seat".
    rf"{_OWNER}\s+at[- ]large\b(?!\s+(?:state|senate|assembly)\b)",
    r"\bat[- ]large\s+(?:congressional\s+)?(?:district|seat|race|contest)\s+(?:of|in)\s+" + _OURS_LAST,
    # "Alaska's lone House seat", "Alaska's only congressional seat": a
    # state with one seat has only its at-large one. "House" right after
    # the adjective, never "state House" (a legislature has many seats).
    rf"{_OWNER}\s+(?:lone|only|sole|single)\s+(?:u\.?s\.?\s+)?(?:congressional|house)\s+"
    r"(?:seat|district|race|contest)\b",
    r"\b(?:lone|only|sole|single)\s+(?:u\.?s\.?\s+)?(?:congressional|house)\s+(?:seat|district|race|contest)"
    r"\s+(?:of|in)\s+" + _OURS_LAST,
)
# A legislature's district read as a House seat: "the state-senate race in
# Georgia's 14th district" is a state senate district, not GA-14.
_STATE_LEGISLATURE_LEAD = re.compile(
    r"\bstate[-\s]+(?:senate|house|legislative|assembly)\s+"
    r"(?:(?:race|seat|contest|election|district)s?\s+)?(?:in|for|of)\s+(?:the\s+)?$"
)
# "Senate" only with a race word or "U.S." beside it, and never "state
# Senate" (spaced or hyphenated): a legislature's upper chamber is a
# different contest. "special" and "U.S." may come in either order around
# it ("the special U.S. Senate election", "Georgia's Senate special
# election", "Georgia's special election for U.S. Senate"). A trailing
# state is never followed by a possessive: "the Senate race for Georgia's
# governor" and "the Senate race in Georgia's 14th district" are about
# something of Georgia's, not Georgia's U.S. Senate seat.
_RACE_WORD = r"(?:race|seat|contest|election|runoff)"
_NOT_STATE_SENATE = r"(?<!state\s)(?<!state-)"
_SPECIAL_FOR = r"special\s+(?:election|race|contest)\s+for\s+(?:the\s+|a\s+)?(?:u\.?s\.?\s+)?senate(?:\s+seat)?"
_SENATE_PHRASES = (
    rf"{_OWNER}\s+(?:(?:special|regular)\s+)?(?:u\.?s\.?\s+)?senate\s+(?:special\s+)?{_RACE_WORD}\b",
    rf"{_OWNER}\s+(?:special\s+)?u\.?s\.?\s+senate\b",
    rf"{_OWNER}\s+{_SPECIAL_FOR}\b",
    rf"(?:\b(?:special|regular)\s+)?(?:\bu\.?s\.?\s+)?{_NOT_STATE_SENATE}\bsenate\s+(?:special\s+)?{_RACE_WORD}\s+"
    rf"(?:in|for|from)\s+{_OURS_LAST}",
    rf"(?:\bspecial\s+)?\bu\.?s\.?\s+senate\s+(?:in|for|from)\s+{_OURS_LAST}",
    rf"\b{_SPECIAL_FOR}\s+(?:race\s+)?(?:in|for|from)\s+{_OURS_LAST}",
)
_SENATE_OWNER_PHRASES = 3  # the first three start with the state as owner
# "[State] Senate" is also the name of the state legislature's upper
# chamber, which elects its members the same night ("Democrat flips Ohio
# Senate seat in Dayton-area district", "Ohio Senate election results: GOP
# holds 24 seats"). Two rules keep that from naming the U.S. Senate race:
#
# - "seat" and "election" (words a legislature's own contests share) count
#   only with "U.S.", "special" or a possessive owner ("Ohio's Senate
#   seat"); bare "Ohio Senate race" still names the race.
# - Without "U.S.", a phrase followed by a seat number or a place inside
#   the state is a legislative district: "Ohio Senate seat 5", "Georgia
#   Senate race for District 14", "… seat in Tampa", "… race for Georgia's
#   governor". Only the state itself, a year or a month may follow "in" /
#   "for" / "from" (months are the calendar's own names, not a word list
#   classifying anything). Punctuation between the phrase and the number
#   changes nothing — "Ohio Senate race, District 5", "(District 5)",
#   "- District 5", ": District 5 flips", "— SD 14", "No. 5": a district
#   word, or a number with at most a short designator before it, after
#   nothing but separators. A year is not a seat number.
_SENATE_LEGISLATURE_WORDS = re.compile(r"\b(?:seat|election)$")
_US = re.compile(r"\bu\.?s\.?\s")
_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december")
_SEPARATORS = r"[\s,:;()\[\]–—-]*"
_SENATE_QUALIFIED_AWAY = re.compile(
    rf"^(?:{_SEPARATORS}(?:(?:#|[a-z]{{1,3}}\.?)[\s-]*)?\d(?!\d{{3}}\b)|{_SEPARATORS}district\b"
    r"|\s+(?:in|for|from)\s+(?:the\s+)?"
    rf"(?!{_OURS}(?!['’])|\d{{4}}\b|(?:{_MONTHS})\b))"
)
_SPECIAL_WORD = re.compile(r"\bspecial\b")
_REGULAR_WORD = re.compile(r"\bregular\b")


def _senate_phrase_is_legislature(phrase: str, after: str, lead: str, owner_form: bool) -> bool:
    """Whether a matched Senate phrase may be the state legislature's upper
    chamber rather than the U.S. Senate (see the rules above). `after` is
    the text right after the phrase, `lead` the text right before it."""
    if _US.search(phrase + " "):
        return False
    if owner_form and _SENATE_QUALIFIED_AWAY.search(after):
        return True
    if owner_form and _SENATE_LEGISLATURE_WORDS.search(phrase):
        possessive = re.search(rf"{_OURS}(?:\s+state)?['’]", phrase) is not None
        special = _SPECIAL_WORD.search(phrase) is not None or _SPECIAL_LEAD.search(lead) is not None
        return not (possessive or special)
    if not owner_form and re.search(r"\bsenate\s+(?:special\s+)?(?:seat|election)\b", phrase):
        return not (_SPECIAL_WORD.search(phrase) or _SPECIAL_LEAD.search(lead))
    return False


# "special" belongs to the race phrase itself, or to the words just before
# it ("the special election for Georgia's Senate seat").
_SPECIAL_LEAD = re.compile(
    r"\bspecial\s+(?:(?:u\.?s\.?\s+)?senate\s+)?(?:election|race|contest)\s+(?:for|in)\s+(?:the\s+)?$"
)
# A postal code inside a link ("x.com/GA-2") is part of an address, not a
# story naming a race; links are dropped before anything is read.
# A bare domain with a path ("x.com/GA-2") is a link too: its last label is
# letters only, so "U.S./" is not one. Labels are bounded (63 characters,
# DNS's own limit; up to six of them), as is a scheme, so a long run of
# dotted text is read in linear time.
_URL_RE = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]{0,31}://|\bwww\.)\S+|\b[\w-]{1,63}(?:\.[\w-]{1,63}){0,5}\.[a-z]{2,63}/\S*",
    re.IGNORECASE,
)

# Spelled-out ordinals ("Virginia's second district", "the Twenty-First
# District of Texas"), rewritten as figures before the phrases run. Up to
# the fifty-ninth: California's 52 seats are the largest delegation. Only
# an ordinal that a district, seat or race word follows (directly, or at
# the end of a list of ordinals: "second and sixth districts") is
# rewritten: a word ordinal on its own is ordinary English — "It was
# Georgia's first.", "Georgia's third straight win" — and the bare,
# clause-closing House forms are left to figures ("Virginia's 2nd.").
_ORDINAL_UNITS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth")
_ORDINAL_TEENS = ("tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
                  "seventeenth", "eighteenth", "nineteenth")
_TENS = (("twenty", "twentieth"), ("thirty", "thirtieth"), ("forty", "fortieth"), ("fifty", "fiftieth"))


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _ordinal_words() -> dict[str, int]:
    words = {w: i + 1 for i, w in enumerate(_ORDINAL_UNITS)}
    words.update({w: i + 10 for i, w in enumerate(_ORDINAL_TEENS)})
    for t, (tens, tenth) in enumerate(_TENS):
        base = 20 + 10 * t
        words[tenth] = base
        for i, unit in enumerate(_ORDINAL_UNITS):
            words[f"{tens}-{unit}"] = words[f"{tens} {unit}"] = base + i + 1
    return words


_ORDINAL_WORDS = _ordinal_words()
_ORDINAL_WORD_ALT = "|".join(re.escape(w) for w in sorted(_ORDINAL_WORDS, key=len, reverse=True))
_ORDINAL_WORD_RE = re.compile(
    rf"\b(?:{_ORDINAL_WORD_ALT})\b"
    rf"(?=(?:(?:\s*,\s*|\s+and\s+|\s*,\s*and\s+)(?:{_ORDINAL_WORD_ALT}|{_ORD})\b){{0,8}}"
    r"\s+(?:congressional\s+|house\s+)?(?:districts?|seat|race|contest)\b)",
    re.IGNORECASE,
)


def _mark_states(text: str, state_name: str) -> str:
    names = sorted({v.lower() for v in STATE_NAMES.values()}, key=len, reverse=True)
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE)
    return pattern.sub(lambda m: _OURS if m.group(0).lower() == state_name else _OTHER, text)


def _senate_needs_telling_apart(db, cycle: int, state: str) -> bool:
    """Whether "the Senate race" in `state` could be either of two races
    this cycle, so a story has to say "special" (or not) to name one.
    Florida and Ohio elect a senator in 2026 only to fill a vacancy: their
    one race is "…-SPECIAL", and "Brown leads Husted in Ohio Senate race"
    names it. Georgia in 2020 held both. Read from the Race rows, as
    state_candidates._race_id_for decides which race a Senate record
    belongs to; with no session to ask, or no single race found, the two
    are told apart — never guessed together."""
    if db is None:
        return True
    from app.pipeline.fetch.state_candidates import senate_race_ids

    return len(senate_race_ids(db, cycle, state)) != 1


# The phrase is half the rule. Six review rounds each found new prose that
# pairs a state with a seat word and is about something else — a
# legislature's district, a commission seat, a race car's second race — and
# a seventh would find more. So a story must ALSO name one of the race's
# own candidates BY FULL NAME: the leader or runner-up in the stored count
# (RaceResult.tallies — the two people a flip is between), or, before any
# count is stored, a Candidate row for the race (the certified nominees
# when a state has confirmed them). This is identity resolution against
# the race's own structured records, the way a bill number is resolved,
# not a classification.
#
# A full name is the candidate's given name, then optionally a middle name
# or initial, then the surname, as one contiguous name (_candidate_name_pattern):
#
# - The given name is one the records state, matched exactly: the first
#   given name FEC files, a nickname it quotes ('DELGADO, MARTIN ALLEN
#   "ROB"'), the first name the count or the state's ballot prints ("Beto
#   O'Dwyer"), or its initial with a period ("W. Carver"). No prefix or
#   short-form fit: "Donna Davis" is not Don Davis, and "Mike" is not a
#   MICHAEL on record only as MICHAEL (a miss, never a wrong promotion).
# - A middle name must be one on record; a middle initial must be one on
#   record, or anything when the record states no middle name at all
#   ("Leland D. Mason" for a count that prints "Leland Mason").
# - The surname is whole: a multi-word or hyphenated one as a unit
#   ("Elena Ruiz Ortega", "Marisol Hart-Keller"); accents and
#   curly apostrophes folded ("Lucia Peña", "Kip O’Dwyer"); a suffix
#   ("Jr.") may follow.
# - It is written as a name (_name_cased): every word before the surname,
#   and the surname's last word, capitalised. "DALE CARVER LEADS …"
#   names him; "voters will mark green ribbons" is not Mark Green.
# - A feed's printing is read against the person's FEC filing (_printed_given_start):
#   Arkansas's feed prints "Congressman Steve Rowe", and "Congressman"
#   is not a word he filed or a short form of one, so the given name is
#   Steve and "Congressman Rowe" states none. A printed given name counts
#   only if it is a name filed, or (_short_form) a short form no longer
#   than the filed name sharing its first two letters ("Steve" for
#   STEPHEN), or a word beginning with / extending the filed name
#   ("Christopher" for CHRIS). So a nickname that changes the initial or
#   the second letter — Bob/ROBERT, Bill/WILLIAM, Liz/ELIZABETH,
#   Jim/JAMES, Tom/THOMAS, Jack/JOHN, Peggy/MARGARET — is not read from a
#   feed's printing: a miss, never a wrong promotion (the filed given name
#   and the ballot's own printing still name the candidate). A printing
#   whose short-form fit is followed by a full word that fits nothing
#   filed states no given name: "Representative Bob Jones" for ROBERT E,
#   and likewise a printed middle name not on file ("Steve Allen Rowe"
#   beside a filing of STEPHEN alone) drops the printing's given name.
#   Documented limits: a title no longer than the filed name and sharing
#   its first two letters ("Sheriff" for SHERIDAN, "Judge" for JUDITH)
#   still passes as a short form; a filing stating only an initial lets
#   any word with that initial pass; and with no filing of the surname in
#   the race a printing stands.
# - Initials count only together: a quoted "A.J." is one name ("A.J.",
#   "A. J.", "AJ"), as is a record of initials alone ("CALLOWAY, J. D.");
#   one bare letter is never a given name, and a quoted single letter
#   ("(I)", incumbent) is a ballot annotation.
#
# Two people sharing a full name are not told apart: a namesake named in
# full beside the seat's phrase promotes it. Nothing on record separates
# them, and the phrase must name this very seat too.
#
# A surname alone never names a candidate. Every round that graded surname
# mentions ("Carver", "Rep. Mason") found another: a title or article in
# the given-name slot ("The King Center", "Captain Carver"), a namesake
# followed by a bare "Carver said …", a surname that is also a word at the
# start of a sentence ("Green energy", "Case counts"). A full name has none
# of those readings. A story that names the race only by surname leaves the
# issue DEVELOPING — a miss, which the next hour's story can still fix; a
# wrong promotion cannot be undone. Likewise a candidate whose records
# state no given name can't be named at all.
#
# With no candidate on record for the race (no session to ask, or none
# stored), the phrase alone decides, as before.
#
# In a state with two Senate races, a phrase that says neither "special"
# nor "regular" is told apart by whose names the story uses: the race
# whose candidate is named in full, and none of the other race's by any
# mention — a capitalised surname alone included, since holding back can
# only cost a miss — is the one; both races' candidates named is a story
# about the two, which names neither.


class _Person(NamedTuple):
    """How a race's candidate can be named in a story (see above): folded
    to lower case, accents removed, apostrophes straight."""

    surname: str  # "cortez masto", "o'rourke"
    leads: frozenset[str]  # given names a full name may start with
    initials: frozenset[str]  # a first initial that may stand for them ("w" for "W. Carver")
    middles: frozenset[str]  # every other given name or initial on record


def _short_form(token: str, name: str) -> bool:
    """Whether printed `token` can be a short or long form of filed given
    name (or nickname) `name`, without being it. Exactly these pass: one
    letter, the name's initial; a name the filing states only as an
    initial, any word with that initial; a printed word that begins with
    (extends) the filed name ("Christopher" for CHRIS); or a printed word
    no longer than the filed name that shares its first two letters
    ("Steve" for STEPHEN, "Chuck" for CHARLES).

    One shared initial is not enough for a word: about one word in twenty
    shares any given initial, and the word a feed prints before a name is
    most often a title — "Representative" for ROBERT, "Senator" for
    SARAH, "Congressman" for CHARLES each fail both the length and the
    two-letter test. The cost is every nickname that changes the initial
    or the second letter — Bob/ROBERT, Bill/WILLIAM, Liz/ELIZABETH,
    Jim/JAMES, Tom/THOMAS, Jack/JOHN, Peggy/MARGARET: not read from a
    feed's printing, a miss, never a wrong promotion (the filed given name
    and the ballot's own printing still name the candidate). See also
    _printed_given_start: a printed middle name not on file after a
    short-form fit ("Steve Allen Rowe" beside a filing of STEPHEN alone)
    drops the printing's given name too.

    Documented limits: a title no longer than the filed name and sharing
    its first two letters ("Sheriff" for SHERIDAN, "Judge" for JUDITH,
    "Chair" for CHARLES) still passes — nothing in the filing tells those
    apart from a short form — and so does any word with the initial of a
    filing that states only that initial."""
    name = name.replace(".", "")
    if not token or not name:
        return False
    if len(token) == 1 or len(name) == 1:
        return token[0] == name[0]
    if token.startswith(name):
        return True
    return len(token) <= len(name) and token[:2] == name[:2]


def _given_fits(token: str, filings: list[_RecordName]) -> bool:
    """Whether a printed given-name token can be the filed person's: a
    given name or nickname they filed, or a short form of their first
    filed given name or of a nickname (_short_form: "Steve" for STEPHEN,
    "S." for it too). "Congressman" in Arkansas's "Congressman Steve
    Rowe" is neither, so it is not read as his given name — the filing
    decides what a name is, not a list of titles."""
    for filing in filings:
        if token in filing.given or token in filing.nicknames:
            return True
        if any(_short_form(token, n) for n in filing.given[:1] + filing.nicknames):
            return True
    return False


def _printed_given_start(given: list[str], filings: list[_RecordName]) -> int | None:
    """Where a printing's given names start, or None when it states none
    the filing can vouch for. The first token that fits the filing
    (_given_fits) starts them, except:

    - one that fits only as a short form, followed by another fitting
      full word the filing doesn't state as a middle name, is passed over
      ("Senator Steve Rowe" for STEPHEN, were "Senator" to fit: "Steve"
      fits as well, and is the name);
    - one that fits only as a short form, followed by a full word that
      neither fits nor is a filed middle name, means the printing states
      no given name ("Representative Bob Jones" for ROBERT E, were
      "Representative" to fit: "Bob" is his name, but nothing on file
      says so, and the word before it is no name of his either).

    An exact filed given name or nickname always stands."""
    exact = {g for f in filings for g in f.given[:1]} | {n for f in filings for n in f.nicknames}
    filed_middles = {g for f in filings for g in f.given[1:]}
    fits = [_given_fits(g, filings) for g in given]
    for i, g in enumerate(given):
        if not fits[i]:
            continue
        if g in exact:
            return i
        after = given[i + 1] if i + 1 < len(given) else None
        if after is not None and len(after) > 1 and after not in filed_middles:
            if fits[i + 1]:
                continue
            return None
        return i
    return None


def _person(names: list[str | None], count_printing: str | None = None,
            reference: list[str | None] = ()) -> _Person | None:
    """One candidate from every name on record for them (FEC's, the
    count's printing, the ballot's), or None when none states a surname.
    A printing that reads only the last word of a multi-word surname as
    the surname ("Elena Ruiz Ortega" read as "Ortega", beside FEC's
    "RUIZ ORTEGA, ELENA") is the same person, with the whole
    surname.

    `count_printing`, the name as a results feed prints it (one of
    `names`), is read against the person's FEC filing: a feed can print an
    honorific the ballot doesn't (Arkansas: "Congressman Steve Rowe").
    The first printed token that fits the filing (_printed_given_start) is
    where its given names start, and words before it are not names; none
    fitting, it states no given name, and a parenthesised word it prints
    counts as a nickname only where it fits too. With no filing among
    `names`, the race's own filings of that surname (`reference`) are
    checked the same way: a printing none of whose words fits them states
    no given name either ("Congressman Smith" beside SMITH, CHARLES), so a
    different person of that surname whose filing the race lacks is a
    miss, never "Congressman" read as a name. With neither, the printing
    stands as printed. A ballot's printing is the state's own and stands
    as printed."""
    records = [r for r in map(_record_name, filter(None, names)) if r.surname]
    if not records:
        return None
    last = max((r.surname for r in records), key=lambda s: len(s.split()))

    def mine(r: _RecordName) -> bool:
        return r.surname == last or last.endswith(" " + r.surname)

    checked = _record_name(count_printing) if count_printing else None
    filings = [r for r in records if r.filed and mine(r)]
    if not filings:
        filings = [r for r in map(_record_name, filter(None, reference)) if r.filed and mine(r)]
    leads, initials, middles = set(), set(), set()
    for record in records:
        if not mine(record):
            continue  # a record of someone else (it can't be this person's)
        surname, given, nicknames, filed = record
        extra = last.split()[:-len(surname.split())] if surname != last else []
        if extra and given[-len(extra):] == extra:
            given = given[:-len(extra)]  # "elena ruiz" + "ortega"
        if record == checked and not filed and filings:
            at = _printed_given_start(given, filings)
            given = given[at:] if at is not None else []
            # A feed's parenthesised word is as likely an annotation
            # ("(Incumbent)", "(Dem)") as a nickname: kept only where the
            # filing vouches for it, as its given names are.
            nicknames = [n for n in nicknames if _given_fits(n, filings)]
        leads.update(nicknames)
        middles.update(n for n in nicknames if "." not in n)
        if not given:
            continue
        if len(given) > 1 and all(len(g) == 1 for g in given):
            # All initials ("CALLOWAY, J. D."): together they are the name.
            leads.add(".".join(given))
            initials.add(given[0])
            middles.update(given[1:])
            continue
        # The first given name, or the name after a leading initial ("J.
        # Robert Smith" goes by Robert); its initial stands for it. A
        # single letter is never a lead.
        first = given[0] if len(given[0]) > 1 or len(given) == 1 else given[1]
        if len(first) > 1:
            leads.add(first)
        initials.add(given[0][0])
        middles.update(g for g in given if g != first)
    return _Person(last, frozenset(leads), frozenset(initials), frozenset(middles))


def _race_people(db, race_id: str) -> list[_Person] | None:
    """Each person a story about `race_id` would name: the leader and
    runner-up in the stored count, else every certified nominee, else every
    Candidate row. None when nothing is on record (the phrase then decides
    alone)."""
    from app.models import Candidate, RaceResult

    if db is None:
        return None
    result = db.get(RaceResult, race_id)
    try:
        tallies = json.loads(result.tallies or "[]") if result is not None else []
    except (ValueError, TypeError):
        tallies = []
    rows: list = []  # the race's Candidate rows, asked for only when needed

    def race_rows() -> list:
        if not rows:
            rows.append(db.query(Candidate).filter(Candidate.race_id == race_id).all())
        return rows[0]

    people: list[_Person] = []
    for tally in tallies[:2] if isinstance(tallies, list) else []:
        if not isinstance(tally, dict):
            continue
        names = [str(tally.get("name") or "")]
        cand = db.get(Candidate, tally["candidateId"]) if tally.get("candidateId") else None
        linked = cand is not None and cand.race_id == race_id
        # The feed's own printing, unless sync already put the ballot's in its place.
        printed = names[0] if not (linked and names[0] == cand.ballot_name) else None
        if linked:
            names += [cand.name, cand.ballot_name]
        person = _person(names, printed)
        if person is not None and not person.leads and not person.initials:
            # A count that prints only a surname ("CARVER"): the race's own
            # Candidate rows of that surname state the given name.
            names += [n for c in race_rows() if _record_name(c.name).surname == person.surname
                      for n in (c.name, c.ballot_name)]
            person = _person(names, printed)
        elif person is not None and not linked:
            # A count row matched to no Candidate: its printed given names
            # are still checked against the race's filings of that surname
            # (an honorific the feed prints is not a given name).
            person = _person(names, printed, [c.name for c in race_rows()
                                              if _record_name(c.name).surname == person.surname])
        if person is not None:
            people.append(person)
    if not people:
        chosen = [c for c in race_rows() if c.confirmed_general] or race_rows()
        people = [p for c in chosen if (p := _person([c.name, c.ballot_name])) is not None]
    return people or None


def _surname_pattern(person: _Person) -> str:
    """The regular expression for `person`'s whole surname: its words
    joined by a space or a hyphen, an apostrophe optional."""
    return r"(?:[^\S\n]*-[^\S\n]*|[^\S\n]+)".join(
        re.escape(t).replace("'", "'?") for t in re.split(r"[\s-]+", person.surname) if t
    )


def _lead_pattern(lead: str) -> str:
    """A lead given name; an initials one ("a.j") matches "A.J.", "A. J."
    and "AJ", never two bare letters ("A J")."""
    if "." not in lead:
        return re.escape(lead)
    return r"(?:\.[^\S\n]*)?".join(re.escape(ch) for ch in lead.split(".")) + r"\.?"


@lru_cache(maxsize=1024)
def _candidate_name_pattern(person: _Person) -> re.Pattern | None:
    """The regular expression a story's (accent-folded) text must match to
    name `person` by full name (see the rule above); None when the records
    state no given name to name them by. Case is ignored here and checked
    by _name_cased, on the matched span."""
    leads = sorted(person.leads, key=len, reverse=True)
    starts = [_lead_pattern(g) for g in leads] + [rf"{re.escape(i)}\." for i in sorted(person.initials)]
    if not starts:
        return None
    middle_names = sorted((m for m in person.middles if len(m) > 1), key=len, reverse=True)
    middle_initials = {m[0] for m in person.middles}
    initial = rf"[{''.join(sorted(middle_initials))}]\.?" if middle_initials else r"[a-z]\.?"
    middle = "|".join([initial, *(rf"[\"“”']?{re.escape(m)}[\"“”']?" for m in middle_names)])
    # [^\S\n]: a name never runs across a line break (title, summary and
    # facts are separate lines: _may_match).
    return re.compile(
        rf"(?<![\w'.-])(?:{'|'.join(starts)})[^\S\n]+(?:(?:{middle})[^\S\n]+){{0,2}}"
        rf"(?P<surname>{_surname_pattern(person)})(?![\w]|-\w)",
        re.IGNORECASE,
    )


@lru_cache(maxsize=1024)
def _surname_alone_pattern(person: _Person) -> re.Pattern:
    """`person`'s surname as a whole word (case checked by _name_cased)."""
    return re.compile(rf"(?<![\w'-])(?P<surname>{_surname_pattern(person)})(?![\w]|-\w)", re.IGNORECASE)


def _starts_upper(word: str) -> bool:
    """Whether a word's first letter is a capital ("O'Dwyer", "A.J.")."""
    return next((ch.isupper() for ch in word if ch.isalpha()), False)


def _name_cased(match: re.Match) -> bool:
    """Whether a matched name is written as a name: every word before the
    surname, and the surname's last word, capitalised ("Mark Green", "Beto
    O'Dwyer", "Chris van Dorn", "DALE CARVER"). The patterns ignore
    case so that an all-capitals headline still names; this is what keeps
    prose from doing so — "will mark green ribbons" is not Mark Green. A
    surname's inner words may be lower case ("van", "de la"): no list of
    particles is needed when only its last word is checked."""
    before = match.string[match.start():match.start("surname")]
    last = re.split(r"[\s-]+", match.group("surname"))[-1]
    return all(_starts_upper(w) for w in before.split()) and _starts_upper(last)


def _names_a_candidate(text: str, people: list[_Person], surname_alone: bool = False) -> bool:
    """Whether `text` names one of `people` (from _race_people) by full
    name, written as a name (_name_cased). With `surname_alone`, a
    capitalised surname on its own counts too — only for holding a story
    back (_other_senate_race_named), where reading too much into a
    surname can cost a miss and never a wrong promotion."""
    folded = _strip_accents(text)
    lower = folded.lower()
    for person in people:
        # A substring scan first, as in _mentions_full_name: the pattern's
        # leading lookbehind defeats the regex engine's own literal scan.
        if max(re.split(r"['\s-]+", person.surname), key=len) not in lower:
            continue
        pattern = _surname_alone_pattern(person) if surname_alone else _candidate_name_pattern(person)
        if pattern is not None and any(_name_cased(m) for m in pattern.finditer(folded)):
            return True
    return False


# What may follow a House district phrase ("Georgia's 2nd District",
# "Alaska's at-large"). Courts of appeals, public service commissions,
# school boards and county commissions number their districts too
# ("Georgia's District 2 Public Service Commission seat", "California's 2nd
# District Court of Appeal"), and name themselves right after the number.
# A congressional district phrase is followed by: nothing, punctuation, a
# race word ("seat", "race"), "House" / "congressional" / "U.S.", or the
# verb of its own clause ("… 2nd District flips"). So, read structurally
# rather than from a list of other bodies' names:
#
# - a run of capitalised words straight after the phrase is another body's
#   name ("Public Service Commission", "Court of Appeal", "School Board")
#   unless it names one of the race's candidates ("Georgia's 2nd District
#   Republican Dale Carver"); with no candidate on record, it never
#   stands;
# - lower-case words running straight into a race word with no determiner
#   or preposition between ("county commission seat", "board of education
#   race") are the same thing in lower case, while "flips the seat" and
#   "heads to runoff" are a clause;
# - a race word followed by "on" / "of" and a capitalised name is a seat
#   ON something else ("2nd seat on the Public Service Commission"), unless
#   that is the House, Congress, or a candidate.
#
# A phrase that ends in its own punctuation ("… Virginia's 2nd.",
# "In California's 45th,") is closed and followed by a new clause.
_AFTER_CONGRESS_WORDS = re.compile(r"(?:(?:u\.?\s?s\.?(?=\s|$)|(?:house|congressional|congress)\b)\s*)*",
                                   re.IGNORECASE)
_HOUSE_RACE_WORD = re.compile(r"(?:seats?|races?|contests?|elections?|primary|primaries|runoffs?)",
                              re.IGNORECASE)
# Words that end a noun compound: determiners and prepositions ("flips the
# seat", "heads to runoff"). "of" is left out — it sits inside one ("board
# of education seat").
_NOT_IN_A_NAME = frozenset({
    "the", "a", "an", "its", "his", "her", "their", "our", "this", "that",
    "to", "in", "into", "for", "on", "at", "with", "from", "by", "after", "as", "over",
})


def _capitalised_run_names(text: str, surnames: list[str] | None, congress_ok: bool = False) -> bool:
    """Whether the run of capitalised words at the start of `text` names
    one of `surnames` (or, with `congress_ok`, is the House or Congress)."""
    run = re.match(r"(?:[A-Z][\w'.-]*\s*)+", text)
    if run is None:
        return True
    words = _strip_accents(run.group(0)).lower()
    if congress_ok and _AFTER_CONGRESS_WORDS.match(words).end() > 0:
        return True
    return any(
        re.search(r"\b" + r"[\s-]+".join(re.escape(t) for t in re.split(r"[\s-]+", s) if t) + r"\b", words)
        for s in surnames or ()
    )


def _district_phrase_closes(phrase: str, after: str, surnames: list[str] | None) -> bool:
    """Whether a matched House district phrase is the congressional
    district's rather than another district-numbered body's (see above).
    `after` is the story's text right after the phrase, case kept."""
    if phrase and not phrase[-1].isalnum():
        return True

    def seat_on(tail: str) -> bool:
        on = re.match(r"\s+(?:on|of)\s+(?:the\s+)?(?=[A-Z])", tail)
        return on is None or _capitalised_run_names(tail[on.end():], surnames, congress_ok=True)

    if re.search(rf"\b{_HOUSE_RACE_WORD.pattern}$", phrase, re.IGNORECASE):
        return seat_on(after)  # "Georgia's 2nd seat" + " on the Public Service Commission"
    if re.match(r"[^\S\n]*(?:\n|$)", after):
        return True  # the end of its line: the title, a fact
    rest = after.split("\n", 1)[0].lstrip()
    skipped = _AFTER_CONGRESS_WORDS.match(rest).group(0)
    rest = rest[len(skipped):]
    if re.search(r"house\s*$", skipped, re.IGNORECASE) and re.match(r"of\s+(?:the\s+)?[A-Z]", rest):
        # "House of …" is a chamber's name: the congressional one is "House
        # of Representatives", a legislature's "House of Delegates".
        return re.match(r"of\s+Representatives\b", rest, re.IGNORECASE) is not None
    if not rest or not rest[0].isalnum():
        return True
    word = rest.split()[0]
    if _HOUSE_RACE_WORD.fullmatch(word):
        return seat_on(rest[len(word):])
    if word[0].islower():
        words = re.match(r"[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,3}", rest)
        for i, token in enumerate(words.group(0).split() if words else ()):
            if token in _NOT_IN_A_NAME:
                return True
            if i and _HOUSE_RACE_WORD.fullmatch(token):
                return False  # "county commission seat"
        return True
    if not word[0].isupper():
        return True  # a number: "Georgia's 2nd District 2026 race"
    return _capitalised_run_names(rest, surnames)


def _results_race_named(issue, story_text: str, db=None) -> bool:
    """Whether `story_text` (a news cluster's title, summary and facts)
    names the race an election-results issue is about: a phrase naming the
    state's seat AND, where the race has candidates on record, one of
    them by full name (see the rule above). False when the issue's race
    can't be read, so an unreadable row is never promoted. `db` answers
    whether a Senate race is its state's only one this cycle (see
    _senate_needs_telling_apart) and who the race's candidates are; the
    issue's own session is used when none is passed."""
    race = None
    try:
        for action in json.loads(issue.actions or "[]"):
            race = _RACE_LINK_RE.search(str((action or {}).get("url") or "")) or race
    except (ValueError, TypeError, AttributeError):
        return False
    if race is None:
        return False
    if db is None:
        try:
            db = object_session(issue)
        except Exception:  # not a mapped instance (a test double)
            db = None
    return race_named(race.group(0).removeprefix("#race-"), story_text, db)


_RACE_ID_RE = re.compile(r"^(\d{4})-(SEN|HOUSE)-([A-Z]{2})(?:-(\d+|SPECIAL))?$")


def race_named(race_id: str, story_text: str, db=None, need_candidate: bool = True) -> bool:
    """Whether `story_text` names the race `race_id` ("2026-SEN-GA",
    "2026-HOUSE-GA-2") by the seat phrase above. With `need_candidate`
    (an election-results issue's promotion), a race with candidates on
    record must also have one of them named by full name; without it (the
    ballot links, app/ballot_links.py, which name candidates separately)
    the phrase alone decides."""
    race = _RACE_ID_RE.match(race_id or "")
    if race is None:
        return False
    cycle, office, state, part = race.groups()
    state_name = STATE_NAMES.get(state, "").lower()
    if not state_name:
        return False
    story_text = _strip_accents(_URL_RE.sub(" ", story_text))
    # The phrases are read in lower case; `cased` keeps the story's own
    # capitals at the same offsets, for what follows a district phrase.
    # Whitespace collapsed, a line break kept as one (a phrase that ends
    # its line is closed: _district_phrase_closes).
    cased = re.sub(r"\s+", lambda w: "\n" if "\n" in w.group(0) else " ", story_text)
    cased = _ORDINAL_WORD_RE.sub(lambda m: _ordinal(_ORDINAL_WORDS[m.group(0).lower()]), cased)
    cased = _mark_states(cased, state_name)
    marked = "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in cased)

    found: list = []  # asked of the database once, and only once a phrase matches

    def people() -> list[tuple[str, str]] | None:
        if not found:
            found.append(_race_people(db, race_id))
        return found[0]

    def named() -> bool | None:
        """Whether the story names one of the race's candidates by full
        name; None when none is on record, or none is asked for."""
        if not need_candidate:
            return None
        ours = people()
        return None if ours is None else _names_a_candidate(story_text, ours)

    def surnames() -> list[str] | None:
        ours = people()
        return None if ours is None else [p.surname for p in ours]

    def house_named(m) -> bool:
        if not _district_phrase_closes(m.group(0), cased[m.end():m.end() + 160], surnames()):
            return False
        return named() is not False

    if office == "SEN":
        tell_apart = None  # asked of the database only once a phrase matches
        for i, phrase in enumerate(_SENATE_PHRASES):
            for m in re.finditer(phrase, marked):
                lead = marked[max(0, m.start() - 60):m.start()]
                if _senate_phrase_is_legislature(m.group(0), marked[m.end():m.end() + 60], lead,
                                                 owner_form=i < _SENATE_OWNER_PHRASES):
                    continue
                ours = named()
                if ours is False:
                    return False  # a phrase, but none of this race's candidates
                if tell_apart is None:
                    tell_apart = _senate_needs_telling_apart(db, int(cycle), state)
                if not tell_apart:
                    return True
                special = _SPECIAL_WORD.search(m.group(0)) is not None or _SPECIAL_LEAD.search(lead) is not None
                if special or _REGULAR_WORD.search(m.group(0)):
                    if special == (part == "SPECIAL"):
                        return True
                    continue
                # A phrase that says neither "special" nor "regular" ("Georgia
                # Senate runoff") is told apart by whose candidates it names:
                # this race's and none of the other's names it; both races'
                # is a story about the two, which names neither. Only with
                # nothing on record to compare does the old reading stand —
                # the unqualified phrase is the regular race.
                other = _other_senate_race_named(db, int(cycle), state, race_id, story_text) if ours else None
                if other is False:
                    return True
                if other is None and part != "SPECIAL":
                    return True
        return False
    n = int(part or 0)
    # Postal-code form, case-sensitive: "GA-2", "GA-02", "AK-AL".
    code = r"(?:0?0|AL)" if n == 0 else rf"0?{n}"
    if re.search(rf"\b{state}-{code}\b", story_text):
        return named() is not False
    if n == 0:
        return any(house_named(m) for p in _AT_LARGE_PHRASES for m in re.finditer(p, marked))
    ordinal = _ordinal(n)
    for phrase in _HOUSE_PHRASES:
        for m in re.finditer(phrase, marked):
            if _STATE_LEGISLATURE_LEAD.search(marked[max(0, m.start() - 60):m.start()]):
                continue
            groups = m.groupdict()
            if (
                (groups.get("ords") and ordinal in re.findall(_ORD, groups["ords"]))
                or (groups.get("num") and int(groups["num"]) == n)
                or ordinal in (groups.get("ord"), groups.get("bare"))
            ) and house_named(m):
                return True
    return False


def _other_senate_race_named(db, cycle: int, state: str, race_id: str, story_text: str) -> bool | None:
    """Whether the story names a candidate of the state's OTHER Senate race
    this cycle (Georgia 2020's regular race, for its special) by any
    mention: full name or capitalised surname. None when that can't be known: no session, no other race found, or no
    candidate on record for it."""
    if db is None:
        return None
    from app.pipeline.fetch.state_candidates import senate_race_ids

    others = [rid for rid in senate_race_ids(db, cycle, state) if rid != race_id]
    known = [theirs for rid in others if (theirs := _race_people(db, rid)) is not None]
    if not others or len(known) != len(others):
        return None
    # Leniently: a capitalised surname of theirs is enough to hold back
    # ("Raphael Warnock leads as Perdue concedes to Brennan" is about both
    # races). Holding back can only cost a miss.
    return any(_names_a_candidate(story_text, theirs, surname_alone=True) for theirs in known)


def _may_match(candidate, title: str, facts: list, summary: str, db=None) -> bool:
    """Whether a news cluster may update `candidate` at all: any issue but
    a still-DEVELOPING election-results one, which only a story naming its
    race may. Once news has promoted one (CONFIRMED), the row is the news
    story's: promotion replaced its actions — the `#race-…` link included —
    with the cluster's, so its race can no longer be read, and gating it
    would make every later update of the same story a duplicate row (the
    old one retired, its id and history lost, Bluesky posting again). It
    matches like any other issue from then on."""
    if candidate.source_type != _ELECTION_RESULTS_SOURCE or candidate.status != ActionIssueStatus.DEVELOPING:
        return True
    if not candidate.is_current:
        return False  # retired (reverted, or its flip retired): never promoted back
    # One line each: a name never runs from one into the next.
    text = "\n".join([title, summary or "", *(str(f) for f in facts or [])])
    return _results_race_named(candidate, text, db)


def _find_matching_issue(
    title: str,
    facts: list,
    recent_issues: list,
    recent_embs: "np.ndarray | None",
    title_emb: "np.ndarray",
    matched_issue_ids: set,
    source_urls: list | None = None,
    summary: str = "",
    db=None,
    source_titles: dict[str, str] | None = None,
):
    """Find the existing issue (if any) this new cluster's title/facts
    should update instead of becoming a new row — extracted from
    _run_refresh's matching loop so it's directly testable without mocking
    the rest of the pipeline (news fetch, LLM, clustering).

    2026-07 audit rework: matching was previously raw title cosine alone
    (>= TOPIC_CHANGE_THRESHOLD), and the audit measured that signal failing
    in BOTH directions on real production rows: two same-story rows scored
    0.80/0.85 (below 0.82 -> duplicate rows, duplicate Bluesky posts),
    while a different-story pair scored 0.88 (above 0.82 -> the row's
    content was overwritten in place, leaving its already-published
    Bluesky post describing a different story than the permalink it links
    to). Title cosine now only nominates candidates (>=
    _TOPIC_MATCH_CANDIDATE_FLOOR); the same-story decision is made by
    signature overlap (see _issue_signature), checked only after the
    exact-content-duplicate case (see _is_exact_content_duplicate) — a
    sparse single-entity signature can fail to match even an identical
    copy of itself (live 2026-07-23 bug, ids 420/421). Signature-less rows
    (no facts stored) fall back to the old >= TOPIC_CHANGE_THRESHOLD
    title-only behavior rather than being unmatchable.

    A shared source URL is checked before the title-cosine candidate
    floor, so a reworded title can still match through it, but only with
    the titles agreeing to SHARED_SOURCE_TITLE_SIM: one roundup article is
    cited by unrelated stories (2026-10-08, see that constant). It was
    treated as conclusive on its own from 2026-08. One shared article is
    conclusive still: the one whose headline is the candidate's title
    (`source_titles`, the new cluster's articles by URL), when that
    headline tells one story (_multi_story_headline). That article is the
    candidate's story, and the new cluster holds it. Of several candidates
    sharing a source, the one whose title agrees best is taken.
    """
    sims = recent_embs @ title_emb if recent_embs is not None else None
    if source_urls:
        new_urls = {u for u in source_urls if u}
        best, best_sim = None, -2.0
        for idx, candidate in enumerate(recent_issues if new_urls else []):
            if candidate.id in matched_issue_ids or not _may_match(candidate, title, facts, summary, db):
                continue
            try:
                shared = new_urls & set(json.loads(candidate.source_urls or "[]"))
            except (ValueError, TypeError):
                continue
            if not shared:
                continue
            sim = _shared_source_title_sim(float(sims[idx]), title, candidate.title or "") if sims is not None else -1.0
            # A vote draft's source is its record: a story citing it is
            # the draft's story (promotion). A news row needs its title
            # to agree too (SHARED_SOURCE_TITLE_SIM), or the shared article
            # to be the one that titled it.
            titled = any(
                (source_titles or {}).get(u) == candidate.title for u in shared
            ) and not _multi_story_headline(candidate.title or "")
            if (candidate.source_type or titled or sim >= SHARED_SOURCE_TITLE_SIM) and sim > best_sim:
                best, best_sim = candidate, sim
        if best is not None:
            action_metrics.increment("issues_matched_by_source")
            return best

    if sims is None:
        return None

    for cand_idx in np.argsort(-sims):
        sim = float(sims[cand_idx])
        if sim < _TOPIC_MATCH_CANDIDATE_FLOOR:
            break
        candidate = recent_issues[int(cand_idx)]
        if candidate.id in matched_issue_ids or not _may_match(candidate, title, facts, summary, db):
            continue
        try:
            cand_facts = json.loads(candidate.facts or "[]")
        except (ValueError, TypeError):
            cand_facts = []
        # A near-identical title is conclusive on its own: facts are free
        # to differ, that's a real update (_same_story).
        if _same_story(sim, title, facts, candidate.title, cand_facts):
            action_metrics.increment("issues_matched_by_title")
            return candidate
    return None


# ── Ballot links ─────────────────────────────────────────────────
# Which ballot an issue reaches: the states and races its own words name
# (ballot_links below), stored on the issue (ActionIssue.ballot_links) and
# served with the state's deadlines and official links (app/ballot_links.py).
#
# Which ballot an Action Center issue reaches: the states and races its
# own words name, worked out when the issue is written (the hourly refresh,
# action_center) and served with the state's deadlines and official links
# (api/action.py, issue_ballot).
#
# An election story ("Agency sues a state over mail ballots", "The president
# stumps for Roe in Georgia") is the one kind of issue whose next step is the
# voter's own ballot, and until 2026-10 none of the eight such issues in a
# month linked a race, a ballot page or a deadline. Every link here comes
# from an entity match against stored rows, never from model text:
#
# - **A nominee named.** A confirmed general-election candidate of a race
#   this cycle named by full name in the headline, summary or facts (the
#   election-results gate's own matcher, action_center._names_a_candidate),
#   members of Congress included. A full name nominees of two races share
#   ("Pat Quill") names one of them only beside its state's name.
# - **A nominee's surname beside the state.** "Roe" in a story that also
#   names Georgia, when Georgia's nominees include exactly one Roe, and
#   the surname is not written after a different given name ("Jordan
#   Marsh" is not Lee Marsh).
# - **The seat named.** "Alaska's Senate race", "Georgia's 2nd District":
#   the results gate's seat phrases (action_center.race_named), which
#   already tell a legislature's seats and other states' names apart.
#
# A state is linked only through one of its races. A state named in the
# headline of a story the class vote calls an election was also linked,
# alone, until review: 16 of 22 such links on the stored issues were on
# topic, under the site's precision bar, so that path was dropped.
#
# Measured on the stored issues: see docs/research/action-center-ballot-links.md.

# Every state name, longest first, so "West Virginia" is one name and
# never Virginia; written as a name (capitalised, or the whole headline in
# capitals). "Washington, D.C." is the capital, not the state.
_BALLOT_STATE_NAMES = sorted(STATE_NAMES.items(), key=lambda kv: len(kv[1]), reverse=True)
_BALLOT_STATE_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(n) for _, n in _BALLOT_STATE_NAMES) + r")(?![\w-])(?!,?\s+D\.?C\b)",
    re.IGNORECASE,
)
_BALLOT_STATE_CODE = {n.lower(): c for c, n in STATE_NAMES.items()}

# How a link was made, most specific first (one race keeps its best).
BALLOT_CANDIDATE, BALLOT_SURNAME, BALLOT_SEAT = "candidate", "surname", "seat"
_BALLOT_VIA_ORDER = {BALLOT_CANDIDATE: 0, BALLOT_SURNAME: 1, BALLOT_SEAT: 2}


def states_named(text: str) -> list[str]:
    """The postal codes of the states `text` names, in order of first
    mention."""
    out: list[str] = []
    for m in _BALLOT_STATE_RE.finditer(text or ""):
        word = m.group(0)
        if not (word[0].isupper() or word.isupper()):
            continue  # "georgia" in a URL slug or prose is not written as a name
        code = _BALLOT_STATE_CODE[word.lower()]
        if code not in out:
            out.append(code)
    return out


@dataclass(frozen=True)
class _Nominee:
    race: Race
    name: str
    person: object  # action_center._Person


def _ballot_nominees(db: Session, cycle: int) -> list[_Nominee]:
    rows = (
        db.query(Candidate, Race).join(Race, Race.id == Candidate.race_id)
        .filter(Race.cycle_year == cycle, Candidate.confirmed_general.is_(True))
        .all()
    )
    out = []
    for cand, race in rows:
        person = _person([cand.name, cand.ballot_name])
        if person is not None:
            out.append(_Nominee(race, cand.ballot_name or cand.name, person))
    return out


def _ballot_surname_alone(text: str, nominee: _Nominee) -> str | None:
    """`nominee`'s surname as the story writes it, when it is written as a
    name and every time without another capitalised word before it that
    isn't one of their own given names; else None. One "Jordan Marsh" or
    "Rep. Marsh" anywhere and the bare surname could be that person (or
    anyone under that title), so it names no one."""
    folded = _strip_accents(text)
    person = nominee.person
    own = {g.lower() for g in person.leads} | {i.lower() for i in person.initials}
    hit = None
    for m in _surname_alone_pattern(person).finditer(folded):
        if not _name_cased(m):
            continue
        before = re.search(r"([\w.'\"“”]+)[^\S\n]+$", folded[:m.start()])
        word = before.group(1).strip("\"“”'") if before else ""
        if word[:1].isupper() and word.rstrip(".").lower() not in own:
            return None
        hit = hit or m.group("surname")
    return hit


def _ballot_full_name(text: str, nominee: _Nominee) -> str | None:
    """`nominee`'s full name as the story writes it (_names_a_candidate's
    match), or None."""
    pattern = _candidate_name_pattern(nominee.person)
    if pattern is None:
        return None
    folded = _strip_accents(text)
    return next((m.group(0) for m in pattern.finditer(folded) if _name_cased(m)), None)


def ballot_links(db: Session, cycle: int, title: str, summary: str, facts: list[str]) -> list[dict]:
    """The issue's ballot links: [{"state", "raceId", "candidate", "via"}],
    the races it names, each by its best link."""
    lines = [title or "", summary or "", *(str(f) for f in facts or [])]
    text = "\n".join(lines)
    named = set(states_named(text))
    found: dict[str, dict] = {}

    def add(race: Race, via: str, candidate: str | None) -> None:
        held = found.get(race.id)
        if held is None or _BALLOT_VIA_ORDER[via] < _BALLOT_VIA_ORDER[held["via"]]:
            found[race.id] = {"state": race.state, "raceId": race.id, "candidate": candidate, "via": via}

    folded = _strip_accents(text).lower()
    nominees = _ballot_nominees(db, cycle)
    # Earliest-mentioned first, so a race keeps the nominee the story leads with.
    nominees.sort(key=lambda n: folded.find(n.person.surname.split()[-1]) % (len(folded) + 1))
    full: dict[str, list[tuple[_Nominee, str]]] = {}
    for n in nominees:
        if max(re.split(r"['\s-]+", n.person.surname), key=len) not in folded:
            continue  # the cheap scan _names_a_candidate itself starts with
        written = _ballot_full_name(text, n)
        if written:
            full.setdefault(written.lower(), []).append((n, written))
    for group in full.values():
        races = {n.race.state for n, _ in group}
        for n, written in group:
            if len(races) == 1 or n.race.state in named:
                add(n.race, BALLOT_CANDIDATE, written)
    for state in named:
        in_state = [n for n in nominees if n.race.state == state]
        by_surname: dict[str, list[_Nominee]] = {}
        for n in in_state:
            by_surname.setdefault(n.person.surname, []).append(n)
        for group in by_surname.values():
            if len(group) == 1 and group[0].race.id not in found:
                written = _ballot_surname_alone(text, group[0])
                if written:
                    add(group[0].race, BALLOT_SURNAME, written)
        for race in db.query(Race).filter(Race.cycle_year == cycle, Race.state == state):
            if race.id not in found and race_named(race.id, text, db, need_candidate=False):
                add(race, BALLOT_SEAT, None)
    return sorted(found.values(), key=lambda link: (_BALLOT_VIA_ORDER[link["via"]], link["raceId"]))


def _run_periodic_bluesky_posts(db: Session) -> None:
    """The daily member spotlight, and the hourly retry of posts Bluesky
    refused (broadcast.deliver_pending).

    It doesn't depend on the news at all — it reads member scores — but it
    ran only as stage 6 of the refresh, downstream of the two early
    aborts. So an hour where the feeds returned nothing, or nothing
    policy-relevant, silently took the daily spotlight with it, and a run
    of such hours spanning a UTC day boundary skipped that day's
    spotlight entirely (post_daily_spotlight is a no-op once the day
    rolls over: it asks whether one was posted *today*, not whether the
    last one is overdue).

    Called on every exit path instead, which also makes the spotlight a
    usable signal: when it is missing from the feed, the pipeline is not
    completing — it is no longer just evidence that the news was quiet.
    """
    try:
        post_daily_spotlight(db)
    except Exception:
        logger.exception("Bluesky spotlight post failed (non-fatal)")
    # Hourly, whatever the news did: a post Bluesky refused earlier today
    # (it is already in the feed) gets another try.
    try:
        from app.broadcast import deliver_pending
        deliver_pending(db)
    except Exception:
        logger.exception("Retrying undelivered Bluesky posts failed (non-fatal)")
    # Also hourly: the feed's picture and description for a post whose page
    # couldn't be read when it was published.
    try:
        from app.broadcast import fill_missing_cards
        fill_missing_cards(db)
    except Exception:
        logger.exception("Filling feed entries' cards failed (non-fatal)")


def _persist_metrics(db: Session) -> dict[str, int]:
    """Write this run's validator counters to api_cache and return them.

    Called on every exit path, not just the happy one. The two early
    returns below (no articles fetched, nothing policy-relevant) used to
    return before the tail persist, so the runs with the most diagnostic
    value — the ones that published nothing at all — were the only runs
    that left no record behind. "Was it a slow news day or is the fetch
    broken?" was then unanswerable after the fact: a quiet hour and a
    failing feed both looked like an absent row.
    """

    snapshot = action_metrics.snapshot()
    action_metrics.persist(db, f"run-{datetime.now(_US_EAST).strftime('%Y-%m-%d-%H%M')}")
    if snapshot:
        logger.info("Validator counters this run: %s", snapshot)
    return snapshot


def _rank_ordered_issues(
    still_current: list[ActionIssue], matched_issue_ids: set[int],
) -> list[ActionIssue]:
    """Final rank order for this run's renumbering pass — extracted for
    direct testability, same precedent as _bluesky_eligible_issues/
    _retire_untouched_issues.

    Issues touched (matched or newly created) this run keep their
    relative order first; spared survivors follow. Spared DEVELOPING
    issues (not yet promoted or expired) always sort after every spared
    CONFIRMED issue — a hedged, unconfirmed draft must never crowd out a
    real top story on its own placeholder rank value.
    """
    touched = sorted(
        (r for r in still_current if r.id in matched_issue_ids),
        key=lambda r: r.rank or 0,
    )
    spared_confirmed = sorted(
        (r for r in still_current
         if r.id not in matched_issue_ids and r.status != ActionIssueStatus.DEVELOPING),
        key=lambda r: r.rank or 0,
    )
    spared_developing = sorted(
        (r for r in still_current
         if r.id not in matched_issue_ids and r.status == ActionIssueStatus.DEVELOPING),
        key=lambda r: r.rank or 0,
    )
    return touched + spared_confirmed + spared_developing


def _bluesky_eligible_issues(db: Session, today: str) -> list[ActionIssue]:
    """Today's current issues, excluding DEVELOPING ones — extracted for
    direct testability, same precedent as _retire_untouched_issues/
    _apply_matched_issue_update.

    Nothing posts to Bluesky before press corroborates it: there is no
    edit/correction mechanism for a wrong post, and Bluesky itself doesn't
    support editing a published one.
    """
    return (
        db.query(ActionIssue)
        .filter(
            ActionIssue.date == today,
            ActionIssue.is_current == True,  # noqa: E712
            ActionIssue.status != ActionIssueStatus.DEVELOPING,
        )
        .order_by(ActionIssue.rank)
        .all()
    )


def _commit_stage(db: Session, stage: str) -> None:
    """Commit a refresh stage's writes, whether or not the stage raised
    partway (what it wrote before the raise is kept, as it always was), so
    no stage leaves the write transaction open into the next one's slow
    work. A failed commit is rolled back and the refresh goes on."""
    try:
        db.commit()
    except Exception:
        logger.exception("Commit after the %s stage failed (non-fatal)", stage)
        db.rollback()


def _run_refresh(db: Session) -> int:
    t0 = time.perf_counter()
    today = eastern_date(utcnow()).isoformat()

    logger.info("Action center refresh starting for %s", today)
    action_metrics.reset()
    # The election whose races an issue's ballot links name (ballot_links).
    ballot_cycle = active_election(db).cycle
    _set_refresh_state(
        is_running=True, stage="primary_source_signals", stage_detail=None,
        started_at=utcnow(),
    )

    # 0. Check primary-source signals (Senate/House roll-call votes, Federal
    # Register significant rules) for anything notable enough to draft
    # ahead of press coverage — see early_signal.py. Independent of the
    # news fetch below (an empty/failed news pull must never block this,
    # and vice versa), so it runs first and each source is wrapped non-
    # fatally: a bug in one must never take down the whole hourly refresh
    # or block the other source.
    #
    # Each stage commits what it wrote before the next one's fetch: a draft
    # flushed by the roll-call check and left uncommitted held SQLite's
    # write lock through the Federal Register and reader-count requests
    # after it, and the nightly chain runs beside this refresh.
    try:
        check_roll_call_signals(db, today)
    except Exception:
        logger.exception("check_roll_call_signals failed (non-fatal)")
    _commit_stage(db, "roll-call signals")
    try:
        check_federal_register_signals(db, today)
    except Exception:
        logger.exception("check_federal_register_signals failed (non-fatal)")
    _commit_stage(db, "Federal Register signals")
    try:
        _refresh_open_comment_views(db)
    except Exception:
        logger.exception("Open comment period reader counts failed (non-fatal)")
        db.rollback()
    # Expiry must run on every exit path (mirrors _persist_metrics's own
    # "called on every exit path" discipline below) — a quiet news day
    # still needs to retire developing issues whose deadline has passed,
    # not just the runs that make it to the main flush/retire stage.
    try:
        expire_stale_developing_issues(db, utcnow())
        retire_stale_vote_drafts(db, today)
        retire_covered_developing_issues(db)
        reword_retired_drafts(db)
    except Exception:
        logger.exception("expire_stale_developing_issues failed (non-fatal)")
    # Committed whichever call above raised: the two early-abort paths
    # below commit nothing of their own, and refresh_action_issues'
    # finally: db.close() would discard it.
    _commit_stage(db, "developing-issue expiry")

    # 1. Fetch articles
    _set_refresh_state(stage="fetch")
    articles = fetch_news_articles()
    if not articles:
        logger.warning("No articles fetched — skipping action center refresh")
        action_metrics.increment("refresh_aborted_no_articles")
        _run_periodic_bluesky_posts(db)
        _persist_metrics(db)
        _set_refresh_state(is_running=False, stage=None)
        return 0
    # Volume counters, recorded on every run so "quiet day" is a number
    # that can be compared against yesterday's, not an inference drawn
    # from the absence of posts.
    action_metrics.increment("articles_fetched", len(articles))

    # The actor tier's threshold, refit once a day from the recorded claim
    # calls before any claim is composed (post_composer.refit_actor_threshold).
    try:
        refit_actor_threshold(db)
    except Exception:
        logger.warning("Actor threshold refit failed; the stored one stands", exc_info=True)

    # The clustering and restatement cuts, refit once a day from the stored
    # articles and issues before either is used (refit_cuts).
    try:
        refit_cuts(db)
    except Exception:
        logger.warning("Cut refit failed; the stored cuts stand", exc_info=True)

    # 2. Filter for policy relevance
    _set_refresh_state(stage="filter")
    relevant = _filter_policy_relevant(db, articles)
    if not relevant:
        logger.warning("No policy-relevant articles found")
        action_metrics.increment("refresh_aborted_no_relevant_articles")
        _run_periodic_bluesky_posts(db)
        _persist_metrics(db)
        _set_refresh_state(is_running=False, stage=None)
        return 0
    action_metrics.increment("articles_policy_relevant", len(relevant))

    # 3. Fetch trending topics from social media
    trending = fetch_trending_topics()

    # 4. Cluster by topic
    _set_refresh_state(stage="cluster")
    clusters = _cluster_articles(relevant)
    # The day's mean title embedding: the "generic news headline" direction.
    # Every per-cluster check below measures in that day-centered title
    # space (see the coherence filter for why not the cluster's own mean).
    title_embs = _embed_texts([a.title for a, _ in relevant])
    day_title_mean = title_embs.mean(axis=0)

    # 5. Rank clusters using coverage breadth + trending relevance
    _set_refresh_state(stage="rank")
    ranked_clusters, ranked_scores = _rank_clusters(clusters, trending, db)

    # 5b. Candidates in rank order, skipping a cluster on the same story as
    # one already published this run (e.g., "Tariff hikes" and "Market
    # fallout from tariffs"). Lazy: `published_clusters` is filled below as
    # clusters publish, and only those count (_deduplicate_top_clusters).
    published_clusters: list[int] = []
    top_clusters = _deduplicate_top_clusters(
        ranked_clusters, ranked_scores, CANDIDATE_POOL, published_clusters,
    )
    _set_refresh_state(stage="issues", stage_detail=f"0/{CANDIDATE_POOL}")

    # 6. Generate analysis for each via LLM

    # Pre-load recent issues for topic-keyed matching.
    # Issues are keyed by TOPIC, not by (date, rank) slot — the same topic
    # always maps to the same DB row regardless of rank or whether it briefly
    # fell out of the top N and came back.
    # DEVELOPING rows are included regardless of date/lookback — their
    # corroboration window is independent of this unrelated "how far back
    # do we re-match ordinary news" tuning knob, and decoupling the two
    # means a longer confirmation_deadline later doesn't silently need a
    # matching change here too.
    _lookback = (datetime.now(_US_EAST) - timedelta(days=REMATCH_WINDOW_DAYS)).strftime("%Y-%m-%d")
    # A retired election-results issue (the count reverted, or the refresh
    # retired it) is left out: promoted, it came back as current news.
    _recent_issues: list[ActionIssue] = (
        db.query(ActionIssue)
        .filter(or_(
            ActionIssue.date >= _lookback,
            ActionIssue.status == ActionIssueStatus.DEVELOPING,
        ))
        .filter(or_(
            ActionIssue.source_type.is_(None),
            ActionIssue.source_type != _ELECTION_RESULTS_SOURCE,
            ActionIssue.is_current.is_(True),
        ))
        .all()
    )
    _recent_embs: "np.ndarray | None" = (
        np.array(_embed_texts_sim([i.title for i in _recent_issues]))
        if _recent_issues else None
    )
    _matched_issue_ids: set[int] = set()  # existing IDs touched this run
    _new_issues: list[ActionIssue] = []   # newly inserted rows (no ID yet)

    issues_created = 0
    stories_built = 0
    # (title, embedding) pairs for post-LLM dedup within a single run
    generated_title_embs: list[tuple[str, "np.ndarray"]] = []

    # The Congress's bills by short title and by id, read once per run
    # (_resolve_bills).
    bill_short_titles = short_title_index(db)
    bill_titles = bill_title_lists(db)
    for rank, (cluster_index, cluster) in enumerate(top_clusters, start=1):
        action_metrics.increment("clusters_attempted")
        _set_refresh_state(stage_detail=f"{rank}/{CANDIDATE_POOL}")
        # Filter the cluster to articles that are genuinely on-topic using
        # centered embeddings — the same space the clustering used. Raw cosine
        # similarity is useless here because every news headline sits in the
        # same high-similarity region; centering removes that bias so only
        # articles that share the cluster's specific topic score highly.
        #
        # Centered on the DAY's mean, never the cluster's own. Subtracting a
        # coherent cluster's own mean subtracts the very topic its articles
        # share, leaving near-zero-sum residuals: the centroid of those is
        # whichever article is noisiest, which then scores 1.00 against it
        # while its siblings score negative. Live logs showed exactly that
        # ("1/5 articles on-topic (sims: 1.00, -0.11, -0.15, -0.25, -0.27)"),
        # and a since-removed topic split broke one Missouri-map cluster into fragments
        # and kept two of eight same-story articles. Measured on the
        # 2026-09-27 feed (9 clusters of 3+): the filter kept 28 articles
        # cluster-centered, 48 day-centered, and every coherent cluster's
        # articles scored 0.47-0.91 instead of one high and the rest negative.
        cluster_titles = [a.title for a in cluster]
        centered_normed = _center_titles(_embed_texts(cluster_titles), day_title_mean)

        c_centroid = centered_normed.mean(axis=0)
        c_norm = float(np.linalg.norm(c_centroid))
        c_centroid = c_centroid / c_norm if c_norm > 0 else c_centroid
        centered_sims = centered_normed @ c_centroid

        # Keep only articles that score above 0.25 similarity to the centroid
        # in centered space. 0.0 (above-average) is too loose when the cluster
        # contains articles about different sub-topics that share one broad
        # dimension (e.g. "Varga administration") — they all score positive.
        # 0.25 requires a meaningful alignment with the cluster's specific topic.
        #
        # MEASURED (7,045 observations over 1,039 persisted runs) — but in
        # the cluster-centered space fixed above (2026-09-27), whose "4,294
        # observations below 0.20" were largely that centering's artifact,
        # not unrelated articles. Day-centered, same-topic articles score
        # 0.47-0.91, so 0.25 still separates them from a stray; the
        # source_coherence_*_sim_bucket counters now record the new space
        # and are what a re-measurement reads.
        #
        # Otsu's method on the same histogram returns 0.35, and that is
        # NOT adopted: Otsu assumes two comparable classes and is pulled
        # upward here by the broad on-topic hump, so it would discard the
        # 0.25-0.35 band — several hundred genuinely on-topic articles per
        # the kept counters — and cluster size is already what limits
        # claim yield. Deriving a number is not a reason to adopt it; the
        # measurement's job here was to confirm the boundary, and it did.
        SOURCE_SIM_FLOOR = 0.25
        for s in centered_sims:
            outcome = "kept" if float(s) >= SOURCE_SIM_FLOOR else "dropped"
            action_metrics.increment_bucket(f"source_coherence_{outcome}_sim_bucket", float(s))
        on_topic = [(a, float(s)) for a, s in zip(cluster, centered_sims) if float(s) >= SOURCE_SIM_FLOOR]
        if not on_topic:
            on_topic = [(cluster[0], 1.0)]

        # A livestream announcement neither titles the issue nor supplies
        # its facts or sources: its headline is an invitation to watch, and
        # what it "claims" ("The president speaks at a midterm rally") is an event
        # that hasn't happened yet when it is published. A cluster of
        # nothing else waits for a report to join it. Replayed on the
        # stored clusters of 2026-10-12/13: "Watch live: The president heads
        # to a state as its Senate race heats up" takes the outlet's report of
        # the trip; a rally covered only by two livestream pages publishes
        # nothing.
        live = announces_broadcast([a.title for a, _ in on_topic], db)
        if any(live):
            action_metrics.increment("articles_dropped_broadcast", sum(live))
            on_topic = [pair for pair, is_live in zip(on_topic, live) if not is_live]
            if not on_topic:
                logger.info("Rank %d: only livestream announcements, no issue until a report joins", rank)
                action_metrics.increment("clusters_only_broadcast")
                continue

        filtered_cluster = [a for a, _ in on_topic]
        # The headline of the top-ranked real article (below) that is not
        # a live blog's headline listing several stories
        # (_multi_story_headline): issue 806 (2026-10-16) was titled by a
        # live blog's "Live updates: <a press secretary's exit>; <a campaign
        # rally>" over a PBS report of the exit alone, brought the rally
        # onto the page, and agreed too little with the PBS-titled issue
        # 805 to be matched to it. A cluster of
        # nothing else waits for a single story's report to join it.
        title = next((a.title for a in filtered_cluster if not _multi_story_headline(a.title)), None)
        if title is None:
            logger.info("Rank %d: only live blogs listing several stories, no issue until a report joins", rank)
            action_metrics.increment("clusters_only_multi_story")
            continue
        logger.info(
            "Rank %d coherence filter: %d/%d articles on-topic (sims: %s)",
            rank, len(filtered_cluster), len(cluster),
            ", ".join("%.2f" % s for _, s in sorted([(a, float(s)) for a, s in zip(cluster, centered_sims)], key=lambda x: -x[1])[:6]),
        )

        resolved_urls: dict[str, str] = {}

        def _url_of(url: str) -> str:
            if url not in resolved_urls:
                resolved_urls[url] = _resolve_url(url)
            return resolved_urls[url]

        seen_sources: dict[str, str] = {}
        for a, _ in on_topic:
            if a.source_name not in seen_sources:
                seen_sources[a.source_name] = _url_of(a.url)
        source_names = list(seen_sources.keys())
        source_urls = list(seen_sources.values())

        # Only ever set from an article whose feed explicitly granted
        # redistribution rights (see news_feeds._rights_cleared_image) —
        # first one found in cluster order, None if none qualify. alt/credit
        # come from that SAME article, not independently searched.
        image_article = next((a for a in filtered_cluster if a.image_url), None)
        image_url = image_article.image_url if image_article else None
        image_alt = image_article.image_alt if image_article else ""
        image_credit = image_article.image_credit if image_article else ""

        # --- Claims, not prose -------------------------------------
        # The model locates assertions; claims.py verifies each is
        # verbatim AND asserted of its actor, then renders it. See
        # claims.py and post_composer.py for the three published
        # failures that made free-form generation untenable, and for
        # what this shape makes impossible rather than merely
        # detectable.
        # The SAME articles extraction reads. These diverged when
        # extraction moved to the full cluster and this did not: a claim
        # taken from an article outside the coherent subset was then
        # checked against a source text that excluded its own article,
        # and the backstop rejected real issues with "numbers not in
        # source: 1, 11". Any grounding check is only as correct as its
        # definition of "the source".
        issue_source_text = " ".join(
            f"{a.title} {a.summary}" for a in cluster
        )

        def _locate(source_material: str, _rank: int = rank) -> dict | None:
            return locate_claim(source_material, db=db, today=today, rank=_rank)

        # Extract from the WHOLE cluster, not the coherence-filtered
        # subset. SOURCE_SIM_FLOOR exists to keep a free-writing model on
        # topic by removing stray articles before it sees them; the claim
        # layer now does that job better and at the right granularity, by
        # checking each extracted CLAIM against the cluster.
        #
        # Keeping both compounded: the first production run of this
        # redesign published ZERO issues. Its counters show the coherence
        # filter had cut both top clusters to 5 articles between them, and
        # at the measured ~39% per-article yield that is about one claim
        # per cluster against a two-claim substance gate. Two filters each
        # reasonable alone, multiplying into nothing.
        #
        # filtered_cluster still supplies the title and the source list —
        # those should come from the coherent core.
        # Headline claims first: they lead (build_lede takes the first),
        # and an earlier article is the better-sourced account. Then the
        # second claim each article's summary holds (extract_body_claims,
        # the same prompt and verification, and the call _build_full_story
        # makes anyway, so the model's answer is cached for it). Since
        # complete linkage, most clusters are one or two articles; with
        # one claim per article, a one-article story could never reach the
        # two-claim gate below however much it said (2026-10-01: 78 of 300
        # clusters in three days skipped as too few facts).
        # Deduped together, so a summary claim that only restates a
        # headline one (or contains it) can't make one fact count as two.
        # Claims come from the on-topic articles only: one from an article
        # the coherence filter dropped was published with a source the
        # issue doesn't list (2026-10-08).
        # A live blog's headline lists several stories, so its headline
        # claim may be another story's; its body still contributes, and
        # on_topic judges that.
        headline_claims = claim_layer.extract_claims(
            [a for a in filtered_cluster if not _multi_story_headline(a.title)], _locate,
        )
        kept = claim_layer.dedupe_claims(headline_claims + claim_layer.extract_body_claims(filtered_cluster, _locate))
        ordered = [c for c in headline_claims if c in kept] + [c for c in kept if c not in headline_claims]
        cluster_claims = claim_layer.on_topic(ordered, filtered_cluster)
        # One fact told twice is one fact: a headline and its own lede, or
        # two outlets' wordings, would otherwise pass the two-claim gate
        # below and fill the facts with the summary again (distinct_claims).
        cluster_claims = claim_layer.distinct_claims(cluster_claims)
        if not cluster_claims:
            # A cluster with no attributable fact produces NO ISSUE.
            # Measured on a live run, roughly one top cluster in four is
            # like this, so MAX_ISSUES is a ceiling rather than a quota.
            # Publishing anyway is what produced "This coverage tracks
            # the race and related developments."
            logger.info("No verifiable claim in cluster rank %d — no issue", rank)
            action_metrics.increment("clusters_without_claims")
            continue

        # The headline of the top-ranked real article. A real outlet's
        # own headline cannot hallucinate, and this was already the
        # proven-safe fallback whenever a generated title failed — it is
        # now the only path.
        summary = claim_layer.build_lede(cluster_claims)
        # The lede is a quoted claim like the facts, so it carries its
        # outlet too: without it, the outlet whose line became the summary
        # appeared only in the source list, and a reader saw fewer outlets
        # under "In the coverage" than under sources (2026-10-01).
        summary_source, summary_source_url = cluster_claims[0].source_name, cluster_claims[0].source_url
        # Facts are the SUPPORTING claims — the lede already states the
        # headline one. Passing the whole list made the summary a
        # verbatim duplicate of fact 1, which the first end-to-end run
        # against live articles showed immediately (a summit story: the
        # summary and the first key fact were the same sentence).
        facts, fact_sources, fact_source_urls = claim_layer.build_facts(cluster_claims[1:])
        # Every article a published line quotes is listed, under the same
        # resolved URL. The list keeps one article per outlet, so a line
        # from an outlet's second article, or one stored under its Google
        # News redirect, linked to a source the issue didn't list
        # (5 of 119 issues since 2026-09-01).
        summary_source_url = _url_of(summary_source_url)
        fact_source_urls = [_url_of(u) for u in fact_source_urls]
        for quoted_name, quoted_url in [(summary_source, summary_source_url), *zip(fact_sources, fact_source_urls)]:
            if quoted_url and quoted_url not in source_urls:
                source_names.append(quoted_name)
                source_urls.append(quoted_url)

        # Backstop, not the primary defence. Every word here is either a
        # verbatim span or a real outlet's headline, so this should
        # always pass — which is exactly why it is worth running: it is
        # what would catch a regression in compose() itself, and it keeps
        # this path inside the one shared validation surface every other
        # generator uses. Same posture as election_bluesky's composed
        # post. Fail closed: a cluster that somehow produces ungrounded
        # text publishes nothing rather than being retried into shape.
        composed_text = f"{title} {summary} " + " ".join(facts)
        reasons = grounding_violations(composed_text, issue_source_text)
        reasons += hedge_and_editorializing_violations(composed_text)
        # No model wrote this: it is composed from verified spans, so its
        # input is the source text it was composed from and checked
        # against. The calls that located the spans are recorded by
        # locate_claim.
        _record_generation_sample(
            db, "action_center_issue", rank, 1, issue_source_text,
            {"title": title, "summary": summary, "facts": facts},
            passed=not reasons, violations=reasons or None,
        )
        if reasons:
            logger.warning(
                "Composed issue for rank %d failed the backstop (%s) — no issue",
                rank, "; ".join(reasons)[:200],
            )
            action_metrics.increment("issues_skipped_grounding")
            continue

        # _check_summary_roles is GONE from this path.
        #
        # It existed because a free-written summary could invert who did
        # what to whom — issue #376 said "E. Jean Carroll was found
        # guilty" when she was the plaintiff. The summary is now a
        # verbatim span the source asserts OF its actor, adjacency
        # checked (see post_composer._asserted_together), so that
        # inversion is unreachable rather than merely unlikely.
        #
        # Keeping it was costing real issues. It is a 1.2B model judging
        # another model's work, and on "Senate Republican seeks to
        # fast-track AI whistleblower bill" — a verbatim headline — it
        # objected that the summary "attributes the billing and legal
        # consequences to entities not mentioned". It was fail-open by
        # design for exactly that unreliability; I had made it fail
        # closed, which turned one bad judgement into a deleted issue.
        #
        # A check that cannot catch anything the shape already prevents,
        # and does reject correct content, is not defence in depth.

        _log_summary_source_consistency(summary, issue_source_text)
        log_intensifier_usage(
            "action_center_issue", summary + " " + " ".join(facts), issue_source_text,
        )

        # Minimum-substance gate (2026-07 audit): an issue resting on a
        # single piece of evidence isn't a publishable issue — observed
        # live as issues of pure filler ("Congressional scheduling
        # adjustments": no name, no number, no bill anywhere).
        #
        # Counted in CLAIMS rather than facts since the redesign: the
        # lede is itself a claim, so two claims means a headline fact
        # plus one corroborating fact — the same "at least two pieces of
        # evidence" bar the original gate set, now that `facts` holds
        # only the supporting ones.
        if len(cluster_claims) < 2:
            logger.info(
                "Skipping rank %d — only %d claim(s) survived: '%s'",
                rank, len(cluster_claims), title[:60],
            )
            action_metrics.increment("issues_skipped_too_few_facts")
            continue

        # Post-LLM title dedup: skip if this LLM-generated title is too
        # similar to one already selected this run. Catches cases where two
        # article clusters were distinct enough to pass pre-LLM embedding
        # dedup (threshold 0.50) but the LLM distills them to the same headline.
        title_emb = _embed_texts_sim([title])[0]
        # 0.60 measured under the similarity model (2026-07-22): a
        # reworded same headline scores 0.823, different stories 0.251.
        # (Was 0.92 on the retrieval model's compressed scale.)
        if any(float(title_emb @ prev_emb) >= 0.60 for _, prev_emb in generated_title_embs):
            logger.info(
                "Skipping duplicate issue rank %d (title too similar to earlier issue): '%s'",
                rank, title[:80],
            )
            action_metrics.increment("issues_skipped_duplicate_title")
            continue
        generated_title_embs.append((title, title_emb))

        if not isinstance(facts, list):
            facts = []
        policy_areas = _classify_issue_policy_areas(title, summary)

        # 7. The bills the articles themselves name (_resolve_bills).
        article_texts = [f"{a.title} {a.summary}" for a in cluster]
        resolved_bills = _resolve_bills(article_texts, bill_short_titles, bill_titles)
        if resolved_bills:
            logger.info("  Resolved %d bill(s): %s",
                        len(resolved_bills),
                        ", ".join(b["id"] for b in resolved_bills))

        # 8. Find related explore documents
        related_docs = _find_related_explore_docs(title, summary, policy_areas, db)
        related_explore_ids = [d["id"] for d in related_docs]

        # 9. Find senators/reps mentioned in this issue (backward compat field)
        related_senators = _find_related_senators(
            title, summary, facts, db,
            article_texts=[f"{a.title}. {a.summary}" for a in filtered_cluster],
        )

        # 9b. Find ALL officials (senators + reps + president + justices) for profile cross-links
        related_officials = _find_related_officials(title, summary, facts, db)

        # Action-surface gate: an issue must connect to something a citizen
        # can actually act on in platform data. Replaces the prototype-
        # similarity civic gate, whose absolute cosine threshold (0.30) sat
        # below the embedding model's same-register noise floor and never
        # fired (2026-07 audit: celebrity-crime and music-performance
        # stories passed). Derived entirely from what the platform has
        # ingested, not from authored prototype text.
        #
        # Only HIGH-PRECISION anchors count (2026-07 audit tightening —
        # a World Cup story published as a civic issue by clearing the old
        # any-anchor version of this gate twice over): a resolved bill or a
        # full-name-matched official is near-certain; a last-name-only
        # official match is not (the story's anchor was soccer player
        # Ferran Torres surname-matching two Reps. Torres), and a SINGLE
        # explore-doc title match at the 0.75 similarity bar is not either
        # (the same story's other anchor was a Puerto Rico PROMESA floor
        # speech at that bar). Two independent explore docs agreeing
        # remains a valid anchor; one alone doesn't publish an issue.
        strong_officials = [
            o for o in related_officials
            if o.get("match_reason") == "named in coverage"
        ]
        if not (resolved_bills or strong_officials or len(related_explore_ids) >= 2):
            logger.info(
                "Skipping rank %d — no strong action surface (no bills, no "
                "full-name officials, <2 civic documents): '%s'",
                rank, title[:60],
            )
            action_metrics.increment("issues_skipped_no_action_surface")
            continue

        # 10. Build data-driven actions (no LLM hallucinations). After the
        # gate: a comment period or a committee is something to do about an
        # issue, not evidence that it is one.
        try:
            comment_docs = _open_comment_docs(title, related_docs, db)
        except Exception:
            logger.exception("Open comment period lookup failed (non-fatal)")
            comment_docs = []
        related_explore_ids += [d["id"] for d in comment_docs if d["id"] not in related_explore_ids]
        try:
            committees = route_committees(title, summary)
        except Exception:
            logger.exception("Committee routing failed (non-fatal)")
            committees = []
        action_metrics.increment("issues_with_committee", bool(committees))
        action_metrics.increment("issues_with_comment_period", bool(comment_docs))
        actions = _build_actions_from_data(
            title, resolved_bills, source_urls, source_names, related_senators, policy_areas,
            committees=committees, comment_docs=comment_docs,
        )

        # Track the date of the newest article driving this cluster so the
        # Bluesky poster can frame posts as "yesterday" or include the date
        # when reporting on events that didn't happen today.
        newest_pub = max(
            (a.published for a in filtered_cluster if a.published is not None),
            default=None,
        )
        primary_article_date = (
            newest_pub.astimezone(_US_EAST).strftime("%Y-%m-%d")
            if newest_pub is not None else today
        )

        # Who the issue is principally about (story_actor), for the admin's
        # weekly actor concentration.
        actor = _principal_actor(title, summary, related_officials)
        # The states and races it names, for its ballot line (ballot_links).
        issue_ballot_links = ballot_links(db, ballot_cycle, title, summary, facts)

        # Find the matching existing issue across all recent issues (2-day
        # lookback, any rank). Same STORY → same row, regardless of rank
        # yo-yoing or a brief displacement — see _find_matching_issue's
        # docstring for the matching rules and their history.
        match = _find_matching_issue(
            title, facts, _recent_issues, _recent_embs, title_emb, _matched_issue_ids,
            source_urls=source_urls, summary=summary, db=db,
            source_titles={_url_of(a.url): a.title for a in filtered_cluster},
        )

        _new_values: dict = {
            "title": title[:500],
            "summary": summary,
            "facts": json.dumps(facts),
            "actions": json.dumps(actions),
            # Aligned with `facts` by index. Verbatim quoting without
            # attribution reads as plagiarism; with it, it reads as
            # evidence — and a reader can check any fact against the
            # outlet that made it.
            "fact_sources": json.dumps(fact_sources),
            "fact_source_urls": json.dumps(fact_source_urls),
            "summary_source": summary_source,
            "summary_source_url": summary_source_url,
            "source_urls": json.dumps(source_urls),
            "source_names": json.dumps(source_names),
            "policy_areas": json.dumps(policy_areas),
            "related_bill_ids": json.dumps(resolved_bills),
            "related_explore_ids": json.dumps(related_explore_ids),
            # By permanent key too: the ids are rows, which get replaced.
            "related_explore_keys": json.dumps(keys_for_ids(db, related_explore_ids)),
            "related_senators": json.dumps(related_senators),
            "related_officials": json.dumps(related_officials),
            "ballot_links": json.dumps(issue_ballot_links),
            "primary_article_date": primary_article_date,
            "image_url": image_url,
            "image_alt": image_alt,
            "image_credit": image_credit,
        }

        # The slot this issue fills, not its candidate position: the third
        # candidate publishing because the first two failed is issue #1.
        slot = issues_created + 1
        if match:
            _matched_issue_ids.add(match.id)
            match.principal_actor = actor
            if match.status == ActionIssueStatus.DEVELOPING:
                _promote_developing_issue(
                    match, _new_values, slot, today, primary_article_date, facts, title,
                )
            else:
                _apply_matched_issue_update(
                    match, _new_values, slot, today, primary_article_date, facts, title,
                )
            # Split from the new-topic case below: `issues_created` counts
            # both, so a run that only ever re-matched yesterday's stories
            # reported the same number as one that found four fresh ones.
            # That distinction is the whole difference between "the news is
            # quiet" and "new topics are being dropped by a gate".
            action_metrics.increment("issues_matched_existing")
        else:
            # Brand new topic — give it a permanent row and post to Bluesky.
            new_row = ActionIssue(date=today, rank=slot, is_current=True, principal_actor=actor, **_new_values)
            db.add(new_row)
            _new_issues.append(new_row)
            action_metrics.increment("issues_new_topic")
            logger.info("Rank %d new topic: '%s'", rank, title[:60])

        # Only a row without a story gets one: new, just promoted, or
        # cleared because its title or facts changed. A developing draft
        # has one primary source and no reporting to tell yet.
        row = match or new_row
        if row.full_story is None and row.status != ActionIssueStatus.DEVELOPING:
            _set_refresh_state(stage_detail=f"story for rank {rank}")
            row.full_story = _build_full_story(
                filtered_cluster, cluster_claims, summary, facts,
                issue_source_text, _locate,
            )
            if row.full_story:
                stories_built += 1

        issues_created += 1
        published_clusters.append(cluster_index)
        # Stop before asking for another candidate: the generator counts
        # each one it yields as tried.
        if issues_created >= MAX_ISSUES:
            break

    # Flush to assign IDs to newly inserted rows, then mark them as touched.
    db.flush()
    for ni in _new_issues:
        _matched_issue_ids.add(ni.id)

    # Retire issues not touched in this run, but only after a grace period.
    # 24h, not the original 90 minutes: reader feedback that issues "come
    # and go too quickly" turned out to be mostly a SUPPLY problem (2026-08
    # audit via admin_action_metrics: the grounding-rejection gate alone
    # was suppressing enough candidates that 39 of 48 hourly runs produced
    # zero new topics — see _retry_until_grounded), not a retirement-timing
    # one — but 90 minutes was still too short on its own merits once
    # supply is healthier: a story that stops clustering the moment it
    # crosses that age gets pulled the very next run, even if it's still
    # the most substantive thing on the board. Every issue now gets a
    # guaranteed full day in the record before it can be retired purely
    # for not re-matching, matching how a register reads: today's docket
    # doesn't quietly shrink hour to hour.
    _grace_cutoff = utcnow() - timedelta(hours=_RETIREMENT_GRACE_HOURS)
    all_current = (
        db.query(ActionIssue)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    n_retired, n_graced = _retire_untouched_issues(all_current, _matched_issue_ids, _grace_cutoff)
    # A vote draft whose bill this run's reporting covers as its own issue
    # gives way to it now, not an hour from now (early_signal).
    db.flush()
    retire_covered_developing_issues(db)
    if n_retired:
        logger.info("Retired %d stale issues not in current clusters", n_retired)
    if n_graced:
        logger.info("Spared %d recent issues from retirement (within grace period)", n_graced)

    # Renumber ranks so they are unique and dense. Skipped clusters leave
    # gaps in this run's enumerate ranks, and grace-period survivors keep
    # a stale rank from an earlier run — both produced duplicate ranks on
    # the public page (two simultaneous #4s, 2026-07 audit). Issues placed
    # by this run keep their relative order first; spared survivors follow.
    still_current = [r for r in all_current if r.is_current]
    for new_rank, row in enumerate(_rank_ordered_issues(still_current, _matched_issue_ids), start=1):
        row.rank = new_rank

    db.commit()

    # Stage 2: Auto-detect and update national monitors (must run before
    # _save_timeline_entry so the top issue has related_monitor_slugs populated)
    _set_refresh_state(stage="monitors", stage_detail=None,
                       last_issues_created=issues_created, last_issues_retired=n_retired)
    # Every run, not only when issues were created: the lifecycle step ages
    # monitors toward watching and closed, and a run of quiet days used to
    # leave a monitor active for as long as they lasted.
    _update_national_monitors(today, db)

    # Preserve today's #1 issue in the permanent timeline
    _set_refresh_state(stage="timeline")
    if issues_created > 0:
        _save_timeline_entry(today, db)
        generate_period_summaries(today, db)

    # Rows written before ballot links were matched get theirs here.
    try:
        link_issue_ballots(db, ballot_cycle)
    except Exception:
        logger.exception("Linking issues to the ballot failed")
        db.rollback()

    # Rows published before principal actors were recorded get theirs here,
    # where the model is loaded; after the first run there are none left.
    try:
        label_principal_actors(db)
    except Exception:
        logger.exception("Labelling issues' principal actors failed")
        db.rollback()

    # The homepage feed's duplicates, marked here where the model is loaded
    # (mark_recent_duplicates). Every run, not only when issues were
    # created: a retirement or a re-rank changes which rows it reads.
    try:
        mark_recent_duplicates(db)
    except Exception:
        logger.exception("Marking the recent feed's duplicates failed")
        db.rollback()

    # Rows written before ActionIssue.new_facts existed get it here, where
    # the model is loaded; the API reads the stored value and never diffs.
    try:
        backfill_new_facts(db)
        db.commit()
    except Exception:
        logger.exception("Backfilling issues' new facts failed")
        db.rollback()

    # Related votes in Congress (app/roll_call_links.py): matched here, where
    # both models are loaded, for the last week's issues and monitor updates
    # and a bounded batch of older ones never matched.
    try:
        roll_call_links.refresh_links(db, today)
        db.commit()
    except Exception:
        logger.exception("Matching issues to roll calls failed")
        db.rollback()

    # Stage 4: Post new/surging issues to Bluesky.
    # Deliberately runs BEFORE full-story generation below: posting only
    # needs title/summary/facts, never full_story, but used to run after
    # it anyway. A single full_story call can legitimately take up to 600s
    # x 3 retries (see ollama_client.call_llm) — on a slow/degraded local
    # LLM, that stage alone can run long enough to eat the entire hourly
    # window, and posting never got a turn (confirmed live 2026-07-13:
    # zero Bluesky posts for ~21h while story generation stalled every
    # cycle). Posting first means a slow story backlog no longer blocks it.
    _set_refresh_state(stage="bluesky", stage_detail=None)
    if issues_created > 0:
        try:
            from app.pipeline.analyze.bluesky_poster import process_issues_for_bluesky
            today_issues = _bluesky_eligible_issues(db, today)
            bsky_posted = process_issues_for_bluesky(today_issues, db)
            _set_refresh_state(last_bsky_posted=bsky_posted or 0)
        except Exception:
            logger.exception("Bluesky posting failed (non-fatal)")

    _set_refresh_state(last_stories_generated=stories_built)

    # Stage 6: Daily senator score spotlight + weekly civic summary
    _run_periodic_bluesky_posts(db)

    # Stage 7: Repost/like news outlet posts that match active issues
    try:
        from app.pipeline.analyze.bluesky_engagement import engage_with_news_posts
        engage_with_news_posts(db)
    except Exception:
        logger.exception("Bluesky engagement failed (non-fatal)")

    _cleanup_old_unposted_issues(db)
    _prune_stale_api_cache(db)

    # Persist this run's validator counters (2026-07 audit M9: these
    # existed only as log lines, wiped by every deploy — validator hit
    # rates were unmeasurable). One api_cache row per run, pruned by the
    # same 60-day cleanup as every other tier.
    _persist_metrics(db)

    elapsed = time.perf_counter() - t0
    logger.info(
        # Most published issues re-match an existing row: "2 issues
        # created" for two refreshed rows read as two new stories.
        "Action center refresh complete: %d issues published (%d new) in %.1fs",
        issues_created, len(_new_issues), elapsed,
    )
    _set_refresh_state(
        is_running=False, stage=None, stage_detail=None,
        last_completed_at=utcnow(),
        last_elapsed=round(elapsed, 1),
    )
    return issues_created


def _principal_actor(title: str, summary: str, officials: list[dict]) -> str:
    """The issue's principal actor (story_actor): the official named first in
    its headline, else the class voted by the relevance gate's labelled
    examples. The story is embedded only when its headline names no official."""
    named = story_actor.headline_official(title, officials)
    if named is not None:
        return named
    return story_actor.story_class(
        _embed_texts_sim([f"{title}. {summary}"])[0],
        _relevance_reference(), [e["category"] for e in relevance_examples()],
    )


def link_issue_ballots(db: Session, cycle: int) -> int:
    """Match ballot links (ballot_links) for every news issue that has none
    yet: the rows from before the column existed."""
    rows = (
        db.query(ActionIssue)
        .filter(ActionIssue.ballot_links.is_(None), ActionIssue.source_type.is_(None))
        .all()
    )
    for row in rows:
        try:
            facts = json.loads(row.facts or "[]")
        except (TypeError, ValueError):
            facts = []
        row.ballot_links = json.dumps(ballot_links(db, cycle, row.title, row.summary or "", facts))
    if rows:
        db.commit()
        logger.info("Linked %d earlier issues to the ballot", len(rows))
    return len(rows)


def label_principal_actors(db: Session) -> int:
    """Record the principal actor (story_actor) of every news issue that has
    none yet: the rows from before the column existed. Officials are matched
    afresh, under today's rules, as a new issue's would be."""
    rows = (
        db.query(ActionIssue)
        .filter(ActionIssue.principal_actor.is_(None), ActionIssue.source_type.is_(None))
        .all()
    )
    if not rows:
        return 0
    for row in rows:
        try:
            facts = json.loads(row.facts or "[]")
        except (TypeError, ValueError):
            facts = []
        officials = _find_related_officials(row.title, row.summary or "", facts, db)
        row.principal_actor = _principal_actor(row.title, row.summary or "", officials)
    db.commit()
    logger.info("Labelled the principal actor of %d earlier issues", len(rows))
    return len(rows)


def _cleanup_old_unposted_issues(db: Session) -> int:
    """Delete unposted issues older than 14 days. Issues that have been
    posted to Bluesky are preserved indefinitely so their permalink URLs
    remain valid. Extracted for direct testability (no mocking the rest
    of _run_refresh's many stages).

    "Unposted" is bsky_last_post_text IS NULL, not bsky_posted_at IS
    NULL. bsky_posted_at does not mean "has been published" — the repost
    path clears it to hand the issue back to the poster (see
    _apply_matched_issue_update), so an issue that published, was later
    flagged for a repost, then failed to publish it (two grounding
    rejections, or a publish error) or simply stopped being matched sits
    at NULL indefinitely while a real post pointing at
    /issue/<id> is live in the feed. Deleting that row 404s a link
    readers can still click, which is exactly what the docstring above
    promises not to do. bsky_last_post_text is only ever written on a
    successful publish and never cleared, so it is the honest record of
    "readers have a URL for this". Near-duplicate suppression
    deliberately leaves it NULL: nothing was published, so there is no
    permalink to protect.

    Only DEVELOPING issues are ever deleted. A confirmed issue has a
    permalink whether or not it was posted: the sitemap lists every one,
    current or retired, as the issue archive (api/sitemap.py), and the
    Action Center linked it the day it ran. While nearly every issue was
    posted this rule rarely bit; since only the day's top two are posted
    (bluesky_poster.MAX_ISSUE_POSTS_PER_DAY), it would have 404'd most of
    the archive two weeks on. A developing issue that was never confirmed
    or published is noindex and in no sitemap, so it can go.
    """
    # Against `date`, an Eastern day (time_utils.eastern_date).
    cutoff = (eastern_date(utcnow()) - timedelta(days=14)).isoformat()
    deleted = (
        db.query(ActionIssue)
        .filter(
            ActionIssue.date < cutoff,
            ActionIssue.status == ActionIssueStatus.DEVELOPING,
            ActionIssue.bsky_posted_at.is_(None),
            ActionIssue.bsky_last_post_text.is_(None),
        )
        .delete()
    )
    # Committed even with nothing matched: the statement opened the write
    # transaction either way (database.WRITE_HOLD_WARN_S).
    db.commit()
    if deleted:
        logger.info("Cleaned up %d old unposted action issues", deleted)
    return deleted


def _prune_stale_api_cache(db: Session) -> int:
    """Delete api_cache entries older than 60 days to bound unbounded
    growth. Extracted for direct testability, same reasoning as
    _cleanup_old_unposted_issues above."""
    from app.models import ApiCache

    cache_cutoff = (utcnow() - timedelta(days=60)).isoformat()
    cache_deleted = (
        db.query(ApiCache)
        .filter(ApiCache.cached_at < cache_cutoff)
        .delete()
    )
    # Committed even with nothing matched: the statement opened the write
    # transaction either way (database.WRITE_HOLD_WARN_S).
    db.commit()
    if cache_deleted:
        logger.info("Pruned %d stale api_cache entries", cache_deleted)
    return cache_deleted
