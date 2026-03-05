"""
Senator analyzer — embedding-based classification + LLM narrative summary.

Architecture:
  - CLASSIFICATION (embedding-based, deterministic):
    - Lobbying match detection via donor↔vote embedding similarity
    - Key vote selection via donor↔policy embedding similarity
    - Promise alignment via promise↔vote embedding similarity
  - LLM (1 call per senator, ONLY for narrative):
    - Human-readable voting summary
    - Key vote reasoning (explain pre-computed classifications)
    - PAC analysis narrative
    - Platform summary text

The LLM receives already-classified data and generates presentation text.
It does NOT make classification decisions.
"""

import logging
import re
from collections import Counter
from typing import Any

from app.config_definitions import PLATFORM_CATEGORIES
from app.pipeline.analyze.ollama_client import call_llm, unwrap_list
from app.pipeline.analyze.policy_alignment import (
    compute_promise_vote_alignment,
    detect_donor_vote_connections,
    get_related_policies,
)
from app.pipeline.vector_store import search_bills

logger = logging.getLogger(__name__)


# ── Embedding-based lobbying match detection ─────────────────────


def detect_lobbying_matches(
    donors: list[dict],
    all_votes: list[dict],
) -> list[dict]:
    """Detect donor-vote connections using embedding cosine similarity.

    Uses semantic similarity between donor industry descriptions and
    vote content, replacing the hardcoded _INDUSTRY_POLICY_MAP.
    """
    return detect_donor_vote_connections(donors, all_votes)


# ── Embedding-based key vote selection ───────────────────────────


def select_key_votes(
    all_votes: list[dict],
    donors: list[dict],
    max_keys: int = 7,
) -> list[str]:
    """Select the most notable votes using embedding-derived policy relevance.

    Scoring heuristic (higher = more notable):
      +3  voted against party line
      +2  policy area related to a top donor's industry (via embedding similarity)
      +1  non-procedural substantive vote
    """
    external = [d for d in donors if d.get("type") not in ("CandidateAffiliated", "SKIP")]
    donor_policies: set[str] = set()
    for d in external[:8]:
        ind = d.get("industry", "OTHER")
        if ind in ("OTHER", "POLITICAL", "SMALL_DONORS", "LARGE_INDIVIDUAL"):
            continue
        donor_policies.update(get_related_policies(ind))

    scored: list[tuple[float, str]] = []
    for v in all_votes:
        if v.get("vote") not in ("Yea", "Nay"):
            continue
        if v.get("policyArea", "PROCEDURAL") == "PROCEDURAL":
            continue

        score = 1.0
        if v.get("votedWithParty") is False:
            score += 3.0
        vote_areas = {
            a.get("area") for a in v.get("policyAreas", [])
        } or {v.get("policyArea", "")}
        if vote_areas & donor_policies:
            score += 2.0
        scored.append((score, v["billId"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [bid for _, bid in scored[:max_keys]]


_TOPIC_SKIP_RE = re.compile(
    r"(?:^(?:Home|About|Contact|Press|Media|News|Office|Staff)\b|"
    r"Sign Up|Subscribe|Follow\s|Share\s|Download\s|"
    r"Scheduling Request|How Can|Send \w+ A Message|"
    r"HELP WITH|Flag Request|Schedule a Tour|"
    r"Committee Assignments|Voting Record\b|"
    r"Facebook|Twitter|Instagram|YouTube|Flickr|"
    r"Open [Ww]ebsite [Ss]earch|Logo Link|"
    r"Senator \w+ (?:Facebook|Twitter|Instagram|YouTube)|"
    r"E-?(?:up|mail|news)|Key Is|Learn more about the work|"
    r"Website Search|^\w{1,15}$)",
    re.IGNORECASE,
)

# Heuristic: a line that's mostly capitalized short words is likely nav
_NAV_JUNK_RE = re.compile(
    r"^(?:[A-Z][a-z]{0,12}\s+){4,}$|"
    r"(?:Senator|Rep\.) \w+ (?:Facebook|Instagram|Twitter)|"
    r"^\s*(?:Search|Menu|Close|Open|Toggle)\s",
    re.IGNORECASE,
)


def _extract_platform_topics(platform_text: str, max_topics: int = 6) -> list[str]:
    """Split platform text into distinct topic queries for targeted vector search."""
    lines = [
        ln.strip().lstrip("•-–—*123456789.)")
        for ln in platform_text.split("\n")
        if ln.strip() and len(ln.strip()) > 20
    ]

    topics = []
    for line in lines:
        cleaned = line.strip()
        if not cleaned or cleaned in topics:
            continue
        if len(cleaned.split()) < 4:
            continue
        if _TOPIC_SKIP_RE.search(cleaned):
            continue
        if _NAV_JUNK_RE.search(cleaned):
            continue
        if _ERROR_PAGE_SIGS.search(cleaned):
            continue
        topics.append(cleaned[:150])
        if len(topics) >= max_topics:
            break

    return topics


# ── Public API ───────────────────────────────────────────────────


def precompute_senator_analysis(item: dict) -> dict:
    """Pre-compute all embedding-based analysis for a senator.

    Implements the "Librarian" half of the producer-consumer pipeline:
    runs lobbying detection, key vote selection, and promise alignment
    using only the embedding model (zero LLM calls). This can run in a
    background thread while the LLM processes the previous senator,
    eliminating idle time between LLM calls.

    On a Pi 5, embedding ops take ~2-4s per senator vs ~15-30s for the
    LLM call. By overlapping them, the LLM never sits idle waiting for
    embedding results.
    """
    donors = item.get("donors", [])
    all_votes = item.get("allVotes", [])
    platform_text = item.get("platformText", "")
    sponsored_bills = item.get("sponsoredBills", [])

    has_data = len(donors) > 0 or len(all_votes) > 0

    lobbying_matches = detect_lobbying_matches(donors, all_votes) if has_data else []
    key_vote_ids = select_key_votes(all_votes, donors) if has_data else []
    computed_promises = _compute_promise_alignments(
        platform_text, all_votes, sponsored_bills=sponsored_bills,
    )

    platform_topics: list[str] = []
    if platform_text and not _ERROR_PAGE_SIGS.search(platform_text):
        platform_topics = _extract_platform_topics(platform_text, max_topics=4)

    return {
        "lobbyingMatches": lobbying_matches,
        "keyVoteIds": key_vote_ids,
        "computedPromises": computed_promises,
        "platformTopics": platform_topics,
    }


async def analyze_senator_batch(
    batch: list[dict],
    db_session: Any | None = None,
    precomputed: dict | None = None,
) -> list[dict]:
    """Analyze senators: embedding classification + LLM narrative summary.

    When precomputed data is provided (from precompute_senator_analysis),
    skips the embedding work and goes straight to the LLM narrative call.
    This is the "Analyst" half of the producer-consumer pipeline.

    Classification (deterministic, embedding-based):
      - Lobbying matches via donor↔vote similarity
      - Key vote selection via donor↔policy similarity
      - Promise alignment via promise↔vote similarity

    LLM (narrative only):
      - Voting summary, key vote reasoning, PAC narrative, platform summary
    """
    results: list[dict] = []

    for item in batch:
        senator = item["senator"]
        donors = item.get("donors", [])
        key_votes = item.get("keyVotes", [])
        all_votes = item.get("allVotes", [])
        platform_text = item.get("platformText", "")
        sponsored_bills = item.get("sponsoredBills", [])

        has_data = len(donors) > 0 or len(key_votes) > 0

        if precomputed:
            lobbying_matches = precomputed["lobbyingMatches"]
            key_vote_ids = precomputed["keyVoteIds"]
            computed_promises = precomputed["computedPromises"]
            platform_topics = precomputed.get("platformTopics", [])
        else:
            lobbying_matches = detect_lobbying_matches(donors, all_votes) if has_data else []
            key_vote_ids = select_key_votes(all_votes, donors) if has_data else []
            computed_promises = _compute_promise_alignments(
                platform_text, all_votes, sponsored_bills=sponsored_bills,
            )
            platform_topics = []

        if has_data:
            llm_result = await _narrative_analysis(
                senator=senator,
                donors=donors,
                all_votes=all_votes,
                key_vote_ids=key_vote_ids,
                platform_text=platform_text,
                computed_promises=computed_promises,
                db_session=db_session,
                platform_topics=platform_topics,
            )
        else:
            llm_result = {}

        final_promises = computed_promises if computed_promises else llm_result.get("campaignPromises", [])

        results.append({
            "senatorId": senator["id"],
            "keyVotes": key_votes,
            "lobbyingMatches": lobbying_matches,
            "keyVoteIds": key_vote_ids,
            "reasoning": llm_result.get("reasoning", {}),
            "votingSummary": llm_result.get("votingSummary", ""),
            "pacDetails": llm_result.get("pacDetails", []),
            "platformSummary": llm_result.get("platformSummary", ""),
            "campaignPromises": final_promises,
        })

    return results


def _positions_from_sponsored_bills(
    sponsored_bills: list[dict],
    all_votes: list[dict],
    max_positions: int = 6,
) -> list[dict]:
    """Derive policy positions from bills the senator has sponsored.

    Sponsored legislation is the most reliable signal of a senator's
    priorities — they invested political capital to introduce these bills.
    We match each bill against the voting record to see whether peers
    supported the same policy area.
    """
    if not sponsored_bills:
        return []

    valid_categories = set(PLATFORM_CATEGORIES.keys())
    from app.pipeline.analyze.party_platform import classify_party_alignment

    # Filter to substantive bills (skip resolutions naming buildings, etc.)
    substantive = [
        b for b in sponsored_bills
        if b.get("title")
        and b.get("policyArea", "").upper() not in ("PROCEDURAL", "")
        and len(b.get("title", "")) > 15
    ]
    if not substantive:
        return []

    # Deduplicate by policy area — take the most recent bill per area
    seen_areas: dict[str, dict] = {}
    for bill in substantive:
        area = bill.get("policyArea", "OTHER")
        if area not in seen_areas:
            seen_areas[area] = bill

    positions = []
    for bill in list(seen_areas.values())[:max_positions]:
        title = bill["title"]
        topic_text = title

        result = compute_promise_vote_alignment(topic_text, all_votes)
        category = _classify_promise_category(topic_text, valid_categories)
        party_align = classify_party_alignment(
            topic_text[:300], category.upper(), "pro",
        )

        positions.append({
            "promiseText": title[:250],
            "category": category,
            "alignment": result["alignment"],
            "relatedVotes": result["relatedVotes"],
            "analysis": result["reasoning"],
            "confidence": result["confidence"],
            "partyAlignment": party_align,
        })

    return positions


def _positions_from_platform_text(
    platform_text: str,
    all_votes: list[dict],
    existing_topics: set[str],
    max_positions: int = 4,
) -> list[dict]:
    """Extract supplementary positions from scraped platform text.

    Only used when the text passes strict quality checks.  Topics that
    are semantically redundant with already-found positions (from
    sponsored bills) are skipped.
    """
    if not platform_text:
        return []

    if _ERROR_PAGE_SIGS.search(platform_text):
        return []

    if _SCRAPE_ARTIFACT_SIGS.search(platform_text[:500]):
        platform_text = _SCRAPE_ARTIFACT_SIGS.sub("", platform_text).strip()
        if len(platform_text) < 200:
            return []

    topics = _extract_platform_topics(platform_text, max_topics=max_positions + 2)
    if not topics:
        return []

    valid_categories = set(PLATFORM_CATEGORIES.keys())
    from app.pipeline.analyze.party_platform import classify_party_alignment
    from app.pipeline.analyze.policy_alignment import _embed, _embed_batch

    # Deduplicate against existing positions from sponsored bills
    if existing_topics:
        existing_embs = _embed_batch(list(existing_topics))
        filtered = []
        for t in topics:
            t_emb = _embed(t[:200])
            if existing_embs.size > 0:
                sims = existing_embs @ t_emb
                if float(sims.max()) > 0.65:
                    continue
            filtered.append(t)
        topics = filtered

    promises = []
    for topic in topics[:max_positions]:
        result = compute_promise_vote_alignment(topic, all_votes)
        category = _classify_promise_category(topic, valid_categories)
        party_align = classify_party_alignment(
            topic[:300], category.upper(), "pro",
        )
        promises.append({
            "promiseText": topic[:250],
            "category": category,
            "alignment": result["alignment"],
            "relatedVotes": result["relatedVotes"],
            "analysis": result["reasoning"],
            "confidence": result["confidence"],
            "partyAlignment": party_align,
        })

    return promises


def _compute_promise_alignments(
    platform_text: str,
    all_votes: list[dict],
    sponsored_bills: list[dict] | None = None,
) -> list[dict]:
    """Compute promise-vote alignment deterministically using embeddings.

    Primary source: sponsored bills (structured, reliable).
    Supplementary: scraped platform text (when quality passes filters).

    For each position, finds semantically related votes and determines
    alignment based on vote direction — no LLM opinion needed.
    """
    positions = _positions_from_sponsored_bills(
        sponsored_bills or [], all_votes,
    )

    existing_topics = {p["promiseText"] for p in positions}
    platform_positions = _positions_from_platform_text(
        platform_text, all_votes, existing_topics,
    )
    positions.extend(platform_positions)

    return positions


def _classify_promise_category(text: str, valid_categories: set[str]) -> str:
    """Classify a promise into a platform category using embedding similarity."""
    from app.pipeline.analyze.policy_alignment import _embed, _embed_batch

    text_emb = _embed(text[:200])
    cat_texts = list(valid_categories)
    cat_embs = _embed_batch(cat_texts)
    if cat_embs.size == 0:
        return "other"

    import numpy as np
    sims = cat_embs @ text_emb
    best_idx = int(np.argmax(sims))
    if float(sims[best_idx]) > 0.25:
        return cat_texts[best_idx]
    return "other"


# ── Single LLM call: all narrative analysis ──────────────────────

_CATEGORIES_STR = "|".join(PLATFORM_CATEGORIES.keys())


async def _narrative_analysis(
    senator: dict,
    donors: list[dict],
    all_votes: list[dict],
    key_vote_ids: list[str],
    platform_text: str,
    computed_promises: list[dict] | None = None,
    db_session: Any | None = None,
    platform_topics: list[str] | None = None,
) -> dict:
    """One LLM call per senator for NARRATIVE ONLY.

    The LLM receives pre-classified data and generates human-readable
    summaries. It does NOT make classification decisions — those are
    computed deterministically by the embedding-based alignment engine.

    Produces: votingSummary, reasoning for key votes, PAC analysis narrative,
    platform summary text.
    """
    external = [d for d in donors if d.get("type") != "CandidateAffiliated"]
    pac_donors = [d for d in donors if d.get("type") == "PAC" and d.get("total", 0) > 0]

    substantive = [
        v for v in all_votes
        if v.get("vote") in ("Yea", "Nay")
        and v.get("policyArea", "PROCEDURAL") != "PROCEDURAL"
    ]

    key_set = set(key_vote_ids)
    key_vote_lines = []
    for v in substantive:
        if v["billId"] not in key_set:
            continue
        party = ""
        if v.get("votedWithParty") is False:
            party = " [AGAINST PARTY]"
        key_vote_lines.append(
            f"{v['billId']} | {v.get('billName', '')[:50]} | "
            f"{v['vote']} | {v.get('policyArea', '')} — "
            f"{v.get('description', '')[:60]}{party}"
        )
    key_votes_text = "\n".join(key_vote_lines) or "None"

    donor_lines = ", ".join(
        f"{d['name'][:30]}(${d.get('total',0):,.0f},{d.get('industry','?')})"
        for d in external[:5]
    )

    pac_lines = "\n".join(
        f"- {d['name']} (${d.get('total', 0):,.0f})"
        for d in pac_donors[:5]
    )

    # Build the narrative-only prompt
    prompt = (
        f"Senator {senator['name']} ({senator['party']}-{senator['state']}).\n"
        f"DONORS: {donor_lines}\n"
        f"KEY VOTES:\n{key_votes_text}\n"
    )
    if pac_lines:
        prompt += f"PACs:\n{pac_lines}\n"
    if platform_topics:
        prompt += "\nPLATFORM PRIORITIES:\n" + "\n".join(f"- {t}" for t in platform_topics[:4]) + "\n"
    elif platform_text and not _ERROR_PAGE_SIGS.search(platform_text):
        prompt += f"\nPLATFORM:\n{platform_text[:800]}\n"

    key_ids_str = ", ".join(f'"{k}"' for k in key_vote_ids[:5])
    prompt += (
        "\nReturn a single flat JSON object. Use actual bill IDs from the data above.\n"
        "{"
        '"votingSummary":"2 plain-English sentences about voting priorities and party independence",'
        f'"reasoning":{{<for each of [{key_ids_str}], billId: 1 sentence why notable>}},'
        '"pacDetails":[{{"name":"PAC name","pacSponsor":"parent org or corporation behind the PAC",'
        '"pacIndustry":"industry","pacAnalysis":"1 sentence: what policy agenda does this PAC advance?"}}]'
    )
    if platform_topics or (platform_text and not _ERROR_PAGE_SIGS.search(platform_text)):
        prompt += ',"platformSummary":"1 sentence summary of platform"'
    prompt += "}"

    result = call_llm(
        prompt_version="senator-narrative-v10",
        system_prompt=(
            "You are a factual political analyst producing NARRATIVE SUMMARIES only. Rules:\n"
            "1. NEVER make classification decisions — those are pre-computed.\n"
            "2. NEVER restate obvious facts (e.g. 'X received funding from Y').\n"
            "3. NEVER fabricate or hallucinate. If you don't know, say 'unclear'.\n"
            "4. For PAC analysis: explain what policy agenda the PAC advances.\n"
            "5. Be factual, non-partisan, and concise.\n"
            "Return ONLY valid JSON."
        ),
        user_prompt=prompt,
        cache_key={
            "senatorId": senator["id"],
            "donorCount": len(external),
            "voteCount": len(substantive),
            "keyIds": sorted(key_vote_ids),
            "platformLen": len(platform_text),
            "v": 10,
        },
        db_session=db_session,
        max_tokens=1200,
        num_ctx=4096,
    )

    if not result or not isinstance(result, dict):
        logger.warning("Narrative analysis failed for %s", senator["name"])
        return {}

    valid_bill_ids = {v["billId"] for v in all_votes}

    reasoning = result.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}
    reasoning = {k: str(v)[:200] for k, v in reasoning.items() if k in valid_bill_ids}

    pac_details = []
    raw_pacs = unwrap_list(result.get("pacDetails")) if result.get("pacDetails") else None
    if raw_pacs is None and isinstance(result.get("pacDetails"), list):
        raw_pacs = result["pacDetails"]
    for item in (raw_pacs or []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        analysis = str(item.get("pacAnalysis", ""))[:300]
        if _FILLER_ANALYSIS.search(analysis):
            analysis = ""
        pac_details.append({
            "name": item["name"],
            "pacSponsor": str(item.get("pacSponsor", ""))[:200],
            "pacIndustry": str(item.get("pacIndustry", ""))[:100],
            "pacAnalysis": analysis,
        })

    return {
        "reasoning": reasoning,
        "votingSummary": str(result.get("votingSummary", ""))[:500],
        "pacDetails": pac_details,
        "platformSummary": str(result.get("platformSummary", ""))[:500],
        "campaignPromises": [],  # handled by embedding engine, not LLM
    }


_FILLER_ANALYSIS = re.compile(
    r"(?:has received funding from|(?:^|[,.])\s*a political PAC"
    r"|opposes the removal of the United States Army"
    r"|which is (?:not )?(?:aligned with|related to) (?:his|her|their) (?:platform|stance|stated))",
    re.IGNORECASE,
)


_ERROR_PAGE_SIGS = re.compile(
    r"(?:404\s*error|page\s*not\s*found|page\s*requested|"
    r"search\s+senate\.gov|e-?mail\s+webmaster|broken\s+link"
    r"|this\s+page\s+doesn.t\s+exist|page\s+does\s+not\s+exist)",
    re.IGNORECASE,
)

_SCRAPE_ARTIFACT_SIGS = re.compile(
    r"(?:Skip to (?:primary navigation|main content|content)|"
    r"× Close Mobile Nav|How Can \w+ Help|"
    r"Send \w+ A Message|Scheduling Requests|"
    r"HELP WITH A FEDERAL AGENCY|Schedule a Tour|"
    r"Flag Request|Appropriations & CDS|"
    r"Toggle (?:navigation|submenu)|Menu Menu|"
    r"your? browser does not support|twitter feed|"
    r"javascript|cookie\s+(?:policy|preferences)|"
    r"Contact\s+(?:Form|Us|Senator)|Newsletter\s+Sign)",
    re.IGNORECASE,
)
