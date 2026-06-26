"""Action Center analysis — turns news articles into ranked civic action items.

Flow:
  1. Fetch RSS articles from low-bias sources (news_feeds.py)
  2. Embed article titles+summaries, filter for US policy relevance
  3. Fetch trending topics from social media (Google Trends, Reddit)
  4. Cluster related articles by cosine similarity
  5. Rank clusters by combined coverage breadth + trending relevance
     (0.4 coverage × 0.6 trending) so issues people are actually
     discussing get prioritized over editorial selection alone
  6. Use LLM to generate factual summary, key facts, and citizen actions
  7. Cross-reference with explore documents via embedding search
  8. Persist as ActionIssue rows for the current date
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx
import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import ActionIssue, DailyTheme, ExploreDocument, Representative, Senator
from app.pipeline.fetch.news_feeds import NewsArticle, fetch_news_articles
from app.pipeline.fetch.trending import TrendingTopic, fetch_trending_topics
from app.pipeline.vector_store import (
    get_embedding_model,
    search_explore_documents,
)

logger = logging.getLogger(__name__)

_POLICY_PROTOTYPES = [
    "Congressional legislation, bill, act, law, regulation, government policy",
    "Federal budget, government spending, appropriations, fiscal policy",
    "Supreme Court ruling, judicial decision, constitutional law",
    "Executive order, presidential action, White House policy",
    "Election, voting rights, campaign, democracy, ballot measure",
    "Healthcare policy, Medicare, Medicaid, insurance regulation",
    "Immigration law, border policy, visa, asylum, deportation",
    "Tax reform, tax policy, IRS, tax cuts, tax increases",
    "Military, defense spending, veterans, national security",
    "Climate policy, environmental regulation, EPA, energy policy",
    "Education policy, student loans, public schools, higher education",
    "Civil rights, discrimination, equality, justice reform",
    "Trade policy, tariffs, sanctions, international agreements",
    "Gun legislation, Second Amendment, firearms regulation",
    "Labor policy, minimum wage, unions, workers rights, employment",
    "Housing policy, rent, mortgage, homelessness, affordable housing",
    "Social Security, retirement, pension, entitlements",
    "Technology regulation, privacy law, antitrust, AI policy",
]

POLICY_RELEVANCE_THRESHOLD = 0.15
CLUSTER_TITLE_THRESHOLD = 0.40
CLUSTER_CENTROID_MERGE_THRESHOLD = 0.20
MAX_ISSUES = 4
MIN_ARTICLES_PER_CLUSTER = 1

ACTION_CENTER_PROMPT_VERSION = "action-v17"

_SYSTEM_PROMPT = """\
You are a nonpartisan civic information analyst. You present facts without \
opinion and help citizens engage with their government regardless of their \
political position. Never advocate for or against any policy. Present all \
sides neutrally. Each issue you analyze is a SEPARATE topic — never mix \
information from one issue into another."""

_ISSUE_PROMPT_TEMPLATE = """\
Below are recent news articles about the same U.S. policy issue. \
Analyze ONLY the topic covered in these specific articles. \
Do NOT reference bills, policies, or events not mentioned in the articles. \
Produce a JSON object with these fields:

- "title": A concise, neutral headline for this issue (max 15 words)
- "summary": A factual 2-4 sentence summary of what is happening and why \
it matters. No opinion.
- "facts": An array of 3-5 key factual bullet points citizens should know. \
Each fact must cite specific numbers, dates, or names when available.
- "actions": An array of exactly 3 objects. Each has "text" and "type". \
"type" must be one of: "contact_senator", "contact_representative", \
"contact_whitehouse", "public_comment", "track_legislation", \
"register_vote", "attend_hearing", "general". \
IMPORTANT: The FIRST action MUST be "contact_senator" or "contact_representative". \
Each action MUST name the specific topic from the articles — never say "this issue" \
or "this policy". Use the actual name of the bill, policy, or event. \
Actions must be neutral — useful whether you agree OR disagree. \
Never say "support" or "oppose" — the citizen decides their own stance. \
Example: if the articles are about a bill called the "Clean Water Act": \
[{{"text": "Contact your senators about the Clean Water Act's new regulations", "type": "contact_senator"}}, \
{{"text": "Attend a town hall to discuss the Clean Water Act's impact on your community", "type": "attend_hearing"}}, \
{{"text": "Read the full Clean Water Act text on Congress.gov", "type": "track_legislation"}}] \
CRITICAL: Do NOT copy this example. Write actions about the ACTUAL topic in the articles.
- "bills": An array of any specific bills or acts mentioned in the articles. \
For each bill, provide an object with "name" (the common name) \
and "id" (the bill number if mentioned anywhere in the articles, e.g. "HR.22" \
or "S.100", or null if not found). Look carefully for references like \
"H.R. 22", "S. 100", etc. Only include bills actually named in the articles. \
If NO bills are mentioned, return an empty array [].
Articles:
{articles}

Respond with ONLY the JSON object."""


_ACTION_TYPE_KEYWORDS: dict[str, list[str]] = {
    "contact_senator": ["senator", "senate"],
    "contact_representative": ["representative", "congress", "house"],
    "contact_whitehouse": ["president", "white house", "executive"],
    "public_comment": ["public comment", "comment period", "regulations.gov"],
    "track_legislation": ["bill", "legislation", "track", "congress.gov"],
    "register_vote": ["register", "vote", "voter"],
    "attend_hearing": ["hearing", "town hall", "meeting", "attend"],
}


def _infer_action_type(text: str) -> str:
    """Infer an action type from free-text using embedding similarity."""
    lower = text.lower()
    prototypes = {
        "contact_senator": "Contact your US senators about this issue",
        "contact_representative": "Contact your House representative",
        "contact_whitehouse": "Write to the President or White House",
        "public_comment": "Submit a public comment on regulations.gov",
        "track_legislation": "Track this bill on congress.gov",
        "register_vote": "Register to vote or check voter registration",
        "attend_hearing": "Attend a town hall or public hearing",
    }
    for atype, keywords in _ACTION_TYPE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return atype
    try:
        action_emb = _embed_texts([text])
        proto_texts = list(prototypes.values())
        proto_embs = _embed_texts(proto_texts)
        sims = (action_emb @ proto_embs.T).flatten()
        best_idx = int(np.argmax(sims))
        if sims[best_idx] > 0.45:
            return list(prototypes.keys())[best_idx]
    except Exception:
        pass
    return "general"


def _normalize_actions(raw_actions: list, title: str = "") -> list[dict]:
    """Normalize LLM actions into structured {text, type} objects."""
    contact_types = {"contact_senator", "contact_representative", "contact_whitehouse"}
    valid_types = contact_types | {
        "public_comment", "track_legislation", "register_vote",
        "attend_hearing", "general",
    }

    result: list[dict] = []
    for item in raw_actions:
        if isinstance(item, dict) and "text" in item:
            atype = item.get("type", "general")
            if atype not in valid_types:
                atype = _infer_action_type(item["text"])
            result.append({"text": item["text"], "type": atype})
        elif isinstance(item, str):
            atype = _infer_action_type(item)
            result.append({"text": item, "type": atype})

    has_contact = any(a["type"] in contact_types for a in result)
    if not has_contact and result:
        topic = title or "this issue"
        result.insert(0, {
            "text": f"Contact your senators or representative to share your position on {topic}",
            "type": "contact_senator",
        })
        if len(result) > 3:
            result = result[:3]

    return result


def _enrich_actions(
    actions: list[dict],
    title: str,
    resolved_bills: list[dict],
    source_urls: list[str],
    source_names: list[str],
    related_senators: list[dict],
) -> list[dict]:
    """Add specific URLs to actions while preserving LLM-generated text.

    The LLM produces specific action text (e.g., "Contact your senators about
    the SAVE Act voter ID requirements"). This function adds direct URLs but
    only overwrites text when the LLM produced something too generic to be
    useful (e.g., "this issue", "this policy").
    """
    bill_url = resolved_bills[0]["url"] if resolved_bills else None
    bill_name = resolved_bills[0].get("name", "") if resolved_bills else ""
    primary_source = source_urls[0] if source_urls else None
    primary_source_name = source_names[0] if source_names else ""
    senator_names = [s["name"] for s in related_senators[:3]] if related_senators else []

    def _is_generic(text: str) -> bool:
        lower = text.lower()
        return any(p in lower for p in (
            "this issue", "this policy", "this topic",
            "the issue", "the policy", "related issue",
        ))

    enriched: list[dict] = []
    for a in actions:
        a = dict(a)
        atype = a.get("type", "general")
        text = a.get("text", "")

        if atype in ("contact_senator", "contact_representative"):
            if not a.get("url"):
                if atype == "contact_senator":
                    a["url"] = "https://www.senate.gov/senators/senators-contact.htm"
                else:
                    a["url"] = "https://www.house.gov/representatives/find-your-representative"
            if _is_generic(text):
                parts = [f"Contact your {'senators' if atype == 'contact_senator' else 'representative'} about {title}"]
                if senator_names:
                    parts.append(f"Key figures: {', '.join(senator_names)}")
                a["text"] = ". ".join(parts)

        elif atype == "contact_whitehouse":
            if not a.get("url"):
                a["url"] = "https://www.whitehouse.gov/contact/"
            if _is_generic(text):
                a["text"] = f"Contact the White House about {title}"

        elif atype == "track_legislation":
            if bill_url:
                a["url"] = bill_url
                if _is_generic(text):
                    a["text"] = (
                        f"Read the full text of {bill_name} on Congress.gov"
                        if bill_name else
                        "Read the related legislation on Congress.gov"
                    )
            elif not a.get("url"):
                if primary_source:
                    a["url"] = primary_source
                else:
                    from urllib.parse import quote
                    a["url"] = f"https://www.congress.gov/search?q={quote(title[:80])}"

        elif atype == "public_comment":
            if not a.get("url"):
                a["url"] = "https://www.regulations.gov"

        elif atype == "attend_hearing":
            if not a.get("url"):
                a["url"] = "https://townhallproject.com"

        elif atype == "register_vote":
            if not a.get("url"):
                a["url"] = "https://vote.gov"

        elif atype == "general":
            if not a.get("url") and primary_source:
                a["url"] = primary_source

        enriched.append(a)

    seen_urls: set[str] = set()
    deduped: list[dict] = []
    secondary_sources = list(zip(source_urls, source_names))
    for a in enriched:
        url = a.get("url")
        if url and url in seen_urls:
            for su, sn in secondary_sources:
                if su not in seen_urls:
                    a["url"] = su
                    break
        if a.get("url"):
            seen_urls.add(a["url"])
        deduped.append(a)

    return deduped


def _embed_texts(texts: list[str]) -> np.ndarray:
    model = get_embedding_model()
    return model.encode(texts, show_progress_bar=False, normalize_embeddings=True)


def _filter_policy_relevant(
    articles: list[NewsArticle],
) -> list[tuple[NewsArticle, np.ndarray]]:
    """Keep only articles that are about US policy/legislation."""
    if not articles:
        return []

    prototype_embeddings = _embed_texts(_POLICY_PROTOTYPES)
    prototype_mean = prototype_embeddings.mean(axis=0)
    prototype_mean /= np.linalg.norm(prototype_mean)

    texts = [f"{a.title}. {a.summary[:200]}" for a in articles]
    article_embeddings = _embed_texts(texts)

    scores = article_embeddings @ prototype_mean
    relevant: list[tuple[NewsArticle, np.ndarray]] = []
    for i, (article, score) in enumerate(zip(articles, scores)):
        if score >= POLICY_RELEVANCE_THRESHOLD:
            relevant.append((article, article_embeddings[i]))

    logger.info(
        "Policy relevance filter: %d/%d articles passed (threshold=%.2f)",
        len(relevant), len(articles), POLICY_RELEVANCE_THRESHOLD,
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
        merge_groups = _agglomerative_cluster(centroid_sim, CLUSTER_CENTROID_MERGE_THRESHOLD)

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
        return [0.0] * len(clusters)

    trending_texts = [t.title for t in trending]
    trending_embeddings = _embed_texts(trending_texts)

    boosts: list[float] = []
    for cluster in clusters:
        cluster_texts = [f"{a.title}. {a.summary[:100]}" for a in cluster]
        cluster_embeddings = _embed_texts(cluster_texts)
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


def _rank_clusters(
    clusters: list[list[NewsArticle]],
    trending: list[TrendingTopic],
) -> list[list[NewsArticle]]:
    """Rank clusters by combined coverage breadth and trending relevance.

    Final score = 0.4 * normalized_coverage + 0.6 * trending_similarity.
    Coverage breadth (distinct source count) is still valued but trending
    signal from social media gets more weight since it reflects actual
    public interest.
    """
    if not clusters:
        return []

    coverage_scores = [len({a.source_name for a in c}) + len(c) * 0.1 for c in clusters]
    max_cov = max(coverage_scores) if coverage_scores else 1.0
    norm_coverage = [s / max_cov for s in coverage_scores]

    trending_boosts = _compute_trending_boost(clusters, trending)
    max_trend = max(trending_boosts) if trending_boosts and max(trending_boosts) > 0 else 1.0
    norm_trending = [s / max_trend for s in trending_boosts]

    COVERAGE_WEIGHT = 0.4
    TRENDING_WEIGHT = 0.6

    combined = [
        COVERAGE_WEIGHT * cov + TRENDING_WEIGHT * trend
        for cov, trend in zip(norm_coverage, norm_trending)
    ]

    ranked_indices = sorted(range(len(clusters)), key=lambda i: combined[i], reverse=True)

    for i, idx in enumerate(ranked_indices[:6]):
        c = clusters[idx]
        titles = c[0].title[:60]
        logger.info(
            "  Rank %d: score=%.3f (cov=%.2f trend=%.2f) sources=%d \"%s...\"",
            i + 1, combined[idx], norm_coverage[idx], norm_trending[idx],
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

    # Threshold in centered-embedding space (see _cluster_articles for rationale)
    DEDUP_THRESHOLD = 0.15

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


def _build_llm_prompt(cluster: list[NewsArticle]) -> str:
    parts: list[str] = []
    for a in cluster[:8]:
        line = f"[{a.source_name}] {a.title}"
        if a.summary:
            line += f"\n  {a.summary[:300]}"
        parts.append(line)
    return _ISSUE_PROMPT_TEMPLATE.format(articles="\n\n".join(parts))


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
        Senator.leadership_score,
    ).all()

    representatives = db.query(
        Representative.id, Representative.name, Representative.state, Representative.party,
        Representative.score_funding_independence, Representative.score_promise_persistence,
        Representative.score_independent_voting, Representative.score_funding_diversity,
        Representative.score_legislative_effectiveness,
        Representative.leadership_score,
    ).all()

    if not senators and not representatives:
        return []

    issue_text = f"{title}. {summary}. {' '.join(facts)}"
    issue_text_lower = issue_text.lower()

    matched: dict[str, dict] = {}

    def _make_entry(s, chamber: str = "senate", match_reason: str | None = None) -> dict:
        from app.config_definitions import SCORE_WEIGHTS
        overall = (
            s.score_funding_independence * SCORE_WEIGHTS["fundingIndependence"]
            + s.score_promise_persistence * SCORE_WEIGHTS["promisePersistence"]
            + s.score_independent_voting * SCORE_WEIGHTS["independentVoting"]
            + s.score_funding_diversity * SCORE_WEIGHTS["fundingDiversity"]
            + getattr(s, "score_legislative_effectiveness", 0) * SCORE_WEIGHTS["legislativeEffectiveness"]
        )
        return {
            "id": s.id, "name": s.name, "state": s.state,
            "party": s.party, "overall_score": round(overall, 1),
            "leadership_score": round(s.leadership_score * 100) if s.leadership_score else None,
            "chamber": chamber,
            "match_reason": match_reason,
        }

    # Pass 1: substring matches with contextual disambiguation
    candidates_needing_disambiguation: list[tuple] = []

    all_members = [(s, "senate") for s in senators] + [(r, "house") for r in representatives]

    for s, chamber in all_members:
        last_name = s.name.split()[-1].lower() if s.name else ""
        full_name_lower = s.name.lower()

        if len(last_name) < 4:
            continue

        # Full name match is high-confidence — no disambiguation needed
        if full_name_lower in issue_text_lower:
            matched[s.id] = _make_entry(s, chamber, match_reason="named in coverage")
            continue

        # Last-name-only match needs word-boundary + disambiguation
        pattern = re.compile(r"\b" + re.escape(last_name) + r"\b", re.IGNORECASE)
        if pattern.search(issue_text):
            candidates_needing_disambiguation.append((s, last_name, pattern, chamber))

    if candidates_needing_disambiguation:
        senator_phrases = []
        context_phrases = []
        candidate_refs = []

        for s, last_name, pattern, chamber in candidates_needing_disambiguation:
            if s.id in matched:
                continue
            senator_phrases.append(f"Senator {s.name} from {s.state}")

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


def _classify_issue_policy_areas(title: str, summary: str) -> list[str]:
    """Classify an action center issue into policy areas using embeddings.

    Uses the same embedding-based classifier as bills (tier 2) rather than
    relying on the LLM, which inconsistently returns empty or wrong labels.
    """
    from app.pipeline.analyze.bill_analyzer import classify_policy_areas_multi

    text = f"{title}. {summary}"
    try:
        areas = classify_policy_areas_multi(text, max_areas=3)
        result = [
            a["area"] for a in areas
            if a["area"] != "PROCEDURAL" and a.get("confidence", 0) > 0.15
        ]
        if result:
            logger.debug("Policy areas for '%s': %s", title[:50], result)
            return result
    except Exception as e:
        logger.warning("Policy area classification failed: %s", e)
    return []


_EXPLORE_DOC_MAX_DISTANCE = 1.10


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
        all_embs = _embed_texts([title] + doc_texts)
        title_emb = all_embs[0]
        doc_embs = all_embs[1:]
        sims = np.array([float(np.dot(title_emb, d)) for d in doc_embs])
    except Exception:
        sims = np.zeros(len(passed))

    min_sim = 0.40

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

    # Collect LLM-extracted bills
    for b in raw_bills:
        if isinstance(b, dict) and b.get("name"):
            llm_id = b.get("id") or None
            if llm_id and re.search(r'\d', llm_id):
                norm = re.sub(r'\s+', '', llm_id).upper()
                norm = norm.replace("H.R.", "HR.").replace("HR", "HR.")
                norm = re.sub(r'\.+', '.', norm)
                if not norm.startswith("S."):
                    norm = norm.replace("S", "S.")
                    norm = re.sub(r'\.+', '.', norm)
                if norm not in seen_raw:
                    seen_raw.add(norm)
                    id_refs.append({"name": b["name"], "id": norm})
            else:
                name_refs.append({"name": b["name"], "id": None})

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
                url = (
                    f"https://www.congress.gov/bill/"
                    f"{settings.CURRENT_CONGRESS}th-congress/"
                    f"{url_type}/{parts[1]}"
                )
                resolved.append({
                    "name": ref["name"], "id": bill_id, "url": url,
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
    bill_congress = bill.get("congress", congress)

    if not bill_type or not bill_number:
        return None
    mapped = _BILL_TYPE_MAP.get(bill_type)
    if not mapped:
        return None

    prefix, url_type = mapped
    bill_id = f"{prefix}.{bill_number}"
    url = (
        f"https://www.congress.gov/bill/"
        f"{bill_congress}th-congress/{url_type}/{bill_number}"
    )
    return {
        "name": bill.get("title", query)[:200],
        "id": bill_id,
        "url": url,
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


_THEME_ICON_LIBRARY: dict[str, str] = {
    "flag": "M4 2v20M4 4h12l-2 4 2 4H4",
    "globe": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20ZM2 12h20M12 2c-3 3-4.5 7-4.5 10s1.5 7 4.5 10M12 2c3 3 4.5 7 4.5 10s-1.5 7-4.5 10",
    "shield": "M12 2L3 7v5c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V7Z",
    "gavel": "M14.5 2.5l7 7-1.4 1.4-7-7ZM3 21l5-5M7.5 9.5l7 7M2.5 14.5l7 7-1.4 1.4-7-7Z",
    "money": "M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6",
    "scales": "M12 2v20M2 7l10-3 10 3M4 7l-2 8c0 2.2 2 4 4 4s4-1.8 4-4L8 7M14 7l-2 8c0 2.2 2 4 4 4s4-1.8 4-4l-2-8",
    "health": "M8 2h8v6h6v8h-6v6H8v-6H2v-8h6Z",
    "lightning": "M13 2L3 14h8l-1 8 10-12h-8Z",
    "fire": "M12 22c4.4 0 8-3.6 8-8 0-4-2.5-6-4-8-1.5-2-2-4-2-6-1 2-3.5 4-4 6-1 4-4 6-4 6 0 4.4 2.6 8 6 8Z",
    "target": "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20ZM12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12ZM12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z",
    "handshake": "M2 14l5-5 4 4 6-6 5 5M7 9l-5 5M20 12l-3 3",
    "megaphone": "M3 11v3a1 1 0 0 0 1 1h2l5 5V6L6 11H4a1 1 0 0 0-1 0ZM16 8a4 4 0 0 1 0 8M19 5a8 8 0 0 1 0 14",
    "warning": "M12 2L1 21h22ZM12 9v4M12 17h0",
    "military": "M12 2l4 3h4v4l3 4-3 4v4h-4l-4 3-4-3H4v-4L1 13l3-4V5h4Z",
    "capitol": "M2 20h20M4 20v-6h16v6M6 14v-4h12v4M10 10V6h4v4M12 6V2",
    "vote": "M9 11l3 3L22 4M20 12v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h11",
    "document": "M4 2h10l6 6v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2ZM14 2v6h6M8 13h8M8 17h8M8 9h4",
    "oil": "M12 2c-4 4-8 6-8 12a8 8 0 0 0 16 0c0-6-4-8-8-12Z",
    "atom": "M12 12m-1 0a1 1 0 1 0 2 0 1 1 0 1 0-2 0ZM12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10M12 2a15.3 15.3 0 0 0-4 10 15.3 15.3 0 0 0 4 10M2 12h20",
    "chart": "M4 20V10M10 20V4M16 20v-6M22 20v-4",
    "users": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
    "prison": "M4 2v20M20 2v20M8 2v20M12 2v20M16 2v20M2 12h20",
    "plane": "M22 2L11 13M22 2l-7 20-4-9-9-4Z",
    "factory": "M2 20V8l5 4V8l5 4V8l5 4V2h5v18Z",
    "tree": "M12 22v-6M8 16l4-12 4 12H8ZM5 20l7-8 7 8H5Z",
    "home": "M3 12l9-9 9 9M5 10v10h14V10",
}

_THEME_SYSTEM_PROMPT = """\
You generate JSON. No explanation, no markdown, just a raw JSON object."""


_THEME_CONCEPT_TEMPLATE = """\
Today's #1 headline on a dark cyberpunk-themed civic news dashboard:
Title: {title}
Summary: {summary}

Choose an icon and colors that SPECIFICALLY represent this topic.

Icon choices: {icon_list}

Color guidance — pick colors that SYMBOLIZE the specific subject:
- Foreign policy with Russia: use Russian flag colors (#ff0000 red, #0039a6 blue)
- Foreign policy with China: use Chinese flag colors (#de2910 red, #ffde00 gold)
- Foreign policy with Ukraine: use Ukrainian flag colors (#0057b8 blue, #ffd700 gold)
- Foreign policy with Iran: use Iranian flag colors (#239f40 green, #da0000 red)
- Military/defense: use olive/military green (#556b2f, #8b0000 dark red)
- Economy/finance/trade: use gold/money green (#ffd700, #00c853)
- Healthcare: use medical blue/red (#0077b6, #e63946)
- Climate/environment: use earth green/sky blue (#2d6a4f, #48cae4)
- Gun policy: use gunmetal/amber (#4a4a4a, #ff8f00)
- Immigration: use warm amber/teal (#f4a261, #2a9d8f)
- Supreme Court/justice: use judicial purple/gold (#6a0dad, #daa520)
- Elections/voting: use patriotic red/blue (#b22234, #3c3b6e)
- Technology/AI: use electric cyan/purple (#00ffff, #9d4edd)
- Energy/oil: use petroleum black-gold/orange (#1a1a2e, #e76f51)
DO NOT default to generic orange/purple. The colors must match the TOPIC.

Return a JSON object:
{{"tagline":"<3-6 word evocative tagline about this specific topic>",\
"mood":"<urgent|tense|volatile|hopeful|somber|charged|divided|uncertain>",\
"icon":"<one of: {icon_list}>",\
"accent":"<hex color that symbolizes the topic — see guidance above>",\
"accentAlt":"<second hex color, complementary, also topic-specific>"}}

JSON only:"""


def _generate_daily_theme(
    issues: list[ActionIssue],
    today: str,
    db: Session,
) -> dict | None:
    """Generate a topic-specific visual theme for the hero issue."""
    from app.pipeline.analyze.ollama_client import call_llm, extract_json

    if not issues:
        return None

    hero = issues[0]
    icon_list = ", ".join(sorted(_THEME_ICON_LIBRARY.keys()))

    concept_result = call_llm(
        prompt_version=ACTION_CENTER_PROMPT_VERSION + "-theme-concept",
        system_prompt=_THEME_SYSTEM_PROMPT,
        user_prompt=_THEME_CONCEPT_TEMPLATE.format(
            title=hero.title,
            summary=hero.summary or "",
            icon_list=icon_list,
        ),
        cache_key={"date": today, "type": "theme-concept", "title": hero.title},
        db_session=db,
        max_tokens=512,
        num_ctx=4096,
    )

    if not concept_result:
        logger.warning("Theme concept LLM returned empty")
        return None

    if isinstance(concept_result, str):
        concept_result = extract_json(concept_result)
    if isinstance(concept_result, list) and concept_result:
        concept_result = concept_result[0] if isinstance(concept_result[0], dict) else None
    if not isinstance(concept_result, dict):
        logger.warning("Theme concept not a dict: %s", type(concept_result))
        return None

    _HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
    _MOOD_VALID = {"urgent", "tense", "volatile", "hopeful", "somber", "charged", "divided", "uncertain"}

    tagline = re.sub(r'[^\w\s\-\'",.:!?/]', '', concept_result.get("tagline", "BREAKING"))[:60]
    raw_mood = concept_result.get("mood", "urgent")
    mood = raw_mood if raw_mood in _MOOD_VALID else "urgent"

    icon_key = concept_result.get("icon", "globe")
    if icon_key not in _THEME_ICON_LIBRARY:
        icon_key = "globe"
    svg_path = _THEME_ICON_LIBRARY[icon_key]

    accent = concept_result.get("accent", "#ff6644")
    accent_alt = concept_result.get("accentAlt", "#6644ff")

    if not _HEX_RE.match(accent):
        accent = "#ff6644"
    if not _HEX_RE.match(accent_alt):
        accent_alt = "#6644ff"

    accent = _ensure_vibrant(accent)
    accent_alt = _ensure_vibrant(accent_alt)

    glow = 0.7 if mood in ("urgent", "volatile", "charged") else 0.4
    speed = 2 if mood in ("urgent", "volatile") else 3

    custom_css = _build_watermark_css(svg_path, accent, accent_alt)

    logger.info(
        "Theme: mood=%s icon=%s accent=%s/%s tagline='%s'",
        mood, icon_key, accent, accent_alt, tagline,
    )

    result = {
        "tagline": tagline,
        "mood": mood,
        "accent": accent,
        "accentAlt": accent_alt,
        "glowIntensity": glow,
        "animationSpeed": speed,
        "borderStyle": "solid",
        "heroGradient": [
            _darken(accent, 0.06),
            _darken(accent_alt, 0.07),
            _darken(accent, 0.05),
        ],
        "customCSS": custom_css,
    }
    return result


def _build_watermark_css(svg_path: str, accent: str, accent_alt: str) -> str:
    """Build CSS for a prominent tiled SVG watermark across the hero panel.

    Uses curated SVG path data from the icon library. Renders a subtle
    repeating tile plus a larger accent icon in the bottom-right.
    """
    if not svg_path or len(svg_path) < 5:
        return ""

    if not re.match(r'^[MLCZAHVSQTmlczahvsqt0-9.,\- ]+$', svg_path):
        return ""

    from urllib.parse import quote

    tile_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{accent}" stroke-width="0.8" '
        f'opacity="0.4" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{svg_path}"/></svg>'
    )
    encoded_tile = quote(tile_svg, safe='')

    hero_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{accent_alt}" stroke-width="1.2" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{svg_path}"/></svg>'
    )
    encoded_hero = quote(hero_svg, safe='')

    hex_encoded = accent.replace("#", "%23")

    return (
        ".theme-hero-panel::before {\n"
        "  content: '';\n"
        "  position: absolute;\n"
        "  inset: 0;\n"
        "  pointer-events: none;\n"
        "  z-index: 0;\n"
        f"  background-image: url(\"data:image/svg+xml,{encoded_tile}\");\n"
        "  background-repeat: repeat;\n"
        "  background-size: 48px 48px;\n"
        "  opacity: 0.035;\n"
        "}\n"
        ".theme-hero-panel::after {\n"
        "  content: '';\n"
        "  position: absolute;\n"
        "  bottom: 12px;\n"
        "  right: 12px;\n"
        "  width: 120px;\n"
        "  height: 120px;\n"
        "  pointer-events: none;\n"
        "  z-index: 1;\n"
        f"  background-image: url(\"data:image/svg+xml,{encoded_hero}\");\n"
        "  background-repeat: no-repeat;\n"
        "  background-size: contain;\n"
        "  opacity: 0.08;\n"
        f"  filter: drop-shadow(0 0 8px {hex_encoded});\n"
        "}\n"
    )


def _ensure_vibrant(hex_color: str) -> str:
    """Boost a hex color if its perceived brightness is too low.

    Uses ITU-R BT.601 luminance weights. Targets a minimum perceived
    brightness of ~60/255 so the accent is visible on near-black.
    """
    try:
        c = hex_color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        if lum < 60:
            scale = 80 / max(lum, 1)
            r = min(int(r * scale), 255)
            g = min(int(g * scale), 255)
            b = min(int(b * scale), 255)
            return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        pass
    return hex_color


def _darken(hex_color: str, darkness: float = 0.12) -> str:
    """Produce a near-black color that preserves the hue of *hex_color*.

    *darkness* controls the target brightness (0 = black, 1 = full).
    """
    try:
        c = hex_color.lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        r, g, b = int(r * darkness), int(g * darkness), int(b * darkness)
        return f"#{max(r,5):02x}{max(g,5):02x}{max(b,5):02x}"
    except (ValueError, IndexError):
        return "#0a0a0f"


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
    current_week_monday = today - timedelta(days=today.weekday())
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

    weeks: dict[int, list] = {}
    for e in entries_this_year:
        d = datetime.strptime(e.date, "%Y-%m-%d").date()
        wnum = d.isocalendar()[1]
        weeks.setdefault(wnum, []).append(e)

    for wnum, week_entries in weeks.items():
        if wnum == current_week_num:
            continue  # current week is not complete yet
        # Compute Monday/Sunday for this week
        first_entry_date = datetime.strptime(week_entries[0].date, "%Y-%m-%d").date()
        monday = first_entry_date - timedelta(days=first_entry_date.weekday())
        sunday = monday + timedelta(days=6)
        if sunday >= today:
            continue  # week hasn't fully ended yet

        existing = (
            db.query(WeekSummary)
            .filter(WeekSummary.year == current_year, WeekSummary.week_num == wnum)
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
            cache_key={"period": "week", "year": current_year, "week": wnum},
            db=db,
        )
        db.add(WeekSummary(
            year=current_year,
            week_num=wnum,
            start_date=monday.strftime("%Y-%m-%d"),
            end_date=sunday.strftime("%Y-%m-%d"),
            summary=llm["summary"],
            top_policy_areas=_json.dumps(llm["topAreas"] or computed_areas),
            entry_count=len(week_entries),
        ))
        db.commit()
        logger.info("Generated week-in-review for %s W%d", current_year, wnum)

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


def _generate_full_story(issue) -> str | None:
    """Generate a thorough 600-900 word factual deep-dive for an action issue.

    Returns plain text (paragraphs separated by double newlines), or None on failure.
    Stored in action_issues.full_story so it is ready before users click through.
    """
    from app.pipeline.analyze.ollama_client import call_llm

    facts = json.loads(issue.facts or "[]")
    source_names = json.loads(issue.source_names or "[]")
    source_urls = json.loads(issue.source_urls or "[]")
    policy_areas = json.loads(issue.policy_areas or "[]")

    facts_text = "\n".join(f"- {f}" for f in facts) if facts else "(none provided)"
    sources_text = ", ".join(source_names[:10]) if source_names else "(none provided)"
    policy_text = ", ".join(policy_areas) if policy_areas else "(none provided)"

    sources_with_urls = []
    for i, name in enumerate(source_names[:10]):
        url = source_urls[i] if i < len(source_urls) else ""
        sources_with_urls.append(f"{name}: {url}" if url else name)
    sources_detail = "\n".join(f"- {s}" for s in sources_with_urls)

    user_prompt = f"""Write a thorough, factual deep-dive article on the following civic issue for Civitas, a U.S. civic transparency platform.

Your article will be displayed as the primary reading experience — citizens should be able to understand everything they need to know without visiting external sources.

STRICT REQUIREMENTS:
- 600-900 words of clear, accessible prose
- Factual and non-partisan — present facts, not opinions
- Cover all five sections below in flowing paragraphs (no headers, no bullet points in the output)
- Do not speculate beyond what the sources support
- Cite specific details and data points from the key facts
- Write in present tense where appropriate

FIVE SECTIONS TO COVER IN ORDER:
1. What is happening right now and why it matters
2. Background and context — how did we get here?
3. Key developments and specific details (use the key facts provided)
4. How government and Congress are responding
5. What this means for ordinary Americans

ISSUE TITLE: {issue.title}
BRIEF SUMMARY: {issue.summary or "(none)"}

KEY FACTS FROM REPORTING:
{facts_text}

POLICY AREAS: {policy_text}
NEWS SOURCES: {sources_text}

Return JSON: {{"story": "full article text with paragraphs separated by \\n\\n"}}"""

    result = call_llm(
        prompt_version="full_story_v1",
        system_prompt=(
            "You are a senior civic journalist writing factual, thorough, accessible "
            "articles for Civitas — a non-partisan platform that aggregates U.S. government "
            "data. Your goal is to give citizens a complete picture of what is happening in "
            "Washington and why it matters to them. Write clearly for a general audience "
            "without being condescending."
        ),
        user_prompt=user_prompt,
        cache_key=f"full_story:{issue.id}:{issue.title[:80]}",
        db_session=None,
        max_tokens=1536,
        num_ctx=4096,
    )

    if not result or not isinstance(result.get("story"), str):
        logger.warning("Full story generation returned no result for issue %s", issue.id)
        return None

    story = result["story"].strip()
    if len(story) < 200:
        logger.warning("Full story too short (%d chars) for issue %s", len(story), issue.id)
        return None

    logger.info("Generated full story for issue %s (%d chars): %s", issue.id, len(story), issue.title[:60])
    return story


def _save_timeline_entry(today: str, db: Session) -> None:
    """Preserve today's #1 issue as a permanent timeline entry."""
    from app.models import TimelineEntry

    top_issue = (
        db.query(ActionIssue)
        .filter(ActionIssue.date == today, ActionIssue.rank == 1)
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


_MONITOR_ISSUE_SIM = 0.62
_MONITOR_ISSUE_TITLE_SIM = 0.48
_MONITOR_MERGE_SIM = 0.42
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


def _should_merge_monitors_llm(
    a: "NationalMonitor",
    b: "NationalMonitor",
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
        logger.info("LLM approved merge: '%s' + '%s' because: %s",
                    a.title, b.title, result.get("reason"))
        return True
    return False


def _reclassify_monitor_llm(
    monitor: "NationalMonitor",
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


def _merge_monitors(keep: "NationalMonitor", absorb: "NationalMonitor",
                    db: Session) -> None:
    """Merge two monitors: move updates from `absorb` into `keep`, delete `absorb`."""
    from app.models import MonitorUpdate

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
    from app.models import NationalMonitor, MonitorUpdate
    from datetime import timedelta

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
    if len(existing_monitors) >= 2:
        mon_embs = model.encode(
            [f"{m.title} {m.description}" for m in existing_monitors],
            normalize_embeddings=True,
        )
        mon_title_embs = model.encode(
            [m.title for m in existing_monitors],
            normalize_embeddings=True,
        )
        merged_ids: set[int] = set()
        for a_idx in range(len(existing_monitors)):
            if existing_monitors[a_idx].id in merged_ids:
                continue
            for b_idx in range(a_idx + 1, len(existing_monitors)):
                if existing_monitors[b_idx].id in merged_ids:
                    continue
                full_sim = float((mon_embs[a_idx] @ mon_embs[b_idx].T).item())
                title_sim = float((mon_title_embs[a_idx] @ mon_title_embs[b_idx].T).item())
                
                # Use a tiered merge logic
                should_merge = False
                if full_sim >= 0.55 or title_sim >= 0.55:
                    should_merge = True
                elif full_sim >= _MONITOR_MERGE_SIM or title_sim >= _MONITOR_MERGE_SIM:
                    # Moderate similarity - use LLM to verify
                    should_merge = _should_merge_monitors_llm(
                        existing_monitors[a_idx], existing_monitors[b_idx], db
                    )

                if should_merge:
                    keep = existing_monitors[a_idx]
                    absorb = existing_monitors[b_idx]
                    if len(keep.updates or []) < len(absorb.updates or []):
                        keep, absorb = absorb, keep
                    _merge_monitors(keep, absorb, db)
                    merged_ids.add(absorb.id)

        if merged_ids:
            db.flush()
            existing_monitors = db.query(NationalMonitor).all()

    # Step 2: Match today's issues to existing monitors and add updates
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
                if sims[i][j] < _MONITOR_ISSUE_SIM:
                    continue
                if title_sims[i][j] < _MONITOR_ISSUE_TITLE_SIM:
                    continue

                issue_monitor_slugs.setdefault(i, []).append(monitor.slug)

                source_urls = json.loads(issue.source_urls or "[]")
                source_names = json.loads(issue.source_names or "[]")
                if not source_urls:
                    matched_issues.add(i)
                    continue

                already_exists = (
                    db.query(MonitorUpdate)
                    .filter(
                        MonitorUpdate.monitor_id == monitor.id,
                        MonitorUpdate.date == today,
                        MonitorUpdate.source_url == source_urls[0],
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
                monitor.status = "active"
                matched_issues.add(i)
                logger.info("Monitor updated: '%s' <- '%s'",
                            monitor.title, issue.title[:60])

    # Tag issues with their related monitor slugs
    for i, issue in enumerate(today_issues):
        slugs = issue_monitor_slugs.get(i, [])
        if slugs:
            issue.related_monitor_slugs = json.dumps(slugs)

    # Step 3: Detect new recurring topics from unmatched issues
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
                if sims[i][j] >= _MONITOR_ISSUE_SIM:
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
                status="active",
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
    if len(all_monitors) >= 2:
        mon_embs2 = model.encode(
            [f"{m.title} {m.description}" for m in all_monitors],
            normalize_embeddings=True,
        )
        mon_title_embs2 = model.encode(
            [m.title for m in all_monitors],
            normalize_embeddings=True,
        )
        merged_ids2: set[int] = set()
        for a_idx in range(len(all_monitors)):
            if all_monitors[a_idx].id in merged_ids2:
                continue
            for b_idx in range(a_idx + 1, len(all_monitors)):
                if all_monitors[b_idx].id in merged_ids2:
                    continue
                full_sim = float((mon_embs2[a_idx] @ mon_embs2[b_idx].T).item())
                title_sim = float((mon_title_embs2[a_idx] @ mon_title_embs2[b_idx].T).item())
                
                should_merge = False
                if full_sim >= 0.55 or title_sim >= 0.55:
                    should_merge = True
                elif full_sim >= _MONITOR_MERGE_SIM or title_sim >= _MONITOR_MERGE_SIM:
                    should_merge = _should_merge_monitors_llm(
                        all_monitors[a_idx], all_monitors[b_idx], db
                    )

                if should_merge:
                    keep = all_monitors[a_idx]
                    absorb = all_monitors[b_idx]
                    if len(keep.updates or []) < len(absorb.updates or []):
                        keep, absorb = absorb, keep
                    _merge_monitors(keep, absorb, db)
                    merged_ids2.add(absorb.id)
        if merged_ids2:
            db.flush()

    # Step 4: Lifecycle management — watching, closing, and cleaning up
    _cleanup_monitor_lifecycle(today, db)

    db.commit()


def _cleanup_monitor_lifecycle(today: str, db: Session) -> None:
    """Close inactive monitors and delete insignificant ones."""
    from datetime import timedelta
    from app.models import NationalMonitor

    dormant_cutoff = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_MONITOR_DORMANT_DAYS)
    ).strftime("%Y-%m-%d")
    close_cutoff = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=_MONITOR_CLOSE_DAYS)
    ).strftime("%Y-%m-%d")

    all_monitors = db.query(NationalMonitor).filter(NationalMonitor.status != "closed").all()
    
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
                m.status = "closed"
                logger.info("Closing monitor due to inactivity: '%s'", m.title)
        
        # 2. Mark active monitors as "watching" if inactive for >7 days
        elif m.status == "active" and last_date < dormant_cutoff:
            m.status = "watching"
            logger.info("Monitor set to watching: '%s'", m.title)
        
        # 3. Periodically re-categorize active monitors to keep taxonomy accurate
        elif m.status == "active":
            # Probability-based or simple toggle to avoid too many LLM calls
            # For now, let's just do it if it's currently 'general' or 'defense' (the most common mis-labels)
            if m.category in ("general", "defense", "guns", "trade"):
                _reclassify_monitor_llm(m, db)

    db.commit()


def refresh_action_issues(db: Session | None = None) -> int:
    """Run the full action center pipeline. Returns number of issues created."""
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        return _run_refresh(db)
    finally:
        if own_session:
            db.close()


def _run_refresh(db: Session) -> int:
    t0 = time.perf_counter()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("Action center refresh starting for %s", today)

    # 1. Fetch articles
    articles = fetch_news_articles()
    if not articles:
        logger.warning("No articles fetched — skipping action center refresh")
        return 0

    # 2. Filter for policy relevance
    relevant = _filter_policy_relevant(articles)
    if not relevant:
        logger.warning("No policy-relevant articles found")
        return 0

    # 3. Fetch trending topics from social media
    trending = fetch_trending_topics()

    # 4. Cluster by topic
    clusters = _cluster_articles(relevant)

    # 5. Rank clusters using coverage breadth + trending relevance
    ranked_clusters = _rank_clusters(clusters, trending)

    # 5b. Deduplicate top clusters so two angles on the same story
    # don't both appear (e.g., "Tariff hikes" and "Market fallout from tariffs")
    top_clusters = _deduplicate_top_clusters(ranked_clusters, MAX_ISSUES)

    # 6. Generate analysis for each via LLM
    from app.pipeline.analyze.ollama_client import call_llm, extract_json

    issues_created = 0
    created_ranks: set[int] = set()
    # (title, embedding) pairs for post-LLM dedup within a single run
    generated_title_embs: list[tuple[str, "np.ndarray"]] = []

    for rank, cluster in enumerate(top_clusters, start=1):
        user_prompt = _build_llm_prompt(cluster)
        seen_sources: dict[str, str] = {}
        for a in cluster:
            if a.source_name not in seen_sources:
                seen_sources[a.source_name] = a.url
        source_names = list(seen_sources.keys())
        source_urls = list(seen_sources.values())

        llm_result = call_llm(
            prompt_version=ACTION_CENTER_PROMPT_VERSION,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            cache_key={"date": today, "rank": rank, "titles": [a.title for a in cluster[:5]]},
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

        title = llm_result.get("title", cluster[0].title)
        summary = llm_result.get("summary", "")
        facts = llm_result.get("facts", [])
        actions = llm_result.get("actions", [])
        policy_areas = llm_result.get("policyAreas", [])

        # Post-LLM title dedup: skip if this LLM-generated title is too
        # similar to one already selected this run. Catches cases where two
        # article clusters were distinct enough to pass pre-LLM embedding
        # dedup (threshold 0.50) but the LLM distills them to the same headline.
        title_emb = _embed_texts([title])[0]
        if any(float(title_emb @ prev_emb) >= 0.92 for _, prev_emb in generated_title_embs):
            logger.info(
                "Skipping duplicate issue rank %d (title too similar to earlier issue): '%s'",
                rank, title[:80],
            )
            continue
        generated_title_embs.append((title, title_emb))

        if not isinstance(facts, list):
            facts = []
        if not isinstance(actions, list):
            actions = []
        actions = _normalize_actions(actions, title=title)
        if len(actions) < 3:
            topic = title or cluster[0].title
            defaults = [
                {"text": f"Contact your representative to share your position on {topic}", "type": "contact_representative"},
                {"text": f"Stay informed by reading official documents about this topic", "type": "track_legislation"},
                {"text": f"Voice your opinion at a town hall or public hearing", "type": "attend_hearing"},
            ]
            existing_types = {a["type"] for a in actions}
            for d in defaults:
                if len(actions) >= 3:
                    break
                if d["type"] not in existing_types:
                    actions.append(d)
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

        # 9. Find senators mentioned in or related to this issue
        related_senators = _find_related_senators(title, summary, facts, db)

        # 10. Enrich actions with specific URLs and text
        actions = _enrich_actions(
            actions, title, resolved_bills,
            source_urls, source_names, related_senators,
        )

        issue = ActionIssue(
            date=today,
            rank=rank,
            title=title[:500],
            summary=summary,
            facts=json.dumps(facts),
            actions=json.dumps(actions),
            source_urls=json.dumps(source_urls),
            source_names=json.dumps(source_names),
            policy_areas=json.dumps(policy_areas),
            related_bill_ids=json.dumps(resolved_bills),
            related_explore_ids=json.dumps(related_explore_ids),
            related_senators=json.dumps(related_senators),
        )

        # Upsert: replace existing issue for same date+rank
        existing = (
            db.query(ActionIssue)
            .filter(ActionIssue.date == today, ActionIssue.rank == rank)
            .first()
        )
        if existing:
            title_changed = existing.title != issue.title[:500]
            for attr in ("title", "summary", "facts", "actions", "source_urls",
                         "source_names", "policy_areas", "related_bill_ids",
                         "related_explore_ids", "related_senators"):
                setattr(existing, attr, getattr(issue, attr))
            existing.created_at = datetime.utcnow()
            if title_changed:
                # New story at this rank — treat as fresh for Bluesky posting
                existing.bsky_posted_at = None
                existing.bsky_posted_rank = None
        else:
            db.add(issue)

        created_ranks.add(rank)
        issues_created += 1

    db.commit()

    # Remove stale issues from prior runs at ranks not refreshed this run.
    # This prevents leftover issues when clustering changes between runs
    # or when fewer clusters are produced (e.g., LLM failure).
    if created_ranks:
        stale_deleted = (
            db.query(ActionIssue)
            .filter(
                ActionIssue.date == today,
                ~ActionIssue.rank.in_(created_ranks),
            )
            .delete(synchronize_session="fetch")
        )
        if stale_deleted:
            db.commit()
            logger.info("Cleaned up %d stale issues from prior runs", stale_deleted)

    # Stage 2: Auto-detect and update national monitors (must run before
    # _save_timeline_entry so the top issue has related_monitor_slugs populated)
    if issues_created > 0:
        _update_national_monitors(today, db)

    # Preserve today's #1 issue in the permanent timeline
    if issues_created > 0:
        _save_timeline_entry(today, db)
        generate_period_summaries(today, db)

    # Stage 3: Generate daily visual theme from the top issues
    if issues_created > 0:
        created_issues = (
            db.query(ActionIssue)
            .filter(ActionIssue.date == today)
            .order_by(ActionIssue.rank)
            .all()
        )
        theme = _generate_daily_theme(created_issues, today, db)
        if theme:
            existing_theme = db.query(DailyTheme).filter(DailyTheme.date == today).first()
            if existing_theme:
                existing_theme.theme_json = json.dumps(theme)
                existing_theme.created_at = datetime.utcnow()
            else:
                db.add(DailyTheme(date=today, theme_json=json.dumps(theme)))
            db.commit()

    # Stage 4: Generate full stories for issues that don't have one yet
    if issues_created > 0:
        story_issues = (
            db.query(ActionIssue)
            .filter(ActionIssue.date == today, ActionIssue.full_story.is_(None))
            .order_by(ActionIssue.rank)
            .all()
        )
        for issue in story_issues:
            try:
                story = _generate_full_story(issue)
                if story:
                    issue.full_story = story
                    db.commit()
            except Exception:
                logger.exception("Full story generation failed for issue %s (non-fatal)", issue.id)

    # Stage 5: Post new/surging issues to Bluesky
    if issues_created > 0:
        try:
            from app.pipeline.analyze.bluesky_poster import process_issues_for_bluesky
            today_issues = (
                db.query(ActionIssue)
                .filter(ActionIssue.date == today)
                .order_by(ActionIssue.rank)
                .all()
            )
            process_issues_for_bluesky(today_issues, db)
        except Exception:
            logger.exception("Bluesky posting failed (non-fatal)")

    # Clean up issues older than 14 days
    from datetime import timedelta as _td
    cutoff = (datetime.now(timezone.utc) - _td(days=14)).strftime("%Y-%m-%d")
    deleted = db.query(ActionIssue).filter(ActionIssue.date < cutoff).delete()
    if deleted:
        db.commit()
        logger.info("Cleaned up %d old action issues", deleted)

    elapsed = time.perf_counter() - t0
    logger.info(
        "Action center refresh complete: %d issues created in %.1fs",
        issues_created, elapsed,
    )
    return issues_created
