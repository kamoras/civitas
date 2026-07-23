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

import calendar
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import (
    ActionIssue,
    ExploreDocument,
    Justice,
    MonitorStatus,
    MonitorUpdate,
    NationalMonitor,
    President,
    Representative,
    Senator,
)
from app.pipeline.analyze.score_calculator import compute_overall_score
from app.pipeline.fetch.news_feeds import NewsArticle, fetch_news_articles
from app.pipeline.fetch.trending import TrendingTopic, fetch_trending_topics
from app.pipeline.vector_store import (
    get_embedding_model,
    search_explore_documents,
)
from app.time_utils import utcnow

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

_US_CIVIC_PROTOTYPES = [
    "US Congress bill vote legislation Senate House passed signed",
    "President executive order White House federal policy decision",
    "US Supreme Court federal court ruling constitutional law decision",
    "US military action Pentagon American troops deployed strikes",
    "Federal agency regulation EPA FDA FTC FCC rule enforcement policy",
    "American workers economy domestic policy jobs wages US",
    "US federal budget spending deficit appropriations government shutdown",
    "US election voting rights ballot federal electoral",
    "US immigration border policy ICE deportation federal",
    "US healthcare Medicare Medicaid insurance federal program policy",
]

# Measured under the similarity model (2026-07-22, real July articles):
# civic headlines score 0.354-0.583 against the prototypes, non-civic
# (World Cup / pop tour / recipes) 0.027-0.053 — threshold sits mid-gap
# with wide margin on both sides. (Was 0.22 under the retrieval model,
# where the same gap was paper-thin.)
POLICY_RELEVANCE_THRESHOLD = 0.20
CLUSTER_TITLE_THRESHOLD = 0.40
# Floor of the self-calibrating pass-2 merge scan (see _cluster_articles).
CLUSTER_CENTROID_MERGE_THRESHOLD = 0.20
MAX_ISSUES = 4

# v20 -> v21 (2026-07 audit M6): added fact rule (6) against cross-topic
# facts. Live issues carried facts from unrelated articles that survived
# cluster-coherence filtering (a Zelenskyy army-chief fact inside a
# Netanyahu-arrest issue; a PhRMA fact inside a cyclospora-outbreak
# issue). A mechanical per-fact check was evaluated first and rejected on
# measurement: fact-vs-title cosine on the production model does NOT
# separate contaminants from legitimate facts (measured on the live
# cases: contaminants 0.72-0.76, legitimate facts 0.73-0.97 — overlapping
# ranges), so this class is addressed at the prompt and disclosed as not
# mechanically enforced.
ACTION_CENTER_PROMPT_VERSION = "action-v21"

# No-signature fallback for topic matching (rows with no stored facts —
# rare). Measured under the similarity model (2026-07-22): a reworded
# same headline scores 0.823, a different-story same-vocab pair 0.552 —
# 0.65 splits them. Signature overlap (see _signatures_match) remains
# the primary same-story decider; this fires only when signatures are
# unavailable. (The old 0.82 was calibrated to the retrieval model's
# compressed 0.74+ band and is meaningless on this scale.)
TOPIC_CHANGE_THRESHOLD = 0.65


def _full_story_should_invalidate(
    old_title: str, old_facts: str, new_title: str, new_facts: str,
) -> bool:
    """True if an issue's title/facts changed enough that its cached
    full_story (if any) now describes the wrong event and must be
    regenerated rather than left stale.

    A topic-similarity match (TOPIC_CHANGE_THRESHOLD) can still land on a
    substantively different story sharing a category — e.g. two different
    senators' health events both matching "ailing senior senator". full_story
    is only ever generated once per issue (Stage 4 filters on
    ``full_story IS NULL``), so if the row's content is silently replaced
    without also clearing full_story, the page keeps showing old text about
    a different event indefinitely. (2026-07 bug: a McConnell hospitalization
    story's full_story survived a re-match onto a later Lindsey Graham
    obituary issue.)
    """
    return old_title != new_title or old_facts != new_facts

# Tokens that appear in most political stories and therefore carry no
# STORY identity — a signature made of these matches everything. Identity
# comes from specific entities and numbers ("216-212", "Taylor Farms",
# "Netanyahu"), never from the shared civic vocabulary. Calibrated against
# real production pairs (2026-07 audit, see _issue_signature): with these
# stripped, two same-story rows measured overlap 0.78/1.00 while a
# different-story pair whose TITLES scored 0.88 cosine measured 0.0.
_SIGNATURE_GENERIC_TOKENS = {
    "the", "a", "an", "this", "these", "those", "several", "multiple",
    "recent", "new", "over", "under", "after", "before", "while", "with",
    "house", "senate", "congress", "congressional", "president",
    "republicans", "republican", "democrats", "democrat", "democratic",
    "gop", "lawmakers", "federal", "government", "state", "states",
    "white", "washington", "american", "americans", "bill", "act",
    "committee", "administration", "officials", "legislation",
}


def _issue_signature(title: str, facts: list[str]) -> set[str]:
    """Identity fingerprint of an issue: specific capitalized entities plus
    digit groups from its title and facts, minus generic civic vocabulary
    (see _SIGNATURE_GENERIC_TOKENS). Two rows about the same real-world
    story share the numbers and named entities that define it; two rows
    that merely sound alike share only the generic tokens this strips."""
    text = f"{title} {' '.join(facts)}"
    tokens = {
        m.group(0).lower()
        for m in re.finditer(r"\b[A-Z][a-zA-Z'\-]{2,}\b", text)
    } - _SIGNATURE_GENERIC_TOKENS
    numbers = {
        m.group(0).replace(",", "")
        for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)
    }
    return tokens | numbers


# Minimum signature containment for two issues to be the same story, and
# the minimum shared-token count backing it (containment over a tiny
# signature is noisy). Calibrated 2026-07 on real production pairs: the
# two same-story pairs that WRONGLY became separate rows (defense bill
# id394/id405, cyclospora id396/id401) measure 0.78 and 1.00; the
# different-story pair that WRONGLY shared a row (post/permalink drift,
# audit H1) measures 0.0 after generic-token stripping. Wide gap, so the
# threshold sits in the middle of it.
_SIGNATURE_MATCH_MIN_CONTAINMENT = 0.35
_SIGNATURE_MATCH_MIN_SHARED = 2

# Candidate floor for topic matching by title similarity — a compute
# guard, not a decider (signature overlap decides same-story; see
# _run_refresh's matching loop). Measured under the similarity model
# (2026-07-22, real production titles): SAME-story pairs score as low as
# +0.134 (the cyclospora pair) while a fully unrelated pair scores
# +0.082 — title cosine cannot decide identity under ANY model tested
# (a different-story same-vocab pair outscored every same-story pair at
# +0.552), which is exactly why signatures carry the decision. The floor
# only excludes clearly-unrelated candidates from signature comparison.
_TOPIC_MATCH_CANDIDATE_FLOOR = 0.10


def _signatures_match(sig_a: set[str], sig_b: set[str]) -> bool:
    if not sig_a or not sig_b:
        return False
    shared = sig_a & sig_b
    containment = len(shared) / min(len(sig_a), len(sig_b))
    return (
        len(shared) >= _SIGNATURE_MATCH_MIN_SHARED
        and containment >= _SIGNATURE_MATCH_MIN_CONTAINMENT
    )


def _is_exact_content_duplicate(
    title: str, facts: list, cand_title: str, cand_facts: list,
) -> bool:
    """Byte-identical title+facts always means the same issue, checked
    BEFORE signature overlap. _signatures_match requires >=
    _SIGNATURE_MATCH_MIN_SHARED (2) tokens even for an exact match, but a
    sparse, single-entity story (signature {'trump'}, nothing else
    extractable) can never clear that floor — even against itself. Live
    2026-07-23 bug: the same source article reprocessed an hour later
    produced a second row (ids 420/421, "Republicans introduce crypto
    legislation...") with title/facts equal but a 1-token signature, so it
    silently created a duplicate instead of matching."""
    return title == cand_title and facts == cand_facts


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

_ISSUE_PROMPT_TEMPLATE = """\
Below are recent news articles about the same U.S. policy issue. \
Analyze ONLY the topic covered in these specific articles. \
Do NOT reference bills, policies, or events not mentioned in the articles. \
Produce a JSON object with these fields:

- "title": A concise, neutral headline for this issue (max 15 words). \
Name the actual countries or entities involved. Do NOT add "U.S." or \
"America" to the title unless the United States is a direct actor in \
these specific articles.
- "summary": A factual 2-4 sentence summary of what is happening and why \
it matters. No opinion — do not state or imply that an action was \
warranted, justified, or reasonable, even if a source article frames it \
that way; report what was done and said, not whether it was right. Report \
directly — never write "reports say," "coverage indicates," or similar. \
Use the SPECIFIC names, quotes, and numbers from the articles rather than \
vaguer paraphrases — if an article names a person or gives a figure, use it \
instead of a vaguer substitute like "a commentator" or "officials." Do not \
write about "the coverage" or "the reporting" as the subject of a sentence \
(e.g., "the coverage emphasizes personal connections") — report what \
actually happened or was said, not a description of what the source \
article chose to discuss. \
Be precise about WHO did WHAT to WHOM — double-check \
the direction of every action and legal outcome before writing it. In legal \
or disputed matters, do not confuse the accuser/plaintiff/victim with the \
accused/defendant, and never state that someone was "found guilty" or \
"found liable" unless the articles say THAT SPECIFIC PERSON was the one \
found guilty or liable, not the person who brought the case against them.
- "facts": An array of 3-5 key factual bullet points citizens should know. \
Each fact must cite specific numbers, dates, or names when available. \
CRITICAL fact rules: (1) Every fact must be directly stated in the articles — \
never infer or extrapolate. (2) Comparisons must name TWO DISTINCT entities — \
never write "X surpasses X" or compare a thing to itself. (3) If an article \
says something was dropped, dismissed, or ended, the fact must reflect that \
outcome — do not write that it is ongoing. (4) Extract only concrete, \
checkable actions and events — never extract an article's opinion, spin, or \
argument about whether an action was warranted or justified, even when the \
article states it as fact. (5) A fact must describe something that happened \
or was said in the world — never a description of the coverage itself \
(e.g., "the coverage emphasizes X" or "the article focuses on Y" are NOT \
facts; extract what X or Y actually is instead). (6) Every fact must be \
about the single topic named in your title. If an article in the list \
covers a different event — a different country's politics, a different \
agency, an unrelated person — do NOT extract facts from it, even though \
it appears above.
- "bills": An array of any specific bills or acts mentioned in the articles. \
For each bill, provide an object with "name" (the bill's common name or \
acronym EXACTLY AS WRITTEN in the articles above — never a bill name from \
these instructions or from your own knowledge) and "id": ALWAYS null. Never \
invent or guess a bill number — leave "id" null even if you think you know \
it. The bill number will be looked up separately. Only include bills \
actually named in the articles. This will usually be empty — if NO bill or \
act is named in the articles, return an empty array [].
Articles:
{articles}

Respond with ONLY the JSON object."""


def _build_actions_from_data(
    title: str,
    resolved_bills: list[dict],
    source_urls: list[str],
    source_names: list[str],
    related_senators: list[dict],
) -> list[dict]:
    """Build action items from real data — no LLM hallucinations.

    Senator contact is handled by the frontend SenatorChips component when
    senators are named. Here we only emit actions we have real URLs for.
    """
    actions: list[dict] = []

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
    # so the frontend has something to show in that case
    if not related_senators:
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
            "User-Agent": "Mozilla/5.0 (compatible; Civitas/1.0)",
        })
        final = str(resp.url)
        if "google.com" not in final:
            logger.debug("Resolved Google News URL → %s", final[:100])
            return final
    except Exception:
        logger.debug("Google News URL resolution (HEAD) failed for %s", url[:100], exc_info=True)
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Civitas/1.0)",
        })
        final = str(resp.url)
        if "google.com" not in final:
            logger.debug("Resolved Google News URL (GET) → %s", final[:100])
            return final
    except Exception:
        logger.debug("Google News URL resolution (GET) failed for %s", url[:100], exc_info=True)
    return url


_ROUNDUP_PATTERNS = re.compile(
    r"\b(week(?:ly)? in (?:politics|review|news)|week(?:'?s)? (?:top |best )?(?:news|stories|headlines)"
    r"|this week in|news of the week|political news this week|what we('re| are) watching"
    r"|week(?:ly)? (?:wrap-?up|round-?up))\b",
    re.IGNORECASE,
)


def _filter_policy_relevant(
    articles: list[NewsArticle],
) -> list[tuple[NewsArticle, np.ndarray]]:
    """Keep only articles about US policy/legislation; drop roundup articles."""
    if not articles:
        return []

    # Drop weekly-roundup/digest articles before embedding — they cover multiple
    # unrelated topics and contaminate cluster centroids, producing catch-all issues.
    specific = []
    for a in articles:
        if _ROUNDUP_PATTERNS.search(a.title):
            logger.debug("Filtered roundup article: %s", a.title[:80])
        else:
            specific.append(a)

    if not specific:
        return []

    prototype_embeddings = _embed_texts_sim(_POLICY_PROTOTYPES)
    us_civic_embeddings = _embed_texts_sim(_US_CIVIC_PROTOTYPES)

    texts = [f"{a.title}. {a.summary[:200]}" for a in specific]
    article_embeddings = _embed_texts_sim(texts)

    # Max-over-prototypes: an article is relevant if it scores high against ANY
    # policy prototype, not just the average direction. The mean collapses 18
    # diverse prototypes into one diffuse vector that sits below the news-headline
    # floor for nearly every article.
    policy_scores = (article_embeddings @ prototype_embeddings.T).max(axis=1)
    us_civic_scores = (article_embeddings @ us_civic_embeddings.T).max(axis=1)
    # Gently penalize articles that are policy-relevant but have no US actor —
    # purely foreign-domestic stories require stronger policy relevance to pass.
    effective_scores = policy_scores * np.where(us_civic_scores >= 0.15, 1.0, 0.82)

    relevant: list[tuple[NewsArticle, np.ndarray]] = []
    for i, (article, score) in enumerate(zip(specific, effective_scores)):
        if score >= POLICY_RELEVANCE_THRESHOLD:
            relevant.append((article, article_embeddings[i]))

    n_penalized = int(np.sum(us_civic_scores < 0.15))
    logger.info(
        "Policy relevance filter: %d/%d articles passed "
        "(%d roundups dropped, %d low-US-civic penalized, threshold=%.2f)",
        len(relevant), len(articles),
        len(articles) - len(specific), n_penalized, POLICY_RELEVANCE_THRESHOLD,
    )
    return relevant


def _agglomerative_cluster(
    sim_matrix: np.ndarray,
    threshold: float,
) -> list[list[int]]:
    """Run greedy agglomerative clustering on a precomputed similarity matrix."""
    n = sim_matrix.shape[0]
    clusters: list[list[int]] = []
    cluster_map: dict[int, int] = {}
    assigned: set[int] = set()

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((float(sim_matrix[i, j]), i, j))
    pairs.sort(reverse=True)

    for score, i, j in pairs:
        if score < threshold:
            break
        ci = cluster_map.get(i)
        cj = cluster_map.get(j)
        if ci is not None and cj is not None:
            if ci != cj:
                src, dst = (cj, ci) if len(clusters[ci]) >= len(clusters[cj]) else (ci, cj)
                for idx in clusters[src]:
                    cluster_map[idx] = dst
                clusters[dst].extend(clusters[src])
                clusters[src] = []
        elif ci is not None:
            clusters[ci].append(j)
            cluster_map[j] = ci
            assigned.add(j)
        elif cj is not None:
            clusters[cj].append(i)
            cluster_map[i] = cj
            assigned.add(i)
        else:
            new_id = len(clusters)
            clusters.append([i, j])
            cluster_map[i] = new_id
            cluster_map[j] = new_id
            assigned.update([i, j])

    for i in range(n):
        if i not in assigned:
            clusters.append([i])

    return [c for c in clusters if c]


# A second sub-cluster must have at least this many articles, and be at
# least this share of the cluster, to count as a genuine second topic
# rather than a couple of stray outliers already handled by SOURCE_SIM_FLOOR.
_CLUSTER_SPLIT_MIN_SUBGROUP_SIZE = 2
_CLUSTER_SPLIT_MIN_SUBGROUP_SHARE = 0.25


def _largest_coherent_subgroup(sim_matrix: np.ndarray, threshold: float) -> list[int]:
    """Indices of the largest sub-cluster in ``sim_matrix``, or all indices
    if it doesn't meaningfully split.

    _cluster_articles' pass-2 centroid merge only guards against one
    cluster swallowing most of the day's articles (its size cap) — it
    doesn't stop two roughly-balanced unrelated topics from landing in one
    small cluster, and SOURCE_SIM_FLOOR's centroid-distance filter can't
    catch that either: a centroid sitting between two topics of similar
    size scores both of them "above floor" toward their own shared
    midpoint (observed 2026-07: an issue titled "political risks from war
    in Iran; ICE escalates tension" merged two unrelated policy areas).
    Re-clustering just this cluster's own similarity matrix at the same
    threshold pass 1 uses to decide "same specific issue" catches a real
    topic split that distance-from-centroid alone misses. This can only
    ever keep one topic — the larger one is kept and the rest dropped
    rather than inventing a combined title for two different issues.
    """
    n = sim_matrix.shape[0]
    subgroups = _agglomerative_cluster(sim_matrix, threshold)
    if len(subgroups) <= 1:
        return list(range(n))
    sizes = sorted((len(g) for g in subgroups), reverse=True)
    if sizes[1] >= _CLUSTER_SPLIT_MIN_SUBGROUP_SIZE and sizes[1] / n >= _CLUSTER_SPLIT_MIN_SUBGROUP_SHARE:
        return sorted(max(subgroups, key=len))
    return list(range(n))


def _cluster_articles(
    items: list[tuple[NewsArticle, np.ndarray]],
) -> list[list[NewsArticle]]:
    """Group articles about the same civic issue using two-pass clustering.

    News outlets cover the same issue with very different framing —
    "Iran missiles hit Israel", "Oil prices surge from Iran conflict",
    and "Rising gas prices imperil Republican majority" are all facets
    of one civic issue. A single-pass approach at a conservative
    threshold treats them as separate stories.

    Pass 1 — title-only embeddings at CLUSTER_TITLE_THRESHOLD:
        Re-embeds just the headline (stripping summary noise) so articles
        sharing the same event/subject merge even if their angle differs.

    Pass 2 — centroid merge at CLUSTER_CENTROID_MERGE_THRESHOLD:
        Computes each cluster's centroid and merges clusters that are
        still close enough to be the same broad topic. This catches
        different-angle coverage (military vs economic vs political)
        that may not share enough title keywords to merge in pass 1.
    """
    if not items:
        return []

    # Pass 1: cluster on title-only embeddings (less source-specific noise).
    # Center embeddings before computing similarity — the embedding model places
    # all English news headlines in a tight cluster (~0.74 median cosine sim),
    # so raw similarity is uninformative. Subtracting the batch mean removes the
    # "generic news article" component and makes topic-specific dimensions dominate.
    titles = [a.title for a, _ in items]
    title_embeddings = _embed_texts(titles)
    mean_emb = title_embeddings.mean(axis=0, keepdims=True)
    centered = title_embeddings - mean_emb
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    centered_embs = centered / np.where(norms < 1e-9, 1.0, norms)
    title_sim = centered_embs @ centered_embs.T

    pass1 = _agglomerative_cluster(title_sim, CLUSTER_TITLE_THRESHOLD)
    logger.info(
        "Clustering pass 1 (titles, threshold=%.2f): %d articles → %d clusters",
        CLUSTER_TITLE_THRESHOLD, len(items), len(pass1),
    )

    # Pass 2: merge clusters whose centroids are close (using centered embeddings)
    if len(pass1) > 1:
        centroids = np.zeros((len(pass1), centered_embs.shape[1]))
        for ci, indices in enumerate(pass1):
            centroid = centered_embs[indices].mean(axis=0)
            norm = np.linalg.norm(centroid)
            centroids[ci] = centroid / norm if norm > 0 else centroid

        centroid_sim = centroids @ centroids.T

        # The merge is single-link, so a fixed threshold chain-merges on
        # bad days: at 0.20 one 2026-07 run collapsed 121/125 articles
        # into a single cluster spanning NATO, a Senate race, and a
        # toddler human-interest story, leaving only junk as separate
        # issues. Instead of a magic constant, self-calibrate per run:
        # scan upward from the floor and keep the most aggressive merge
        # whose largest cluster stays within a sanity cap — one story
        # can dominate a news day, but not be most of it.
        n_articles = len(items)
        size_cap = max(8, round(0.20 * n_articles))
        merge_groups = pass1_groups = [[ci] for ci in range(len(pass1))]
        chosen_threshold = None
        for threshold in np.arange(CLUSTER_CENTROID_MERGE_THRESHOLD, 0.61, 0.05):
            candidate = _agglomerative_cluster(centroid_sim, float(threshold))
            largest = max(sum(len(pass1[ci]) for ci in g) for g in candidate)
            if largest <= size_cap:
                merge_groups = candidate
                chosen_threshold = float(threshold)
                break
        if chosen_threshold is None:
            merge_groups = pass1_groups
            logger.warning(
                "Centroid merge skipped — every scanned threshold produced a "
                "cluster larger than %d articles", size_cap,
            )
        else:
            logger.info(
                "Centroid merge threshold self-calibrated to %.2f "
                "(largest cluster ≤ %d articles)",
                chosen_threshold, size_cap,
            )

        merged: list[list[int]] = []
        for group in merge_groups:
            combined: list[int] = []
            for ci in group:
                combined.extend(pass1[ci])
            merged.append(combined)
    else:
        merged = pass1

    result: list[list[NewsArticle]] = []
    for cluster_indices in merged:
        result.append([items[idx][0] for idx in cluster_indices])

    logger.info(
        "Clustering pass 2 (centroids, threshold=%.2f): %d → %d clusters",
        CLUSTER_CENTROID_MERGE_THRESHOLD, len(pass1), len(result),
    )
    return result


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


def _count_official_mentions(cluster_text: str, officials: dict) -> int:
    """Count distinct tracked officials named in a cluster's coverage.

    A member counts on a full-name match, or on a titled surname match
    ("Sen. Collins", "Rep. Crockett") — the title disambiguates surnames
    that are also common words, so no stoplist is needed. The current
    president counts on a bare surname (word-boundary) match.
    """
    text_lower = cluster_text.lower()
    titled_surnames = {
        m.group(1).lower() for m in _TITLED_SURNAME_RE.finditer(cluster_text)
    }

    count = 0
    for full, last in zip(officials["member_full"], officials["member_last"]):
        if last in titled_surnames or full in text_lower:
            count += 1

    if officials["president_last"]:
        if re.search(
            r"\b" + re.escape(officials["president_last"]) + r"\b", text_lower
        ) or officials["president_full"] in text_lower:
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
) -> list[list[NewsArticle]]:
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
    """
    if not clusters:
        return []

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

    return [clusters[i] for i in ranked_indices]


def _deduplicate_top_clusters(
    ranked_clusters: list[list[NewsArticle]],
    max_issues: int,
) -> list[list[NewsArticle]]:
    """Select top clusters ensuring no two cover the same topic.

    Greedily picks the highest-ranked cluster, then skips any subsequent
    cluster whose centroid is too similar to an already-selected one.
    With two-pass clustering, most merging happens earlier; this is a
    final safety net before LLM analysis.

    Remaining duplicates that slip through are merged into the earlier
    selected cluster rather than discarded, so their articles contribute
    to the LLM prompt for that issue.
    """
    if len(ranked_clusters) <= 1:
        return ranked_clusters[:max_issues]

    # Threshold in normalized-centered-embedding space. Must be high enough that
    # only genuinely same-story clusters are merged; 0.15 was too loose and caused
    # unrelated clusters (e.g. abortion, World Cup) to be merged into Ukraine.
    DEDUP_THRESHOLD = 0.50

    cluster_texts = [
        " ".join(a.title for a in cluster[:5])
        for cluster in ranked_clusters
    ]
    raw_embs = _embed_texts(cluster_texts)
    mean_emb = raw_embs.mean(axis=0, keepdims=True)
    centered = raw_embs - mean_emb
    norms = np.linalg.norm(centered, axis=1, keepdims=True)
    embeddings = centered / np.where(norms < 1e-9, 1.0, norms)

    selected: list[int] = []
    for i in range(len(ranked_clusters)):
        if len(selected) >= max_issues:
            break

        merged_into = None
        for j in selected:
            sim = float(embeddings[i] @ embeddings[j])
            if sim >= DEDUP_THRESHOLD:
                merged_into = j
                break

        if merged_into is not None:
            ranked_clusters[merged_into].extend(ranked_clusters[i])
            logger.info(
                "Merged cluster '%s...' into '%s...' (sim=%.3f)",
                ranked_clusters[i][0].title[:40],
                ranked_clusters[merged_into][0].title[:40],
                float(embeddings[i] @ embeddings[merged_into]),
            )
        else:
            selected.append(i)

    logger.info(
        "Cluster dedup: selected %d of %d ranked clusters",
        len(selected), len(ranked_clusters),
    )
    return [ranked_clusters[i] for i in selected]


def _validate_facts(facts: list, source_text: str | None = None) -> list:
    """Drop hallucinated or self-referential facts before saving.

    Catches five LLM failure modes:
    - Self-comparison: "Meta surpasses Meta Platforms" — same root word on both sides
    - Non-list return: LLM occasionally wraps facts in a dict or returns a string
    - Stale future dates: fact says "will remain until December 2025" in June 2026
    - Fabricated statistics: when ``source_text`` (the article texts the LLM
      was shown) is provided, any fact containing a digit group that never
      appears in the source is dropped. The prompt already forbids inferred
      numbers; this enforces it mechanically (see grounding.py).
    - Meta-facts: "No specific dates were provided in the articles" describes
      the coverage's limits, not something that happened in the world — the
      prompt's fact rule (5) already forbids this, but unlike the other
      three prompt-only rules above it had no mechanical backstop, and it's
      the single most common failure mode of the smaller LFM2.5-1.2B model
      (2026-07-16 swap, #96) on real production output — spotted live on
      2026-07-19 issues: "No specific dates or names of the bills were
      provided in the articles," "Specific details about security
      protocols were mentioned but not expanded in the articles," "No
      formal policy changes or legal actions were reported in the
      coverage." All three self-reference the source material as the
      fact's subject instead of describing an actual event.
    """
    if not isinstance(facts, list):
        return []

    import re as _re

    _DATE_MONTH_YEAR = _re.compile(
        r'\b(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+(\d{4})\b',
        _re.IGNORECASE,
    )
    _FORWARD_PHRASES = ("will remain", "is expected", "continue", "until ", "through ", "by the end")
    _META_PHRASES = (
        "in the article", "in the articles", "in the coverage", "in the report",
        "in the reporting", "the coverage ", "the reporting ",
        # 2026-07 audit: "The articles focused on internal party dynamics
        # rather than public policy outcomes" and "The articles referenced
        # specific names and dates" both published — "the articles" as
        # sentence SUBJECT wasn't covered by the "in the articles" form.
        "the article ", "the articles ",
    )
    _today = datetime.now(_US_EAST).date()
    _month_index = {m: i for i, m in enumerate(calendar.month_name) if m}

    from app.pipeline.analyze import action_metrics
    from app.pipeline.analyze.grounding import (
        placeholder_tokens,
        ungrounded_former_official_claims,
        ungrounded_relationship_claims,
    )

    clean = []
    for fact in facts:
        if not isinstance(fact, str) or not fact.strip():
            continue
        lower = fact.lower()

        # Literal unfilled placeholders — "[date]", "[name]". Every other
        # check here is digit-based, which is exactly how "Thune announced
        # the tribute details on [date]." published (and was posted to
        # Bluesky) in 2026-07: no digits, nothing fired.
        placeholders = placeholder_tokens(fact)
        if placeholders:
            logger.warning("Dropping fact with placeholder tokens (%s): %s",
                           ", ".join(placeholders), fact[:120])
            action_metrics.increment("facts_dropped_placeholder")
            continue

        # Family-relationship claims with no family vocabulary anywhere in
        # the source articles — same fabricated-relationship class as the
        # electoral-claims guard (2026-07: "for the seat left by her
        # brother" published as fact with nothing able to check it).
        if source_text and ungrounded_relationship_claims(fact, source_text):
            logger.warning("Dropping fact with ungrounded family relationship: %s", fact[:120])
            action_metrics.increment("facts_dropped_relationship")
            continue

        # "Former <office>" status the source articles never asserted — the
        # stale-training-data class (2026-07: "former President Donald
        # Trump" published while the sources said "President Trump").
        if source_text and ungrounded_former_official_claims(fact, source_text):
            logger.warning("Dropping fact with ungrounded 'former' office-holder status: %s", fact[:120])
            action_metrics.increment("facts_dropped_former_status")
            continue

        # Detect self-referential comparisons: extract capitalized words and check
        # if any word root appears on both sides of a comparison verb.
        comparison_verbs = ("surpass", "overtake", "exceed", "beat", "top", "outpace")
        if any(verb in lower for verb in comparison_verbs):
            words = _re.findall(r"\b[A-Z][a-z]{3,}\b", fact)
            roots = [w.lower()[:6] for w in words]  # stem to first 6 chars
            if len(roots) != len(set(roots)):  # duplicate root → self-comparison
                logger.warning("Dropping self-referential fact: %s", fact[:120])
                action_metrics.increment("facts_dropped_self_comparison")
                continue

        # Detect stale future-tense facts with past dates — e.g. the LLM writes
        # "the ban will remain until December 2025" when it's now June 2026.
        stale = False
        if any(phrase in lower for phrase in _FORWARD_PHRASES):
            for m in _DATE_MONTH_YEAR.finditer(fact):
                month_num = _month_index.get(m.group(1).capitalize(), 0)
                if month_num == 0:
                    continue
                try:
                    mentioned = datetime(int(m.group(2)), month_num, 1).date()
                    if mentioned < _today:
                        logger.warning("Dropping stale future-dated fact: %s", fact[:120])
                        action_metrics.increment("facts_dropped_stale_date")
                        stale = True
                        break
                except ValueError:
                    pass
        if stale:
            continue

        # Detect meta-facts that describe the source material's limits rather
        # than an actual event — see the docstring for real production examples.
        if any(phrase in lower for phrase in _META_PHRASES):
            logger.warning("Dropping meta-fact referencing the coverage itself: %s", fact[:120])
            action_metrics.increment("facts_dropped_meta")
            continue

        # Fabricated-statistic check against the articles the LLM was shown.
        if source_text:
            from app.pipeline.analyze.grounding import ungrounded_numbers
            novel = ungrounded_numbers(fact, source_text)
            if novel:
                logger.warning(
                    "Dropping fact with numbers not in source (%s): %s",
                    ", ".join(novel), fact[:120],
                )
                action_metrics.increment("facts_dropped_ungrounded_number")
                continue

        clean.append(fact.strip())

    return clean


_ROLE_PATTERNS = [
    # Matches "U.S. Senator Name", "Senator Name", "Sen. Name"
    (re.compile(
        r'\b(?:U\.?S\.?\s+)?(?:Senator|Sen\.)\s+([A-Z][a-zA-Z\.\'-]+(?:\s+[A-Z][a-zA-Z\.\'-]+){0,2})',
    ), "Senator"),
    # Matches "U.S. Representative Name", "Representative Name", "Rep. Name",
    # "Congressman Name", "Congresswoman Name"
    (re.compile(
        r'\b(?:U\.?S\.?\s+)?(?:Representative|Rep\.|Congressman|Congresswoman)\s+'
        r'([A-Z][a-zA-Z\.\'-]+(?:\s+[A-Z][a-zA-Z\.\'-]+){0,2})',
    ), "Representative"),
]

# Words stripped before comparing extracted names to DB names
_ROLE_STRIP = {"senator", "sen", "rep", "representative", "congressman", "congresswoman",
               "u.s", "us", "former", "the", "honorable", "hon"}


def _name_in_table(extracted: str, known_names: list[str]) -> bool:
    """Return True if extracted name shares at least one substantive token with any known name."""
    tokens = {t.lower().rstrip(".") for t in extracted.split()} - _ROLE_STRIP
    if not tokens:
        return False
    for known in known_names:
        known_tokens = {t.lower().rstrip(".") for t in known.split()}
        if tokens & known_tokens:
            return True
    return False


def _validate_politician_roles(
    title: str,
    summary: str,
    facts: list[str],
    db: "Session",
) -> tuple[str, str, list[str]]:
    """Strip hallucinated legislative titles from LLM-generated content.

    The LLM occasionally labels politicians with the wrong role
    (e.g. calling a Cabinet Secretary a "Senator"). For each "Senator X" or
    "Representative X" pattern found in the generated text, the name is
    verified against the senators / representatives tables. If no match is
    found the role prefix is removed, leaving just the name.
    """
    from app.models import Senator, Representative

    senator_names = [s.name for s in db.query(Senator).all()]
    rep_names = [r.name for r in db.query(Representative).all()]

    def _fix(text: str) -> str:
        for pattern, role in _ROLE_PATTERNS:
            for m in pattern.finditer(text):
                extracted = m.group(1)
                known = senator_names if role == "Senator" else rep_names
                if not _name_in_table(extracted, known):
                    # "Former Senator X" / "Ex-Rep. X" is a historical claim,
                    # not a hallucinated current role — we can only verify
                    # CURRENT membership, and stripping just the role word
                    # produced garbled text ("Former Mitt Romney").
                    preceding = text[max(0, m.start() - 8):m.start()].lower()
                    if preceding.rstrip().endswith(("former", "ex-", "ex")):
                        continue
                    # Remove the role prefix — keep just the name
                    logger.warning(
                        "Role hallucination corrected: '%s' is not a %s — stripping role label",
                        extracted, role,
                    )
                    text = text.replace(m.group(0), extracted, 1)
        return text

    title = _fix(title)
    summary = _fix(summary)
    facts = [_fix(f) for f in facts]
    return title, summary, facts


def _build_llm_prompt(cluster: list[NewsArticle]) -> str:
    parts: list[str] = []
    for a in cluster[:8]:
        line = f"[{a.source_name}] {a.title}"
        if a.summary:
            line += f"\n  {a.summary[:300]}"
        parts.append(line)
    return _ISSUE_PROMPT_TEMPLATE.format(articles="\n\n".join(parts))


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
    if not prev or not prev[0].isupper() or len(prev) < 3:
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
) -> list[dict]:
    """Find senators mentioned in issue text using embedding similarity.

    Uses a two-pass approach:
    1. Substring scan for last-name / full-name hits, with disambiguation
       to reject matches where the name is used in an institutional context
       (e.g. "Department of Justice" should not match Senator Justice).
    2. Embedding fallback when no substring matches are found.
    """
    senators = db.query(
        Senator.id, Senator.name, Senator.state, Senator.party,
        Senator.score_funding_independence, Senator.score_promise_persistence,
        Senator.score_independent_voting, Senator.score_funding_diversity,
        Senator.score_legislative_effectiveness,
        Senator.leadership_score, Senator.contact_form_url, Senator.website_url,
    ).all()

    representatives = db.query(
        Representative.id, Representative.name, Representative.state, Representative.party,
        Representative.score_funding_independence, Representative.score_promise_persistence,
        Representative.score_independent_voting, Representative.score_funding_diversity,
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

    # Pass 1: substring matches with contextual disambiguation
    candidates_needing_disambiguation: list[tuple] = []

    all_members = [(s, "senate") for s in senators] + [(r, "house") for r in representatives]

    # Full-name matches first, over every member, before any last-name-only
    # candidacy is considered — 2026-07 fix: a same-surname collision (30+
    # exist in the current roster: Smith x5, Johnson x5, Moore x5, Graham
    # x2, etc.) used to slip through undetected, because the single-pass
    # version below checked each member independently — Sen. Lindsey Graham
    # got fuzzy-matched onto a story that was actually about candidate
    # Darline Graham (also SC, also just "Graham" in the text), since
    # nothing yet knew her full-name match already fully accounts for
    # every "Graham" in the piece. Collecting every confirmed full-name
    # match FIRST means a shared surname is only ever a live disambiguation
    # candidate for whichever single member wasn't already explained by a
    # more specific match.
    full_name_matched_last_names: set[str] = set()
    for s, chamber in all_members:
        full_name_lower = s.name.lower() if s.name else ""
        if full_name_lower and full_name_lower in issue_text_lower:
            matched[s.id] = _make_entry(s, chamber, match_reason="named in coverage")
            full_name_matched_last_names.add(s.name.split()[-1].lower())

    for s, chamber in all_members:
        if s.id in matched:
            continue

        last_name = s.name.split()[-1].lower() if s.name else ""

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
        # Pass 1 for the Graham/Graham case this fixes).
        if last_name in full_name_matched_last_names:
            continue

        # Last-name-only match needs word-boundary + disambiguation
        pattern = re.compile(r"\b" + re.escape(last_name) + r"\b", re.IGNORECASE)
        occurrences = list(pattern.finditer(issue_text))
        if occurrences and all(
            _surname_owned_by_other_name(issue_text, m, s.name) for m in occurrences
        ):
            # Every occurrence of this surname is part of a DIFFERENT
            # person's full name ("Ferran Torres" is not Rep. Ritchie
            # Torres). Generalizes the full-name-matched-member exclusion
            # above to people the platform doesn't track — the 2026-07
            # audit found a World Cup story tagging both Reps. Torres
            # ("referenced in coverage") off soccer player Ferran Torres'
            # surname, and the embedding disambiguation below is provably
            # unable to catch this: measured on the live case, the sports
            # context scored 0.78-0.80 against the "Representative X from
            # NY" prototypes while genuine civic last-name references
            # scored 0.77-0.85 — the ranges overlap completely, so no
            # threshold separates them.
            continue
        if occurrences:
            candidates_needing_disambiguation.append((s, last_name, pattern, chamber))

    if candidates_needing_disambiguation:
        senator_phrases = []
        context_phrases = []
        candidate_refs = []

        for s, last_name, pattern, chamber in candidates_needing_disambiguation:
            if s.id in matched:
                continue
            # Was hardcoded "Senator" for every candidate, including the
            # ~435 House members — weakening disambiguation quality for
            # every Representative match, since the prototype phrase
            # didn't match their actual title.
            title = "Senator" if chamber == "senate" else "Representative"
            senator_phrases.append(f"{title} {s.name} from {s.state}")

            # Extract ~60 chars of context around each match
            contexts = []
            for m in pattern.finditer(issue_text):
                start = max(0, m.start() - 30)
                end = min(len(issue_text), m.end() + 30)
                contexts.append(issue_text[start:end].strip())
            context_phrases.append(" | ".join(contexts[:3]))
            candidate_refs.append((s, chamber))

        if senator_phrases:
            all_texts = senator_phrases + context_phrases
            embeddings = _embed_texts(all_texts)
            n = len(senator_phrases)
            senator_embeds = embeddings[:n]
            context_embeds = embeddings[n:]

            DISAMBIGUATION_THRESHOLD = 0.35
            for i, (s, chamber) in enumerate(candidate_refs):
                sim = float(np.dot(senator_embeds[i], context_embeds[i]))
                if sim >= DISAMBIGUATION_THRESHOLD:
                    matched[s.id] = _make_entry(s, chamber, match_reason="referenced in coverage")
                else:
                    logger.debug(
                        "Rejected senator match '%s' (sim=%.3f < %.2f) — "
                        "likely institutional reference",
                        s.name, sim, DISAMBIGUATION_THRESHOLD,
                    )

    result = list(matched.values())
    if result:
        logger.info("Found %d related senators for '%s': %s",
                     len(result), title[:50],
                     ", ".join(s["name"] for s in result))
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
    import re

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

    # Justice detection — last-name + embedding disambiguation (same as senators)
    justices = db.query(
        Justice.id, Justice.name, Justice.appointing_party,
    ).filter(Justice.is_active == True).all()  # noqa: E712

    if justices:
        candidates_needing_disambiguation: list[tuple] = []
        matched_justices: dict[str, dict] = {}
        DISAMBIGUATION_THRESHOLD = 0.35

        for j in justices:
            last = j.name.split()[-1]
            if len(last) < 4:
                continue
            full_match = j.name.lower() in text.lower()
            if full_match:
                matched_justices[j.id] = {
                    "id": j.id, "name": j.name, "party": j.appointing_party or "R",
                    "branch": "scotus", "match_reason": "named in coverage",
                }
                continue
            # Same common-word-surname gap as senators/reps — Justice
            # Ketanji Brown Jackson's surname is a common place name
            # ("Jackson, Mississippi") and everyday word.
            if last.lower() in _COMMON_WORD_SURNAMES:
                continue
            pattern = re.compile(r"\b" + re.escape(last) + r"\b", re.IGNORECASE)
            m = pattern.search(text)
            if m:
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                context = text[start:end]
                candidates_needing_disambiguation.append((j, context))

        if candidates_needing_disambiguation:
            justice_phrases = [f"Justice {j.name}" for j, _ in candidates_needing_disambiguation]
            context_phrases = [ctx for _, ctx in candidates_needing_disambiguation]
            try:
                justice_embeds = np.array(_embed_texts(justice_phrases))
                context_embeds = np.array(_embed_texts(context_phrases))
                for i, (j, _) in enumerate(candidates_needing_disambiguation):
                    sim = float(np.dot(justice_embeds[i], context_embeds[i]))
                    if sim >= DISAMBIGUATION_THRESHOLD:
                        matched_justices[j.id] = {
                            "id": j.id, "name": j.name, "party": j.appointing_party or "R",
                            "branch": "scotus", "match_reason": "referenced in coverage",
                        }
            except Exception as exc:
                logger.debug("Justice embedding disambiguation failed: %s", exc)

        for entry in matched_justices.values():
            if not any(e["id"] == entry["id"] for e in combined):
                combined.append(entry)

    return combined


def _classify_issue_policy_areas(title: str, summary: str) -> list[str]:
    """Classify an action center issue into its policy area using embeddings.

    Uses the same embedding-based classifier as bills (tier 2) rather than
    relying on the LLM, which inconsistently returns empty or wrong labels.
    Single-area only — see classify_policy_areas_multi's docstring for why
    secondary-area detection was removed (2026-07 audit: raw cosine
    similarity across the category anchors can't distinguish genuine
    secondary relevance from noise for this embedding model).
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


# Calibrated 2026-07 against live production data: sampled ~15 real
# Action Center issues, computed both ChromaDB L2 distance (gate 1, full
# document text) and title-only cosine similarity (gate 2, see below)
# against their retrieved candidates, and hand-labeled each as a genuine
# topical match or not. At the prior 1.10/0.40, essentially every issue
# was returning 2-3 unrelated documents (confirmed live: a World Cup
# soccer story matched to Chinese steel anti-dumping notices; an Attorney
# General story matched to an unrelated advisory-committee meeting
# notice) — neither gate was tight enough to reject a topic with no real
# government-document counterpart in the corpus (most non-legislative
# news: sports, foreign elections, market moves, celebrity legal news).
# The observed score bands: genuinely unrelated topics cluster at
# distance > 0.87; the best real match found in the sample (a data-center
# buildout story matched to the actual data-center-permitting executive
# order) sat at 0.80. Tightened to 0.85 rather than exactly at that
# boundary, since a single sample isn't enough to trust to the second
# decimal — this is a real improvement, not a perfect fix (see
# _ADMINISTRATIVE_NOTICE_TITLE_RE below for the one false-positive
# pattern tight enough to survive this threshold).
_EXPLORE_DOC_MAX_DISTANCE = 0.85

# Paperwork Reduction Act information-collection notices and Federal
# Advisory Committee Act meeting notices use fixed, legally-templated
# title phrasing (5 CFR 1320 / 41 CFR 102-3) and are — by the nature of
# what they announce (a routine data-collection renewal, an upcoming
# committee meeting) — never substantively about any particular news
# story, regardless of how their titles happen to embed. This is the one
# false-positive pattern that survives _EXPLORE_DOC_MAX_DISTANCE/min_sim
# tightening above: confirmed live, a "Notice of Public Meeting of the
# Montana Advisory Committee" and an "Agency Information Collection
# Activities" notice both scored well inside the "genuine match" distance
# and similarity bands for unrelated issues. Matching the fixed template
# phrasing (not a list of specific bad titles — every notice using this
# legally-mandated language is generic, not just the ones seen so far) is
# the same "measure the real property, don't guess" principle as
# GENERIC_TITLE_REPEAT_THRESHOLD above, just for a structural pattern
# repeat-counting can't catch since each notice is uniquely titled.
# "proposed collection...comment request" and "solicitation of
# nominations" (PRA/FACA phrasing variants) added after live
# re-verification post-deploy caught them still leaking through on real
# production titles the first pass didn't sample.
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
      1. ChromaDB L2 distance must be below ``_EXPLORE_DOC_MAX_DISTANCE``
      2. Reciprocal similarity: the candidate doc title is embedded against
         the issue title alone (not summary), and only kept if it scores in
         the top ``max_docs`` by similarity *and* exceeds the adaptive
         threshold (median similarity of the candidate pool).

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

    # Re-measured under the similarity model (2026-07-22, the same live
    # cases that exposed the PROMESA/World Cup false anchor): genuine
    # issue-doc matches score 0.467-0.776, unrelated floor-speech noise
    # 0.128-0.183 — 0.33 sits mid-gap. (The old 0.75 was fit to the
    # retrieval model's compressed band, where noise reached 0.84 and
    # this bar still admitted the World Cup/PROMESA pair.)
    min_sim = 0.33

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


CONGRESS_API_BASE = "https://api.congress.gov/v3"


def _congress_ordinal(congress: int) -> str:
    """Ordinal form ("119th", "101st", "112th") — congress.gov bill URLs
    embed it, and a wrong suffix (e.g. "101th") 404s."""
    if 11 <= congress % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(congress % 10, "th")
    return f"{congress}{suffix}"


def _congress_gov_bill_url(congress: int, url_type: str, number: str | int) -> str:
    return (
        f"https://www.congress.gov/bill/"
        f"{_congress_ordinal(congress)}-congress/{url_type}/{number}"
    )


def _resolve_bills(raw_bills: list, article_texts: list[str]) -> list[dict]:
    """Resolve bill names from LLM output + article text to Congress.gov URLs.

    Regex-extracted bill IDs (e.g. "H.R. 22") are resolved first since they
    map directly to URLs.  LLM-extracted names without IDs fall back to API
    search only if no regex match already covers that bill.

    Returns list of {"name": str, "id": str, "url": str} dicts.
    """
    id_refs: list[dict] = []
    name_refs: list[dict] = []

    # Scan article text for bill patterns like "H.R. 22", "S. 1234"
    combined_text = " ".join(article_texts)
    bill_pattern = re.compile(
        r'\b(H\.?\s*R\.?\s*\d+|S\.?\s*\d+|H\.?\s*J\.?\s*Res\.?\s*\d+'
        r'|S\.?\s*J\.?\s*Res\.?\s*\d+)\b',
        re.IGNORECASE,
    )
    seen_raw: set[str] = set()
    for match in bill_pattern.finditer(combined_text):
        raw_id = re.sub(r'\s+', '', match.group(0)).upper()
        raw_id = raw_id.replace("H.R.", "HR.").replace("HR", "HR.")
        raw_id = re.sub(r'\.+', '.', raw_id)
        if not raw_id.startswith("S."):
            raw_id = raw_id.replace("S", "S.")
            raw_id = re.sub(r'\.+', '.', raw_id)
        if raw_id not in seen_raw:
            seen_raw.add(raw_id)
            id_refs.append({"name": raw_id, "id": raw_id})

    # Collect LLM-extracted bills — always search by name, never trust LLM IDs.
    # LLMs frequently hallucinate bill numbers (e.g. "S.2026" when the year is 2026).
    # Regex extraction from article text above is the only source of trusted IDs.
    #
    # Also require the extracted NAME itself to appear verbatim in the source
    # articles before trusting it — confirmed live 2026-07: a smaller LLM
    # (post model-swap) anchored on this prompt's own example bill name and
    # repeated it across unrelated articles (World Cup coverage, a AG
    # confirmation hearing) that never mentioned any bill at all. Article
    # text is the only source of truth for what was actually named, same
    # principle as the ID-regex extraction above.
    combined_text_lower = combined_text.lower()
    for b in raw_bills:
        if isinstance(b, dict) and b.get("name"):
            name = b["name"].strip()
            if name and name.lower() in combined_text_lower:
                name_refs.append({"name": name, "id": None})
            elif name:
                logger.warning(
                    "Dropping LLM-extracted bill %r — not found verbatim in "
                    "source articles (likely hallucinated)", name,
                )

    bill_refs = id_refs + name_refs

    if bill_refs:
        logger.info("Bill refs to resolve: %s",
                     ", ".join(f"{r['name']}(id={r.get('id')})" for r in bill_refs))

    if not bill_refs:
        return []

    resolved: list[dict] = []
    seen_ids: set[str] = set()

    for ref in bill_refs:
        bill_id = ref.get("id")

        # If we have a bill ID like "HR.22" or "S.1234", build URL directly
        if bill_id and re.match(r'^(HR|S|HJRES|SJRES)\.\d+$', bill_id):
            if bill_id in seen_ids:
                continue
            seen_ids.add(bill_id)
            parts = bill_id.split(".")
            type_map = {
                "HR": "house-bill", "S": "senate-bill",
                "HJRES": "house-joint-resolution",
                "SJRES": "senate-joint-resolution",
            }
            url_type = type_map.get(parts[0])
            if url_type:
                url = _congress_gov_bill_url(
                    settings.CURRENT_CONGRESS, url_type, parts[1]
                )
                resolved.append({
                    "name": ref["name"], "id": bill_id, "url": url,
                    "congress": settings.CURRENT_CONGRESS,
                })
                continue

        # Otherwise search Congress.gov API by bill name
        if ref["name"] and ref["name"] not in seen_ids:
            seen_ids.add(ref["name"])
            found = _search_congress_bill(ref["name"])
            if found:
                resolved.append(found)

    return resolved[:5]


_BILL_TYPE_MAP = {
    "hr": ("HR", "house-bill"),
    "s": ("S", "senate-bill"),
    "hjres": ("HJRES", "house-joint-resolution"),
    "sjres": ("SJRES", "senate-joint-resolution"),
    "hconres": ("HCONRES", "house-concurrent-resolution"),
    "sconres": ("SCONRES", "senate-concurrent-resolution"),
    "hres": ("HRES", "house-resolution"),
    "sres": ("SRES", "senate-resolution"),
}

_STOP_WORDS = frozenset({
    "act", "of", "the", "for", "a", "an", "to", "and", "in", "on",
})


def _score_bill_match(query_lower: str, query_words: set[str],
                      bill: dict, congress: int) -> float:
    """Score how well a Congress.gov bill record matches a query.

    Checks both directions: query words appearing in the title, and title
    words appearing in the query (handles LLM adding extra words to a
    short official title like "SAVE Act").
    """
    title = (bill.get("title") or "").lower().strip()
    short_title = (bill.get("shortTitle") or "").lower().strip()

    if query_lower == title or query_lower == short_title:
        score = 2.0
    elif query_lower in title or query_lower in short_title:
        score = 1.0
    elif title in query_lower and len(title) > 3:
        score = 1.5
    elif query_words:
        title_words = {w for w in title.split() if w not in _STOP_WORDS}
        if not title_words:
            score = 0.0
        else:
            query_in_title = len(query_words & title_words) / len(query_words)
            title_in_query = len(query_words & title_words) / len(title_words)
            score = max(query_in_title, title_in_query) * 0.8
    else:
        score = 0.0

    if bill.get("congress") == congress:
        score += 0.1
    return score


def _bill_record_to_result(bill: dict, query: str, congress: int) -> dict | None:
    """Convert a Congress.gov bill record to {name, id, url} or None."""
    bill_type = (bill.get("type") or "").lower()
    bill_number = bill.get("number")
    try:
        bill_congress = int(bill.get("congress") or congress)
    except (TypeError, ValueError):
        bill_congress = congress

    if not bill_type or not bill_number:
        return None
    mapped = _BILL_TYPE_MAP.get(bill_type)
    if not mapped:
        return None

    prefix, url_type = mapped
    bill_id = f"{prefix}.{bill_number}"
    url = _congress_gov_bill_url(bill_congress, url_type, bill_number)
    return {
        "name": bill.get("title", query)[:200],
        "id": bill_id,
        "url": url,
        "congress": bill_congress,
    }


def _search_congress_bill(query: str) -> dict | None:
    """Search Congress.gov for a bill by name. Returns {name, id, url} or None.

    Strategy:
    1. Full-text search API (current congress, then any congress).
    2. If no confident match, fall back to browsing the bill-list endpoint
       for HR and S bills in the current congress and matching by title.
       The search API often misses short-titled bills like "SAVE Act" (HR.22).
    """
    api_key = settings.DATA_GOV_API_KEY
    if not api_key:
        return None

    congress = settings.CURRENT_CONGRESS
    query_lower = query.lower().strip()
    query_words = {w for w in query_lower.split() if w not in _STOP_WORDS}

    best: dict | None = None
    best_score = 0.0

    with httpx.Client(timeout=15.0) as client:
        # --- Strategy 1: full-text search API ---
        for search_congress in [congress, None]:
            congress_filter = f"&congress={search_congress}" if search_congress else ""
            search_url = (
                f"{CONGRESS_API_BASE}/bill"
                f"?query={query}&limit=10&sort=updateDate+desc"
                f"{congress_filter}&api_key={api_key}&format=json"
            )
            try:
                resp = client.get(search_url)
                if resp.status_code != 200:
                    continue
                bills = resp.json().get("bills", [])
            except Exception:
                logger.debug("Congress.gov search failed for %r", query, exc_info=True)
                continue

            for b in bills:
                s = _score_bill_match(query_lower, query_words, b, congress)
                if s > best_score:
                    best_score = s
                    best = b

            if best_score >= 1.0:
                break

        # --- Strategy 2: browse bill list by title (catches short-titled bills) ---
        if best_score < 1.0:
            for bill_type in ("hr", "s"):
                list_url = (
                    f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}"
                    f"?limit=50&sort=number+asc"
                    f"&api_key={api_key}&format=json"
                )
                try:
                    resp = client.get(list_url)
                    if resp.status_code != 200:
                        continue
                    bills = resp.json().get("bills", [])
                except Exception:
                    continue

                for b in bills:
                    s = _score_bill_match(query_lower, query_words, b, congress)
                    if s > best_score:
                        best_score = s
                        best = b
                if best_score >= 1.0:
                    break

    if not best or best_score < 0.5:
        logger.info("Bill search %r: no match (best_score=%.2f)", query, best_score)
        return None

    result = _bill_record_to_result(best, query, congress)
    if result:
        logger.info("Bill search %r -> %s (%s) score=%.2f",
                     query, result["id"], result["name"][:60], best_score)
    return result


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
    from app.pipeline.analyze.ollama_client import call_llm, extract_json

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
    return {
        "summary": str(result.get("summary", "")),
        "topAreas": [str(a) for a in result.get("topAreas", [])[:5]],
    }


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
    # For each distinct ISO week in the DB that has ended (Sunday < today), ensure a WeekSummary exists
    entries_this_year = (
        db.query(TimelineEntry)
        .filter(TimelineEntry.date >= f"{current_year}-01-01",
                TimelineEntry.date <= today_str)
        .order_by(TimelineEntry.date)
        .all()
    )

    # Group by the full ISO (year, week) pair, not week number alone:
    # late-December dates can belong to ISO week 1 of the NEXT year (and
    # early January to week 52/53 of the previous one), so keying on week
    # number collided the year-end week with January's row of the same
    # number — the year-end week was then silently never summarized.
    weeks: dict[tuple[int, int], list] = {}
    for e in entries_this_year:
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

        label = f"the week of {monday.strftime('%B %-d')}–{sunday.strftime('%-d, %Y')}"
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
    # For each completed month (not current month) in current year
    months_done: dict[int, list] = {}
    for e in entries_this_year:
        mnum = int(e.date[5:7])
        months_done.setdefault(mnum, []).append(e)

    for mnum, month_entries in months_done.items():
        if mnum >= current_month:
            continue  # current month not complete
        existing = (
            db.query(MonthSummary)
            .filter(MonthSummary.year == current_year, MonthSummary.month == mnum)
            .first()
        )
        if existing:
            continue

        month_name = datetime(current_year, mnum, 1).strftime("%B %Y")
        top_areas: dict[str, int] = {}
        for e in month_entries:
            for area in _json.loads(e.policy_areas or "[]"):
                top_areas[area] = top_areas.get(area, 0) + 1
        computed_areas = [a for a, _ in sorted(top_areas.items(), key=lambda x: -x[1])[:5]]

        llm = _generate_period_summary(
            label=month_name,
            entries=month_entries,
            cache_key={"period": "month", "year": current_year, "month": mnum},
            db=db,
        )
        db.add(MonthSummary(
            year=current_year,
            month=mnum,
            summary=llm["summary"],
            top_policy_areas=_json.dumps(llm["topAreas"] or computed_areas),
            entry_count=len(month_entries),
        ))
        db.commit()
        logger.info("Generated month-in-review for %s %d", current_year, mnum)

    # --- Year summaries ---
    # For each year < current_year that has timeline entries
    past_year_entries = (
        db.query(TimelineEntry)
        .filter(TimelineEntry.date < f"{current_year}-01-01")
        .order_by(TimelineEntry.date)
        .all()
    )
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


def _story_word_target(n_facts: int) -> tuple[int, int]:
    """Word-count band scaled to how much source material actually exists.

    A fixed 350-500 word floor forced the model to pad every issue to the
    same length regardless of how much reporting backed it. When an issue
    had one thin fact, the model filled the gap with invented specifics —
    a story built from a single vague fact about a "China climate deal"
    stated a fabricated "1.5 degrees Celsius" target and "Paris Agreement"
    framing that appeared nowhere in the source (2026-07 audit). Scaling
    the target to fact count removes the incentive to invent: 1 fact gets
    a short paragraph, not a forced 350-word article.
    """
    low = max(120, min(550, 80 + 90 * n_facts))
    high = max(200, min(750, 140 + 130 * n_facts))
    return low, high


def _generate_full_story(issue, db_session: Session | None = None) -> str | None:
    """Generate a factual deep-dive for an action issue, length scaled to
    how many key facts actually support it (see ``_story_word_target``).

    Returns plain text (paragraphs separated by double newlines), or None on failure.
    Stored in action_issues.full_story so it is ready before users click through.
    """
    from app.pipeline.analyze.ollama_client import call_llm

    facts = json.loads(issue.facts or "[]")
    source_names = json.loads(issue.source_names or "[]")
    policy_areas = json.loads(issue.policy_areas or "[]")

    facts_text = "\n".join(f"- {f}" for f in facts) if facts else "(none provided)"
    sources_text = ", ".join(source_names[:10]) if source_names else "(none provided)"
    policy_text = ", ".join(policy_areas) if policy_areas else "(none provided)"

    word_low, word_high = _story_word_target(len(facts))

    user_prompt = f"""Write a concise, factual article on the following civic issue for Civitas, a U.S. civic transparency platform.

STRICT REQUIREMENTS:
- {word_low}-{word_high} words. Stop when the facts run out — do not pad. A short, \
accurate article is far better than a longer one that repeats itself or invents detail.
- Every sentence must add new information not already stated.
- Do NOT repeat or rephrase information you have already written.
- Factual and non-partisan — report what happened, not what to think about it.
- Flowing paragraphs only (no headers, no bullet points).
- Do not speculate beyond what the sources support.
- Do not name, quote, or attribute a statement or role to any person not \
named in the key facts above.
- Do NOT open with a generic hedge like "Recent coverage indicates," "Recent \
reports say/suggest," "Recent developments show," or any similar throat-clearing \
preamble. Start the first sentence with the concrete news itself — who did what.
- Do NOT use hedging attribution phrases ANYWHERE in the piece — "sources say," \
"reports indicate," "coverage shows," "officials suggest," and similar. State \
facts directly as facts, not as something reports/coverage/sources are saying.
- Do NOT evaluate, justify, or defend any action, speech, or policy. Never write \
that something "is warranted" or "is justified," and never present an actor's \
stated rationale for their own action as established fact. Do not speculate \
about the political or legislative purpose or effect of an action (e.g., how a \
speech "helps move legislation"). Report only what was said or done — not \
whether it was right, smart, necessary, or effective.
- Do NOT assert what an actor's strategic motive, purpose, or intention was \
(e.g., "reflects an effort to manage public perception," "in an effort to \
shape the narrative") unless that motive is explicitly stated as a claim or \
quote in the key facts. Report the action; do not explain why you think they \
did it.
- Use the SPECIFIC names, quotes, and numbers given in the key facts — do not \
paraphrase a specific fact into a vaguer, unnamed version. If a key fact names \
a person or gives a figure, the article should use that same name or figure, \
not a vaguer substitute like "a commentator" or "several officials."
- Do NOT write about "the coverage," "the debate," "the discussion," or "the \
reporting" as if it were the subject of the article — write about what people \
and institutions actually did or said, not about how the news covered it.
- Do NOT end with a vague, generic wrap-up sentence that restates the topic \
without adding new information (e.g., "these developments underscore the \
complex interplay between..."). If there is nothing more concrete to add, end \
the article on the last concrete fact instead.

STRUCTURE (3 natural paragraphs):
1. What is happening and why it matters right now
2. Relevant background and specific details from the key facts
3. What government, Congress, or affected people are doing about it (only if known)

If a section has no supporting facts, skip it rather than inventing content.

ISSUE TITLE: {issue.title}
BRIEF SUMMARY: {issue.summary or "(none)"}

KEY FACTS FROM REPORTING:
{facts_text}

POLICY AREAS: {policy_text}
NEWS SOURCES: {sources_text}

Return JSON: {{"story": "full article text with paragraphs separated by \\n\\n"}}"""

    system_prompt = (
        "You are a senior civic journalist writing factual, thorough, accessible "
        "articles for Civitas — a non-partisan platform that aggregates U.S. government "
        "data. Your goal is to give citizens a complete picture of what is happening in "
        "Washington and why it matters to them. Write clearly for a general audience "
        "without being condescending. Report events directly and in your own voice — "
        "never as something 'reports say' or 'coverage indicates.' Never evaluate "
        "whether an action was warranted, justified, or well-reasoned, and never adopt "
        "an actor's stated rationale as fact — describe what was said and done, not "
        "whether it was right or what it accomplishes."
    )
    # Everything the model is shown — the grounding universe for statistics.
    source_material = f"{issue.title}\n{issue.summary or ''}\n{facts_text}"

    retry_note = ""
    for attempt in range(2):
        result = call_llm(
            prompt_version="full_story_v2",
            system_prompt=system_prompt,
            user_prompt=user_prompt + retry_note,
            # Public-facing surface: use the story-tier model when
            # configured (see settings.OLLAMA_STORY_MODEL — two-tier
            # design, 2026-07). The cache hash includes the resolved
            # model, so switching tiers never serves stale generations.
            model=settings.OLLAMA_STORY_MODEL or None,
            # The retry must not be served the same rejected story from cache.
            cache_key=(
                f"full_story:{issue.id}:{issue.title[:80]}" if attempt == 0 else None
            ),
            # call_llm caches only when BOTH cache_key and db_session are
            # set — passing None here silently disabled the first-attempt
            # cache the cache_key above exists for.
            db_session=db_session,
            max_tokens=2048,
            num_ctx=4096,
        )

        if not result or not isinstance(result.get("story"), str):
            logger.warning("Full story generation returned no result for issue %s", issue.id)
            return None

        story = _fix_impossible_senate_vote_counts(result["story"].strip())
        if len(story) < 200:
            logger.warning("Full story too short (%d chars) for issue %s", len(story), issue.id)
            return None

        # Reject fabricated statistics and fabricated named officials: any
        # money/percent/magnitude/year figure, or any titled/role-described
        # person, must appear in the material the model was shown. Plain
        # contextual numbers and untitled bare names are left to the prompt
        # rules (see grounding.py) — this only catches the two highest-
        # precision hallucination signals mechanically. (2026-07: a full
        # story invented "The Senate Republican leader, Chuck Schumer, has
        # said Graham's death has made a hard month harder for the Senate
        # agenda" — no Schumer mention anywhere in the source material, and
        # this generator had no check for fabricated names at all until
        # then, unlike the Bluesky poster which already ran this check.)
        from app.pipeline.analyze.grounding import (
            hedge_and_editorializing_violations,
            repeated_sentences,
            ungrounded_electoral_claims,
            ungrounded_former_official_claims,
            ungrounded_relationship_claims,
            ungrounded_statistics,
            ungrounded_titled_names,
        )
        novel = ungrounded_statistics(story, source_material)
        names = ungrounded_titled_names(story, source_material)
        dupes = repeated_sentences(story)
        # hedge_and_editorializing_violations also covers literal unfilled
        # placeholder tokens ("[date]") since the 2026-07 audit.
        hedge_editorial = hedge_and_editorializing_violations(story)
        # Same fabricated-relationship class as the electoral guard, family
        # edition (2026-07 audit: "her brother" published ungrounded).
        relationships = ungrounded_relationship_claims(story, source_material)
        # Same relational-fabrication guard the Bluesky poster runs: a full
        # story that invents a race/campaign between two officials who both
        # appear in the facts for an unrelated reason (2026-07: a Graham story
        # claiming he "was facing competition from Susan Collins for his senate
        # race") slips past the number and name checks — both surnames are
        # grounded and no figure is fabricated.
        electoral = ungrounded_electoral_claims(story, source_material)
        # Stale-training-data status claims — the model demoting a sitting
        # official to "former" from its outdated world knowledge (2026-07:
        # "former President Donald Trump" published to Bluesky while the
        # source material said "President Trump").
        former = ungrounded_former_official_claims(story, source_material)
        if not novel and not names and not dupes and not hedge_editorial and not electoral and not relationships and not former:
            logger.info(
                "Generated full story for issue %s (%d chars): %s",
                issue.id, len(story), issue.title[:60],
            )
            return story

        problems = []
        if novel:
            problems.append(
                f"figures not present in the key facts ({', '.join(novel)})"
            )
            logger.warning(
                "Full story failed statistic grounding for issue %s (attempt %d): %s",
                issue.id, attempt + 1, ", ".join(novel),
            )
        if names:
            problems.append(
                f"officials not present in the key facts ({', '.join(names)})"
            )
            logger.warning(
                "Full story failed named-official grounding for issue %s (attempt %d): %s",
                issue.id, attempt + 1, ", ".join(names),
            )
        if dupes:
            problems.append(
                "sentences repeated verbatim later in the article "
                f"({'; '.join(s[:80] for s in dupes)})"
            )
            logger.warning(
                "Full story repeated itself for issue %s (attempt %d): %s",
                issue.id, attempt + 1, "; ".join(dupes),
            )
        if hedge_editorial:
            problems.extend(hedge_editorial)
            logger.warning(
                "Full story failed hedge/editorializing check for issue %s (attempt %d): %s",
                issue.id, attempt + 1, "; ".join(hedge_editorial),
            )
        if electoral:
            problems.append(
                f"an election or campaign not present in the key facts ({', '.join(electoral)})"
            )
            logger.warning(
                "Full story invented an electoral contest for issue %s (attempt %d): %s",
                issue.id, attempt + 1, ", ".join(electoral),
            )
        if relationships:
            problems.append(
                f"a family relationship not present in the key facts ({', '.join(relationships)})"
            )
            logger.warning(
                "Full story asserted an ungrounded family relationship for issue %s (attempt %d): %s",
                issue.id, attempt + 1, ", ".join(relationships),
            )
        if former:
            problems.append(
                "'former' office-holder status not present in the key facts "
                f"({', '.join(former)})"
            )
            logger.warning(
                "Full story called an official 'former' without source basis for issue %s (attempt %d): %s",
                issue.id, attempt + 1, ", ".join(former),
            )
        retry_note = (
            "\n\nYour previous attempt was rejected because it contained "
            f"{' and '.join(problems)}. Stop writing once the facts are "
            "covered instead of repeating yourself, use only numbers "
            "that appear in the material above, do not name or quote "
            "anyone who isn't named in the material above, do not describe "
            "any election, race, campaign, or challenge for office unless the "
            "material above says so, report events directly instead of "
            "through phrases like 'reports say,' and do not evaluate whether "
            "any action was warranted or justified."
        )

    return None


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


_MONITOR_ISSUE_SIM = 0.70        # issue vs monitor-description (headline vs long text)
_MONITOR_ISSUE_TITLE_SIM = 0.62
_MONITOR_ISSUE_SIM_HIGH = 0.80   # above this: skip LLM gate, auto-match
_MONITOR_MERGE_SIM = 0.42
# Above this, two monitors are similar enough to merge outright without the
# LLM verification step below _MONITOR_MERGE_SIM uses — was a bare 0.55
# duplicated at both monitor-merge call sites with no name or rationale.
_MONITOR_AUTO_MERGE_SIM = 0.55
# Headline-to-headline floor is ~0.74; use 0.83 to distinguish same-topic from
# any-two-news-headlines so Step 3 doesn't create monitors for unrelated topics.
_MONITOR_HISTORY_SIM = 0.83
_MONITOR_MIN_DAYS = 5
_MONITOR_MIN_UNIQUE_SOURCES = 3
_MONITOR_LOOKBACK_DAYS = 14
_MONITOR_DORMANT_DAYS = 7
_MONITOR_CLOSE_DAYS = 30
_MONITOR_MIN_UPDATES_FOR_ARCHIVE = 3


_MONITOR_METADATA_PROMPT = """\
You are a senior civic data analyst. Below are recent news articles for a \
potential National Monitor. A National Monitor tracks a SIGNIFICANT, \
LONG-TERM national or international issue of high civic importance.

Articles:
{articles}

Analyze these articles and provide a JSON object:
- "title": A concise, broad, and neutral name for this ongoing monitor (e.g., "U.S.-Iran Conflict" or "Federal Housing Reform").
- "description": A factual 2-3 sentence summary of the ongoing situation and its national significance.
- "category": The most appropriate category from this list: {categories}.
- "is_significant": Boolean. True if this is a recurring national issue with long-term implications. False if it is a transient news story, a niche event, or lacks broad civic relevance.

Respond with ONLY the JSON object."""


_MONITOR_MERGE_PROMPT = """\
You are a civic data analyst. Decide if these two National Monitors should be MERGED.
A merge should occur if they are tracking the SAME underlying national or international issue.

Monitor A: "{title_a}"
Description A: "{desc_a}"

Monitor B: "{title_b}"
Description B: "{desc_b}"

Rules:
- Merge if B is a specific event or facet within the broader context of A (e.g., a specific strike within a conflict).
- Merge if they cover the same topic but from different angles (e.g., "Oil Prices" and "Middle East Conflict").
- Do NOT merge if they are distinct issues that simply happen in the same region or share a keyword but address different civic concerns.

Return a JSON object: {{"should_merge": boolean, "reason": "short explanation"}}
"""


def _slugify(text: str) -> str:
    import re
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s-]+', '-', slug)
    return slug[:200]


# LFM2.5-1.2B-Instruct (production model as of 2026-07) frequently outputs a
# positive verdict (matches/should_merge = true) whose own "reason" text plainly
# says the two items are unrelated — confirmed live 2026-07 via direct sandbox
# testing against the real model with real production prompts/data (e.g. reason:
# "distinct from the U.S.-Iran conflict monitor's focus..." paired with
# matches: true). Reordering the JSON schema (reason before verdict) and adding
# few-shot examples were both tried and did NOT fix it — few-shot made it worse
# via verbatim reasoning-text copying from the wrong example. This regex catches
# the model contradicting its own stated reasoning and overrides the verdict to
# False, the same "never trust the LLM's structured output over the evidence it
# itself produced" principle as the bill-name verification guard in
# _resolve_bills above.
_CONTRADICTION_RE = re.compile(
    r'distinct from|different from|not related|unrelated to|no connection'
    r'|does not (?:directly )?(?:involve|relate|connect)|not directly (?:involve|related)'
    r'|no direct (?:involvement|connection|relation)|separate from|not the same'
    r'|superficial overlap|does not share',
    re.IGNORECASE,
)


def _reason_contradicts_positive_verdict(reason: str) -> bool:
    """True if an LLM's own explanation text undercuts the positive verdict it just gave."""
    return bool(reason) and bool(_CONTRADICTION_RE.search(reason))


def _should_merge_monitors_llm(
    a: NationalMonitor,
    b: NationalMonitor,
    db: Session,
) -> bool:
    """Use LLM to decide if two monitors should be merged."""
    from app.pipeline.analyze.ollama_client import call_llm, extract_json

    user_prompt = _MONITOR_MERGE_PROMPT.format(
        title_a=a.title, desc_a=a.description[:300],
        title_b=b.title, desc_b=b.description[:300],
    )

    result = call_llm(
        prompt_version="monitor-merge-v1",
        system_prompt="You are a civic data analyst. Respond in JSON.",
        user_prompt=user_prompt,
        cache_key={"type": "monitor_merge", "ids": sorted([a.id, b.id])},
        db_session=db,
        max_tokens=256,
    )

    if isinstance(result, str):
        result = extract_json(result)

    if isinstance(result, dict) and result.get("should_merge"):
        reason = result.get("reason", "")
        if _reason_contradicts_positive_verdict(reason):
            logger.warning(
                "LLM merge verdict contradicts its own reasoning, overriding to False: "
                "'%s' + '%s' — %s", a.title, b.title, reason,
            )
            return False
        logger.info("LLM approved merge: '%s' + '%s' because: %s",
                    a.title, b.title, reason)
        return True
    return False


def _should_match_monitor_llm(
    issue_title: str,
    issue_summary: str,
    monitor: NationalMonitor,
    db: Session,
) -> bool:
    """LLM gate for borderline embedding matches: does this issue genuinely belong to this monitor?"""
    from app.pipeline.analyze.ollama_client import call_llm

    result = call_llm(
        prompt_version="monitor-match-v1",
        system_prompt="You are a civic data analyst. Respond in JSON.",
        user_prompt=(
            f'Monitor: "{monitor.title}"\n'
            f'Monitor description: "{monitor.description[:300]}"\n\n'
            f'Issue title: "{issue_title}"\n'
            f'Issue summary: "{issue_summary[:300]}"\n\n'
            "Does this issue genuinely belong to this monitor? The monitor and issue must "
            "share the same specific subject (same country, same policy dispute, same named actors). "
            "Superficial overlap (both involve government, both are international) is NOT enough.\n\n"
            'Return JSON: {"matches": true/false, "reason": "one sentence"}'
        ),
        cache_key={"type": "monitor_match", "monitor": monitor.title, "issue": issue_title},
        db_session=db,
        max_tokens=128,
    )
    if not isinstance(result, dict):
        return False
    matched = bool(result.get("matches", False))
    reason = result.get("reason", "")
    if matched and _reason_contradicts_positive_verdict(reason):
        logger.warning(
            "LLM match verdict contradicts its own reasoning, overriding to False: "
            "'%s' → '%s' — %s", issue_title[:50], monitor.title, reason,
        )
        return False
    logger.debug(
        "LLM monitor match '%s' → '%s': %s — %s",
        issue_title[:50], monitor.title, matched, reason,
    )
    return matched


def _reclassify_monitor_llm(
    monitor: NationalMonitor,
    db: Session,
) -> None:
    """Use LLM to re-evaluate and potentially re-categorize an existing monitor."""
    from app.pipeline.analyze.ollama_client import call_llm, extract_json
    from app.config_definitions import POLICY_AREAS

    # Only re-classify if it's currently a generic or suspicious category
    # or if we just want a periodic sanity check.
    
    updates = monitor.updates[:5]
    articles = "\n".join([f"- {u.article_title}: {u.summary[:150]}" for u in updates])
    
    user_prompt = f"""\
Identify the best policy category for this National Monitor.
Title: {monitor.title}
Description: {monitor.description[:300]}
Recent Updates:
{articles}

Categories: {", ".join(POLICY_AREAS)}

Return JSON: {{"category": "CATEGORY_NAME", "reason": "why"}}
"""

    result = call_llm(
        prompt_version="monitor-reclassify-v1",
        system_prompt="You are a civic data analyst. Respond in JSON.",
        user_prompt=user_prompt,
        cache_key={"type": "monitor_reclassify", "id": monitor.id, "title": monitor.title},
        db_session=db,
        max_tokens=256,
    )

    if isinstance(result, str):
        result = extract_json(result)
    
    if isinstance(result, dict) and result.get("category"):
        new_cat = str(result["category"]).upper().replace(" ", "_")
        if new_cat in POLICY_AREAS:
            old_cat = monitor.category
            monitor.category = new_cat.lower()
            monitor.policy_areas = json.dumps([new_cat])
            if old_cat != monitor.category:
                logger.info("Re-categorized monitor '%s': %s -> %s (%s)",
                            monitor.title, old_cat, monitor.category, result.get("reason"))


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
    db.delete(absorb)


def _merge_similar_monitors(monitors: list[NationalMonitor], model, db: Session) -> bool:
    """Pairwise-compare a monitor list and merge any that are similar
    enough. Above _MONITOR_AUTO_MERGE_SIM, monitors merge outright;
    between that and _MONITOR_MERGE_SIM, an LLM call verifies first.
    Returns True if anything merged, so the caller knows to db.flush().

    _update_national_monitors calls this twice — once for monitors that
    existed before today's new ones are created, once again afterward to
    catch newly-created near-duplicates — previously as two copy-pasted
    loops.
    """
    if len(monitors) < 2:
        return False

    mon_embs = model.encode(
        [f"{m.title} {m.description}" for m in monitors],
        normalize_embeddings=True,
    )
    mon_title_embs = model.encode(
        [m.title for m in monitors],
        normalize_embeddings=True,
    )
    merged_ids: set[int] = set()
    for a_idx in range(len(monitors)):
        if monitors[a_idx].id in merged_ids:
            continue
        for b_idx in range(a_idx + 1, len(monitors)):
            if monitors[b_idx].id in merged_ids:
                continue
            full_sim = float((mon_embs[a_idx] @ mon_embs[b_idx].T).item())
            title_sim = float((mon_title_embs[a_idx] @ mon_title_embs[b_idx].T).item())

            should_merge = False
            if full_sim >= _MONITOR_AUTO_MERGE_SIM or title_sim >= _MONITOR_AUTO_MERGE_SIM:
                should_merge = True
            elif full_sim >= _MONITOR_MERGE_SIM or title_sim >= _MONITOR_MERGE_SIM:
                should_merge = _should_merge_monitors_llm(monitors[a_idx], monitors[b_idx], db)

            if should_merge:
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


def _generate_monitor_metadata(
    issue: ActionIssue,
    past_issues: list[ActionIssue],
    db: Session,
) -> dict | None:
    """Use LLM to generate professional metadata for a new National Monitor."""
    from app.pipeline.analyze.ollama_client import call_llm, extract_json
    from app.config_definitions import POLICY_AREAS

    all_issues = [issue] + past_issues
    # Gather unique articles to provide enough context for the LLM
    seen_titles: set[str] = set()
    articles: list[str] = []
    for i in all_issues[:15]:
        if i.title not in seen_titles:
            seen_titles.add(i.title)
            sources = json.loads(i.source_names or "[]")
            source_str = f" [{sources[0]}]" if sources else ""
            articles.append(f"{i.title}{source_str}\n  {i.summary[:200]}")

    if not articles:
        return None

    user_prompt = _MONITOR_METADATA_PROMPT.format(
        articles="\n\n".join(articles),
        categories=", ".join(POLICY_AREAS),
    )

    result = call_llm(
        prompt_version="monitor-metadata-v1",
        system_prompt="You are a civic data analyst. Respond in JSON.",
        user_prompt=user_prompt,
        cache_key={"type": "monitor_metadata", "titles": sorted(list(seen_titles))[:5]},
        db_session=db,
        max_tokens=1024,
    )

    if isinstance(result, str):
        result = extract_json(result)
    
    if not isinstance(result, dict) or not result.get("is_significant"):
        logger.info("Monitor metadata rejected or not significant for '%s'", issue.title[:50])
        return None
    
    # Validation
    category = str(result.get("category", "FOREIGN_POLICY")).upper().replace(" ", "_")
    if category not in POLICY_AREAS:
        category = "FOREIGN_POLICY"
    
    return {
        "title": str(result.get("title", issue.title))[:500],
        "description": str(result.get("description", issue.summary))[:1000],
        "category": category.lower(),
    }


def _update_national_monitors(today: str, db: Session) -> None:
    """Detect recurring topics and create/update national monitors.

    Uses embedding similarity to match today's issues to existing monitors
    and to detect new recurring topics from past days' issues.
    Every monitor update traces to a specific source article — no LLM-generated
    facts, only condensed summaries of sourced articles.
    """
    try:
        from app.pipeline.vector_store import get_embedding_model
        model = get_embedding_model()
    except Exception:
        logger.warning("Could not load embedding model for monitors")
        return

    today_issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.date == today)
        .order_by(ActionIssue.rank)
        .all()
    )
    if not today_issues:
        return

    existing_monitors = db.query(NationalMonitor).all()

    today_embeddings = model.encode(
        [i.title for i in today_issues],
        normalize_embeddings=True,
    )

    # Step 1: Merge any existing monitors that are too similar to each other.
    _set_refresh_state(stage_detail="1/4 dedup")
    if _merge_similar_monitors(existing_monitors, model, db):
        db.flush()
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
        monitor_title_embeddings = model.encode(
            [m.title for m in existing_monitors],
            normalize_embeddings=True,
        )
        sims = today_embeddings @ monitor_embeddings.T
        title_sims = today_embeddings @ monitor_title_embeddings.T

        for i, issue in enumerate(today_issues):
            for j, monitor in enumerate(existing_monitors):
                full_sim = float(sims[i][j])
                title_sim = float(title_sims[i][j])
                if full_sim < _MONITOR_ISSUE_SIM:
                    continue
                if title_sim < _MONITOR_ISSUE_TITLE_SIM:
                    continue
                # LLM gate for borderline matches: require high confidence or LLM approval
                if full_sim < _MONITOR_ISSUE_SIM_HIGH:
                    if not _should_match_monitor_llm(
                        issue.title, issue.summary or "", monitor, db
                    ):
                        continue

                issue_monitor_slugs.setdefault(i, []).append(monitor.slug)

                source_urls = json.loads(issue.source_urls or "[]")
                source_names = json.loads(issue.source_names or "[]")
                if not source_urls:
                    matched_issues.add(i)
                    continue

                # Match against ANY of today's source URLs, not just the
                # first: source ordering shifts between hourly runs as new
                # articles arrive, so keying the duplicate check on
                # source_urls[0] alone let the same story accrue a second
                # same-day update whenever its leading source changed.
                already_exists = (
                    db.query(MonitorUpdate)
                    .filter(
                        MonitorUpdate.monitor_id == monitor.id,
                        MonitorUpdate.date == today,
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
        .filter(ActionIssue.date >= cutoff_date, ActionIssue.date < today)
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

            # --- LLM Metadata Generation ---
            metadata = _generate_monitor_metadata(issue, matched_past, db)
            if not metadata:
                continue

            slug = _slugify(metadata["title"])
            
            # Ensure unique slug
            existing_slug = db.query(NationalMonitor).filter(NationalMonitor.slug == slug).first()
            if existing_slug:
                slug = f"{slug}-{int(time.time()) % 1000}"

            monitor = NationalMonitor(
                slug=slug,
                title=metadata["title"],
                description=metadata["description"],
                category=metadata["category"],
                status=MonitorStatus.ACTIVE,
                policy_areas=json.dumps([metadata["category"].upper()]),
                last_article_date=today,
            )
            db.add(monitor)
            db.flush()

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

            logger.info("New monitor created (LLM-vetted): '%s' (%d days)",
                        monitor.title, len(matched_dates))

    # Step 3b: Re-merge after creating new monitors.
    all_monitors = db.query(NationalMonitor).all()
    if _merge_similar_monitors(all_monitors, model, db):
        db.flush()

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
        last_date = m.last_article_date or m.created_at.strftime("%Y-%m-%d")
        update_count = len(m.updates or [])

        # 1. Close monitors inactive for >30 days
        if last_date < close_cutoff:
            # If it never gained enough updates to be considered historically 
            # significant, just delete it to keep the database clean.
            if update_count < _MONITOR_MIN_UPDATES_FOR_ARCHIVE:
                logger.info("Deleting insignificant monitor: '%s' (%d updates)", 
                            m.title, update_count)
                db.delete(m)
            else:
                m.status = MonitorStatus.CLOSED
                logger.info("Closing monitor due to inactivity: '%s'", m.title)

        # 2. Mark active monitors as "watching" if inactive for >7 days
        elif m.status == MonitorStatus.ACTIVE and last_date < dormant_cutoff:
            m.status = MonitorStatus.WATCHING
            logger.info("Monitor set to watching: '%s'", m.title)

        # 3. Periodically re-categorize active monitors to keep taxonomy accurate
        elif m.status == MonitorStatus.ACTIVE:
            # Probability-based or simple toggle to avoid too many LLM calls
            # For now, let's just do it if it's currently 'general' or 'defense' (the most common mis-labels)
            if m.category in ("general", "defense", "guns", "trade"):
                _reclassify_monitor_llm(m, db)

    db.commit()


_US_REFS_RE = re.compile(
    r'\bU\.S\.?\b|\bUnited States\b|\bAmerican?\b', re.IGNORECASE,
)


def _validate_geographic_consistency(title: str, source_titles: list[str]) -> str:
    """Remove hallucinated U.S. references from the generated title.

    Catches the pattern where the LLM inserts 'U.S.' into a title about
    a story where the US is not a direct actor — e.g. China-Japan export
    controls becoming 'U.S. and Japan: Tensions Rise...'. Checks the title
    against actual source article titles; if no source mentions the US,
    the reference is stripped.
    """
    if not _US_REFS_RE.search(title):
        return title
    combined_sources = " ".join(source_titles)
    if _US_REFS_RE.search(combined_sources):
        return title
    logger.warning(
        "Removing hallucinated U.S. reference from title (absent from source articles): '%s'",
        title[:80],
    )
    # Strip "U.S. and X: ..." or "U.S.: ..." lead patterns
    fixed = re.sub(
        r'^(U\.S\.?|United States|American?)\s+(and\s+[A-Z][^:]*:\s*|:\s*)',
        '', title, flags=re.IGNORECASE,
    )
    # Strip "U.S. and " mid-title
    fixed = re.sub(r'\b(U\.S\.?|United States|American?)\s+and\s+', '', fixed, flags=re.IGNORECASE)
    fixed = fixed.strip().strip(':').strip()
    return fixed if len(fixed) >= 10 else title


# The Senate has 100 members, so any reported vote tally whose yeas+nays
# exceeds 100 is physically impossible for the Senate — it can only be a
# House roll call (435 members). Confirmed live 2026-07: a generated fact
# read "The bill passed the Senate with a vote of 226-195" for a story
# where the bill passed the House 226-195 and was later taken up in the
# Senate — the model correctly extracted a real number from the source
# article but attached the wrong chamber label from elsewhere in the
# same article. Unlike most hallucination guards in this file, this one
# doesn't just drop the offending content: because only two chambers
# exist and only the Senate has this hard 100-member ceiling, "count
# exceeds 100 and is currently labeled Senate" has exactly one possible
# correction, deterministically.
SENATE_MAX_MEMBERS = 100
_VOTE_TALLY_RE = re.compile(r'\b(\d{1,3})\s*(?:-|to|–|—)\s*(\d{1,3})\b')
_SENATE_WORD_RE = re.compile(r'\bSenate\b')
_HOUSE_WORD_RE = re.compile(r'\bHouse\b')
_CHAMBER_LOOKBACK_CHARS = 80


def _fix_impossible_senate_vote_counts(text: str) -> str:
    """Correct 'Senate' to 'House' when the nearest vote tally before it
    exceeds the Senate's 100-member ceiling. See module-level comment
    above _VOTE_TALLY_RE for the real case this was found from."""
    if not text:
        return text

    replacements: list[tuple[int, int]] = []  # (start, end) spans to become "House"
    for m in _VOTE_TALLY_RE.finditer(text):
        total = int(m.group(1)) + int(m.group(2))
        if total <= SENATE_MAX_MEMBERS:
            continue  # plausible for either chamber — not this function's problem

        window_start = max(0, m.start() - _CHAMBER_LOOKBACK_CHARS)
        window = text[window_start:m.start()]
        senate_hits = list(_SENATE_WORD_RE.finditer(window))
        house_hits = list(_HOUSE_WORD_RE.finditer(window))
        if not senate_hits or house_hits:
            # No nearby "Senate" to fix, or "House" already mentioned
            # closer/at all in the window — ambiguous, leave untouched
            # rather than guess.
            continue
        nearest = senate_hits[-1]
        replacements.append((window_start + nearest.start(), window_start + nearest.end()))

    if not replacements:
        return text

    logger.warning(
        "Correcting %d impossible Senate vote-count mention(s) (>100 total) to House: %s",
        len(replacements), text[:120],
    )
    fixed = text
    for start, end in sorted(replacements, reverse=True):
        fixed = fixed[:start] + "House" + fixed[end:]
    return fixed


_ROLE_CHECK_SYSTEM = (
    "You are a rigorous fact-checker. You check ONE thing: whether a summary "
    "correctly attributes actions and outcomes to the right people — who did "
    "what to whom, who sued whom, who was found guilty or liable versus who "
    "brought the case or made the accusation. You are not checking style, "
    "completeness, or opinion — only whether any party's role has been "
    "reversed or confused with another party's."
)

_ROLE_CHECK_TEMPLATE = """\
Source articles:
{articles}

Generated summary:
{summary}

Does the summary correctly attribute every action, accusation, and legal \
outcome to the right person — with nobody's role reversed (e.g. describing \
an accuser/plaintiff/victim as the one found guilty or liable, or crediting \
one party's action or outcome to the other party)?

Respond with ONLY a JSON object:
{{"accurate": true}}
or
{{"accurate": false, "reason": "<one sentence naming what was reversed>"}}"""


def _check_summary_roles(summary: str, articles_text: str, db: Session) -> tuple[bool, str]:
    """Second-pass check for subject/object role reversal in a generated
    summary (confirmed live, 2026-07: issue #376 stated E. Jean Carroll "was
    found guilty of sexual assault and defamation" when Trump was the party
    a jury found liable — Carroll was the plaintiff).

    Mechanical grounding checks (grounding.py) can't catch this class of
    error: both parties' names genuinely appear in the source near the
    relevant legal language, so keyword/proximity matching can't tell which
    direction is correct — that needs actual reading comprehension. This
    runs a second, independently-prompted LLM pass focused only on role
    correctness, since the same generation pass that made the error is
    unlikely to catch its own mistake.

    Fails open (returns accurate=True) on any error or unparseable response
    — a broken verification call should not block issue creation, only a
    confirmed reversal should trigger a retry.
    """
    from app.pipeline.analyze.ollama_client import call_llm, extract_json

    try:
        result = call_llm(
            prompt_version=ACTION_CENTER_PROMPT_VERSION + "-role-check",
            system_prompt=_ROLE_CHECK_SYSTEM,
            user_prompt=_ROLE_CHECK_TEMPLATE.format(articles=articles_text[:3000], summary=summary),
            cache_key=None,  # always re-check a freshly (re)generated summary
            db_session=db,
            max_tokens=150,
            num_ctx=4096,
        )
    except Exception:
        logger.exception("Summary role-check LLM call failed")
        return True, ""

    if isinstance(result, str):
        result = extract_json(result)
    if not isinstance(result, dict):
        return True, ""
    if result.get("accurate", True):
        return True, ""
    return False, str(result.get("reason", "role reversal suspected"))[:200]


# How long a held refresh lock is honored before being treated as
# abandoned (a crashed container never deletes its lock row). Generous
# vs. the observed refresh duration; matches the pre-existing 4-hour
# stale-override convention for the in-process guard.
_REFRESH_LOCK_STALE_S = 4 * 3600


def _acquire_refresh_lock(db: Session) -> bool:
    """Cross-container refresh lock (2026-07, platform-review O15): the
    hourly refresh previously had only a process-local guard, so during a
    blue/green deploy overlap two containers could both run it —
    duplicate Bluesky posts and contended SQLite writes. The lock is a
    plain INSERT into api_cache, whose (tier, cache_key) PRIMARY KEY
    makes the second acquirer's insert fail atomically — no
    check-then-insert race, no schema changes. A crashed holder's row is
    taken over once it exceeds _REFRESH_LOCK_STALE_S.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import ApiCache

    now = utcnow()
    stale_cutoff = now - timedelta(seconds=_REFRESH_LOCK_STALE_S)
    db.query(ApiCache).filter(
        ApiCache.tier == "action-refresh-lock",
        ApiCache.cache_key == "lock",
        ApiCache.cached_at < stale_cutoff,
    ).delete()
    db.commit()

    try:
        db.add(ApiCache(
            tier="action-refresh-lock", cache_key="lock",
            data_json="{}", cached_at=now,
        ))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _release_refresh_lock(db: Session) -> None:
    from app.models import ApiCache

    try:
        db.query(ApiCache).filter(
            ApiCache.tier == "action-refresh-lock",
            ApiCache.cache_key == "lock",
        ).delete()
        db.commit()
    except Exception:
        logger.exception("Failed to release action refresh lock (will expire as stale)")
        db.rollback()


def refresh_action_issues(db: Session | None = None) -> int:
    """Run the full action center pipeline. Returns number of issues created."""
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        if not _acquire_refresh_lock(db):
            logger.info("Action refresh lock held by another container — skipping this run")
            return 0
        try:
            return _run_refresh(db)
        finally:
            _release_refresh_lock(db)
    finally:
        if own_session:
            db.close()


def _find_matching_issue(
    title: str,
    facts: list,
    recent_issues: list,
    recent_embs: "np.ndarray | None",
    title_emb: "np.ndarray",
    matched_issue_ids: set,
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
    """
    if recent_embs is None:
        return None
    new_sig = _issue_signature(title, facts)
    sims = recent_embs @ title_emb
    for cand_idx in np.argsort(-sims):
        sim = float(sims[cand_idx])
        if sim < _TOPIC_MATCH_CANDIDATE_FLOOR:
            break
        candidate = recent_issues[int(cand_idx)]
        if candidate.id in matched_issue_ids:
            continue
        try:
            cand_facts = json.loads(candidate.facts or "[]")
        except (ValueError, TypeError):
            cand_facts = []
        if _is_exact_content_duplicate(title, facts, candidate.title, cand_facts):
            return candidate
        cand_sig = _issue_signature(candidate.title or "", cand_facts)
        if _signatures_match(new_sig, cand_sig):
            return candidate
        if (not new_sig or not cand_sig) and sim >= TOPIC_CHANGE_THRESHOLD:
            return candidate
    return None


def _run_refresh(db: Session) -> int:
    t0 = time.perf_counter()
    today = datetime.now(_US_EAST).strftime("%Y-%m-%d")

    logger.info("Action center refresh starting for %s", today)
    from app.pipeline.analyze import action_metrics
    action_metrics.reset()
    _set_refresh_state(
        is_running=True, stage="fetch", stage_detail=None,
        started_at=utcnow(),
    )

    # 1. Fetch articles
    articles = fetch_news_articles()
    if not articles:
        logger.warning("No articles fetched — skipping action center refresh")
        _set_refresh_state(is_running=False, stage=None)
        return 0

    # 2. Filter for policy relevance
    _set_refresh_state(stage="filter")
    relevant = _filter_policy_relevant(articles)
    if not relevant:
        logger.warning("No policy-relevant articles found")
        _set_refresh_state(is_running=False, stage=None)
        return 0

    # 3. Fetch trending topics from social media
    trending = fetch_trending_topics()

    # 4. Cluster by topic
    _set_refresh_state(stage="cluster")
    clusters = _cluster_articles(relevant)

    # 5. Rank clusters using coverage breadth + trending relevance
    _set_refresh_state(stage="rank")
    ranked_clusters = _rank_clusters(clusters, trending, db)

    # 5b. Deduplicate top clusters so two angles on the same story
    # don't both appear (e.g., "Tariff hikes" and "Market fallout from tariffs")
    top_clusters = _deduplicate_top_clusters(ranked_clusters, MAX_ISSUES)
    _set_refresh_state(stage="issues", stage_detail=f"0/{len(top_clusters)}")

    # 6. Generate analysis for each via LLM
    from app.pipeline.analyze.ollama_client import call_llm, extract_json

    # Pre-load recent issues for topic-keyed matching.
    # Issues are keyed by TOPIC, not by (date, rank) slot — the same topic
    # always maps to the same DB row regardless of rank or whether it briefly
    # fell out of the top N and came back.
    _lookback = (datetime.now(_US_EAST) - timedelta(days=2)).strftime("%Y-%m-%d")
    _recent_issues: list[ActionIssue] = (
        db.query(ActionIssue)
        .filter(ActionIssue.date >= _lookback)
        .all()
    )
    _recent_embs: "np.ndarray | None" = (
        np.array(_embed_texts_sim([i.title for i in _recent_issues]))
        if _recent_issues else None
    )
    _matched_issue_ids: set[int] = set()  # existing IDs touched this run
    _new_issues: list[ActionIssue] = []   # newly inserted rows (no ID yet)

    issues_created = 0
    # (title, embedding) pairs for post-LLM dedup within a single run
    generated_title_embs: list[tuple[str, "np.ndarray"]] = []

    for rank, cluster in enumerate(top_clusters, start=1):
        _set_refresh_state(stage_detail=f"{rank}/{len(top_clusters)}")
        # Filter the cluster to articles that are genuinely on-topic using
        # centered embeddings — the same space the clustering used. Raw cosine
        # similarity is useless here because every news headline sits in the
        # same high-similarity region; centering removes that bias so only
        # articles that share the cluster's specific topic score highly.
        cluster_titles = [a.title for a in cluster]
        raw_embs = _embed_texts(cluster_titles)
        mean_emb = raw_embs.mean(axis=0)
        centered = raw_embs - mean_emb
        norms = np.linalg.norm(centered, axis=1, keepdims=True)
        centered_normed = centered / np.where(norms < 1e-9, 1.0, norms)

        # Split off a second genuine topic before the centroid-distance filter
        # below, which can't detect a roughly-balanced two-topic cluster (see
        # _largest_coherent_subgroup). Re-clustering drops article count, so
        # everything after this point only ever sees the retained subgroup.
        keep_idx = _largest_coherent_subgroup(centered_normed @ centered_normed.T, CLUSTER_TITLE_THRESHOLD)
        if len(keep_idx) < len(cluster):
            logger.warning(
                "Rank %d cluster split — dropping %d article(s) covering a "
                "second, unrelated topic: %s",
                rank, len(cluster) - len(keep_idx),
                [cluster[i].title[:60] for i in range(len(cluster)) if i not in keep_idx],
            )
            cluster = [cluster[i] for i in keep_idx]
            centered_normed = centered_normed[keep_idx]

        c_centroid = centered_normed.mean(axis=0)
        c_norm = float(np.linalg.norm(c_centroid))
        c_centroid = c_centroid / c_norm if c_norm > 0 else c_centroid
        centered_sims = centered_normed @ c_centroid

        # Keep only articles that score above 0.25 similarity to the centroid
        # in centered space. 0.0 (above-average) is too loose when the cluster
        # contains articles about different sub-topics that share one broad
        # dimension (e.g. "Trump administration") — they all score positive.
        # 0.25 requires a meaningful alignment with the cluster's specific topic.
        SOURCE_SIM_FLOOR = 0.25
        on_topic = [(a, float(s)) for a, s in zip(cluster, centered_sims) if float(s) >= SOURCE_SIM_FLOOR]
        if not on_topic:
            on_topic = [(cluster[0], 1.0)]

        filtered_cluster = [a for a, _ in on_topic]
        logger.info(
            "Rank %d coherence filter: %d/%d articles on-topic (sims: %s)",
            rank, len(filtered_cluster), len(cluster),
            ", ".join("%.2f" % s for _, s in sorted([(a, float(s)) for a, s in zip(cluster, centered_sims)], key=lambda x: -x[1])[:6]),
        )

        # Build the LLM prompt from only the on-topic articles so the generated
        # title, summary, and facts reflect the cluster's actual topic.
        user_prompt = _build_llm_prompt(filtered_cluster)

        seen_sources: dict[str, str] = {}
        for a, _ in on_topic:
            if a.source_name not in seen_sources:
                seen_sources[a.source_name] = _resolve_url(a.url)
        source_names = list(seen_sources.keys())
        source_urls = list(seen_sources.values())

        # Cache key uses the FILTERED titles so that when coherence filtering
        # changes which articles the LLM sees, the cache is invalidated and
        # a fresh generation reflects the cleaner cluster.
        llm_result = call_llm(
            prompt_version=ACTION_CENTER_PROMPT_VERSION,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            cache_key={"date": today, "rank": rank, "titles": [a.title for a in filtered_cluster[:5]]},
            db_session=db,
            max_tokens=1024,
            num_ctx=4096,
        )

        if not llm_result:
            logger.warning("LLM returned empty for cluster rank %d", rank)
            continue

        if isinstance(llm_result, str):
            llm_result = extract_json(llm_result)
        if not isinstance(llm_result, dict):
            logger.warning("LLM result not a dict for rank %d: %s", rank, type(llm_result))
            continue

        issue_source_text = " ".join(
            f"{a.title} {a.summary}" for a in filtered_cluster
        )

        title = llm_result.get("title", cluster[0].title)
        summary = _fix_impossible_senate_vote_counts(llm_result.get("summary", ""))
        facts = _validate_facts(
            llm_result.get("facts", []),
            source_text=issue_source_text,
        )
        facts = [_fix_impossible_senate_vote_counts(f) for f in facts]

        title = _validate_geographic_consistency(title, [a.title for a in filtered_cluster])

        # A title that demotes a sitting official to "former" without any
        # source basis (2026-07: "former President Donald Trump" — the
        # model's stale training data, not the articles) is replaced with
        # the top article's real headline: the retry below regenerates only
        # summary/facts, so a bad title has a deterministic fallback instead
        # of a retry, same as the geographic-consistency correction above.
        from app.pipeline.analyze.grounding import (
            hedge_and_editorializing_violations,
            ungrounded_former_official_claims,
        )
        title_former = ungrounded_former_official_claims(title, issue_source_text)
        if title_former:
            logger.warning(
                "Title asserted ungrounded 'former' status (%s) — falling back to "
                "article headline: '%s'",
                ", ".join(title_former), title[:80],
            )
            action_metrics.increment("titles_replaced_former_status")
            title = filtered_cluster[0].title

        # Politician role validator: strip hallucinated "Senator X" / "Rep. X" labels
        # that don't match anyone in the database.
        title, summary, facts = _validate_politician_roles(title, summary, facts, db)

        # Mechanical check for hedging attribution ("sources show," "reports
        # indicate") and editorializing ("was warranted") — same backstop as
        # the Bluesky poster and _generate_full_story. The prompt already
        # forbids both (see _SYSTEM_PROMPT / _ISSUE_PROMPT_TEMPLATE) but the
        # local model doesn't reliably follow prompt-only instructions, and
        # unlike the full-story path this summary/facts generation had no
        # mechanical backstop at all until this fix. Ungrounded "former
        # <office>" status claims ride the same retry (2026-07 live case).
        combined_text = summary + " " + " ".join(facts)
        reasons = hedge_and_editorializing_violations(combined_text)
        summary_former = ungrounded_former_official_claims(combined_text, issue_source_text)
        if summary_former:
            reasons.append(
                "'former' office-holder status the articles never state "
                f"({', '.join(summary_former)})"
            )
        if reasons:
            logger.warning(
                "Issue text failed grounding for rank %d: %s — retrying",
                rank, "; ".join(reasons),
            )
            retry_result = call_llm(
                prompt_version=ACTION_CENTER_PROMPT_VERSION,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt + (
                    f"\n\nYour previous response was rejected because it contained "
                    f"{'; '.join(reasons)}. Report events directly instead of "
                    "through phrases like 'reports say' or 'coverage indicates,' "
                    "do not call any official 'former' unless the articles do, "
                    "and do not evaluate whether any action was warranted or "
                    "justified."
                ),
                cache_key=None,
                db_session=db,
                max_tokens=1024,
                num_ctx=4096,
            )
            if isinstance(retry_result, str):
                retry_result = extract_json(retry_result)
            resolved = False
            if isinstance(retry_result, dict):
                retry_summary = _fix_impossible_senate_vote_counts(retry_result.get("summary", ""))
                retry_facts = _validate_facts(
                    retry_result.get("facts", []),
                    source_text=issue_source_text,
                )
                retry_facts = [_fix_impossible_senate_vote_counts(f) for f in retry_facts]
                retry_combined = retry_summary + " " + " ".join(retry_facts)
                if (
                    retry_summary
                    and not hedge_and_editorializing_violations(retry_combined)
                    and not ungrounded_former_official_claims(retry_combined, issue_source_text)
                ):
                    title, summary, facts = _validate_politician_roles(title, retry_summary, retry_facts, db)
                    resolved = True
            if not resolved:
                logger.error(
                    "Issue text still had hedging/editorializing language for "
                    "rank %d after retry — skipping: %s",
                    rank, "; ".join(reasons),
                )
                action_metrics.increment("issues_skipped_grounding")
                continue

        # Second-pass check for who-did-what-to-whom role reversal (see
        # _check_summary_roles). One retry with a corrective note; if the
        # retry still fails, skip this cluster entirely for today rather
        # than publish a summary we have specific reason to believe
        # misattributes an action or legal outcome to the wrong person —
        # same fail-closed posture as _generate_full_story's grounding retry.
        source_text_for_check = " ".join(f"{a.title} {a.summary}" for a in filtered_cluster)
        accurate, reason = _check_summary_roles(summary, source_text_for_check, db)
        if not accurate:
            logger.warning(
                "Summary role-check failed for rank %d ('%s'): retrying with correction",
                rank, reason,
            )
            retry_result = call_llm(
                prompt_version=ACTION_CENTER_PROMPT_VERSION,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt + (
                    f"\n\nYour previous summary had a factual error: {reason}. "
                    "Rewrite the summary making sure every action, accusation, "
                    "and legal outcome is attributed to the correct person."
                ),
                cache_key=None,
                db_session=db,
                max_tokens=1024,
                num_ctx=4096,
            )
            if isinstance(retry_result, str):
                retry_result = extract_json(retry_result)
            retry_summary = retry_result.get("summary") if isinstance(retry_result, dict) else None
            if retry_summary:
                accurate, reason = _check_summary_roles(str(retry_summary), source_text_for_check, db)
                if accurate:
                    summary = str(retry_summary)

            if not accurate:
                logger.error(
                    "Summary role-check failed twice for rank %d ('%s') — "
                    "skipping this issue rather than publish it: %s",
                    rank, reason, summary[:200],
                )
                action_metrics.increment("issues_skipped_role_check")
                continue

        # Minimum-substance gate (2026-07 audit): fewer than 2 facts
        # surviving validation means there isn't a publishable issue here —
        # observed live as issues consisting entirely of vacuous filler
        # ("Congressional scheduling adjustments": no name, no number, no
        # bill anywhere in its facts). Fail closed, same posture as the
        # grounding retries above.
        if len(facts) < 2:
            logger.info(
                "Skipping rank %d — only %d fact(s) survived validation: '%s'",
                rank, len(facts), title[:60],
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

        # 7. Resolve bill references to Congress.gov URLs
        raw_bills = llm_result.get("bills", [])
        if not isinstance(raw_bills, list):
            raw_bills = []
        article_texts = [f"{a.title} {a.summary}" for a in cluster]
        resolved_bills = _resolve_bills(raw_bills, article_texts)
        if resolved_bills:
            logger.info("  Resolved %d bill(s): %s",
                        len(resolved_bills),
                        ", ".join(b["id"] for b in resolved_bills))

        # 8. Find related explore documents
        related_docs = _find_related_explore_docs(title, summary, policy_areas, db)
        related_explore_ids = [d["id"] for d in related_docs]

        # 9. Find senators/reps mentioned in this issue (backward compat field)
        related_senators = _find_related_senators(title, summary, facts, db)

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

        # 10. Build data-driven actions (no LLM hallucinations)
        actions = _build_actions_from_data(
            title, resolved_bills, source_urls, source_names, related_senators,
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

        # Find the matching existing issue across all recent issues (2-day
        # lookback, any rank). Same STORY → same row, regardless of rank
        # yo-yoing or a brief displacement — see _find_matching_issue's
        # docstring for the matching rules and their history.
        match = _find_matching_issue(
            title, facts, _recent_issues, _recent_embs, title_emb, _matched_issue_ids,
        )

        _update_attrs = (
            "title", "summary", "facts", "actions", "source_urls",
            "source_names", "policy_areas", "related_bill_ids",
            "related_explore_ids", "related_senators", "related_officials",
            "primary_article_date",
        )

        _new_values: dict = {
            "title": title[:500],
            "summary": summary,
            "facts": json.dumps(facts),
            "actions": json.dumps(actions),
            "source_urls": json.dumps(source_urls),
            "source_names": json.dumps(source_names),
            "policy_areas": json.dumps(policy_areas),
            "related_bill_ids": json.dumps(resolved_bills),
            "related_explore_ids": json.dumps(related_explore_ids),
            "related_senators": json.dumps(related_senators),
            "related_officials": json.dumps(related_officials),
            "primary_article_date": primary_article_date,
        }

        if match:
            _matched_issue_ids.add(match.id)
            has_new_articles = primary_article_date > (match.primary_article_date or "1970-01-01")
            invalidate_story = _full_story_should_invalidate(
                match.title, match.facts, _new_values["title"], _new_values["facts"],
            )

            match.rank = rank
            match.date = today
            match.is_current = True
            for attr in _update_attrs:
                setattr(match, attr, _new_values[attr])
            if invalidate_story:
                match.full_story = None

            if has_new_articles:
                # New articles arrived — allow the Bluesky poster to post an update.
                match.bsky_posted_at = None
                match.bsky_posted_rank = None
                logger.info(
                    "Rank %d '%s': new articles (article_date=%s) — updating and allowing repost",
                    rank, title[:60], primary_article_date,
                )
            else:
                logger.info(
                    "Rank %d '%s': no new articles (article_date=%s) — rank updated, no repost",
                    rank, title[:60], primary_article_date,
                )
        else:
            # Brand new topic — give it a permanent row and post to Bluesky.
            new_row = ActionIssue(date=today, rank=rank, is_current=True, **_new_values)
            db.add(new_row)
            _new_issues.append(new_row)
            logger.info("Rank %d new topic: '%s'", rank, title[:60])

        issues_created += 1

    # Flush to assign IDs to newly inserted rows, then mark them as touched.
    db.flush()
    for ni in _new_issues:
        _matched_issue_ids.add(ni.id)

    # Retire issues not touched in this run, but only after a grace period.
    # An issue must miss two consecutive hourly runs (~2h) before being retired.
    # This prevents a briefly-trending topic from displacing a solid story on
    # a single run, then the original story coming back an hour later.
    # Grace period: issue must be older than 90 minutes to be eligible for retirement.
    _grace_cutoff = utcnow() - timedelta(minutes=90)
    all_current = (
        db.query(ActionIssue)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    n_retired = 0
    n_graced = 0
    for row in all_current:
        if row.id not in _matched_issue_ids:
            if row.created_at and row.created_at > _grace_cutoff:
                # Too young to retire — give it another run to prove itself.
                n_graced += 1
            else:
                row.is_current = False
                n_retired += 1
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
    touched = sorted(
        (r for r in still_current if r.id in _matched_issue_ids),
        key=lambda r: r.rank or 0,
    )
    spared = sorted(
        (r for r in still_current if r.id not in _matched_issue_ids),
        key=lambda r: r.rank or 0,
    )
    for new_rank, row in enumerate(touched + spared, start=1):
        row.rank = new_rank

    db.commit()

    # Stage 2: Auto-detect and update national monitors (must run before
    # _save_timeline_entry so the top issue has related_monitor_slugs populated)
    _set_refresh_state(stage="monitors", stage_detail=None,
                       last_issues_created=issues_created, last_issues_retired=n_retired)
    if issues_created > 0:
        _update_national_monitors(today, db)

    # Preserve today's #1 issue in the permanent timeline
    _set_refresh_state(stage="timeline")
    if issues_created > 0:
        _save_timeline_entry(today, db)
        generate_period_summaries(today, db)

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
            today_issues = (
                db.query(ActionIssue)
                .filter(ActionIssue.date == today, ActionIssue.is_current == True)  # noqa: E712
                .order_by(ActionIssue.rank)
                .all()
            )
            bsky_posted = process_issues_for_bluesky(today_issues, db)
            _set_refresh_state(last_bsky_posted=bsky_posted or 0)
        except Exception:
            logger.exception("Bluesky posting failed (non-fatal)")

    # Stage 5: Generate full stories for issues that don't have one yet.
    # Runs every refresh (not gated on issues_created) so that stories missed
    # due to LLM timeouts or concurrent refreshes get filled in on the next cycle.
    story_issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.date == today, ActionIssue.is_current == True,  # noqa: E712
                ActionIssue.full_story.is_(None))
        .order_by(ActionIssue.rank)
        .all()
    )
    _stories_total = len(story_issues)
    _stories_done = 0
    _set_refresh_state(stage="stories", stage_detail=f"0/{_stories_total}" if _stories_total else None)
    for i, issue in enumerate(story_issues):
        _set_refresh_state(stage_detail=f"{i + 1}/{_stories_total}")
        try:
            story = _generate_full_story(issue, db_session=db)
            if story:
                issue.full_story = story
                _stories_done += 1
                db.commit()
        except Exception:
            logger.exception("Full story generation failed for issue %s (non-fatal)", issue.id)
    _set_refresh_state(last_stories_generated=_stories_done)

    # Stage 6: Daily senator score spotlight + weekly civic summary
    try:
        from app.pipeline.analyze.bluesky_spotlight import post_daily_spotlight, post_weekly_summary
        post_daily_spotlight(db)
        post_weekly_summary(db)
    except Exception:
        logger.exception("Bluesky spotlight/weekly post failed (non-fatal)")

    # Stage 7: Repost/like news outlet posts that match active issues
    try:
        from app.pipeline.analyze.bluesky_engagement import engage_with_news_posts
        engage_with_news_posts(db)
    except Exception:
        logger.exception("Bluesky engagement failed (non-fatal)")

    # Clean up unposted issues older than 14 days.
    # Issues that have been posted to Bluesky are preserved indefinitely
    # so their permalink URLs remain valid.
    from datetime import timedelta as _td
    cutoff = (datetime.now(timezone.utc) - _td(days=14)).strftime("%Y-%m-%d")
    deleted = (
        db.query(ActionIssue)
        .filter(ActionIssue.date < cutoff, ActionIssue.bsky_posted_at.is_(None))
        .delete()
    )
    if deleted:
        db.commit()
        logger.info("Cleaned up %d old unposted action issues", deleted)

    # Prune api_cache entries older than 60 days to bound unbounded growth.
    cache_cutoff = (datetime.now(timezone.utc) - _td(days=60)).isoformat()
    from app.models import ApiCache
    cache_deleted = (
        db.query(ApiCache)
        .filter(ApiCache.cached_at < cache_cutoff)
        .delete()
    )
    if cache_deleted:
        db.commit()
        logger.info("Pruned %d stale api_cache entries", cache_deleted)

    # Persist this run's validator counters (2026-07 audit M9: these
    # existed only as log lines, wiped by every deploy — validator hit
    # rates were unmeasurable). One api_cache row per run, pruned by the
    # same 60-day cleanup as every other tier.
    _metrics_snapshot = action_metrics.snapshot()
    action_metrics.persist(db, f"run-{datetime.now(_US_EAST).strftime('%Y-%m-%d-%H%M')}")
    if _metrics_snapshot:
        logger.info("Validator counters this run: %s", _metrics_snapshot)

    elapsed = time.perf_counter() - t0
    logger.info(
        "Action center refresh complete: %d issues created in %.1fs",
        issues_created, elapsed,
    )
    _set_refresh_state(
        is_running=False, stage=None, stage_detail=None,
        last_completed_at=utcnow(),
        last_elapsed=round(elapsed, 1),
    )
    return issues_created
