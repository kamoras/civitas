"""
Senator analyzer — ONE LLM call per senator for all narrative analysis.

Separation of concerns:
  - ALGORITHMIC (no LLM): lobbying match detection, key vote selection
  - LLM (1 call): voting summary, key vote reasoning, PAC identification,
    platform promise analysis

The algorithmic pieces use vector-search pre-matching (donor industry ↔
bill policy area) and party-alignment data that are already computed.
This keeps the LLM prompt small enough for num_ctx=2048 while producing
richer output than the prior 2-call approach.
"""

import logging
import re
from collections import Counter
from typing import Any

from app.config_definitions import PLATFORM_CATEGORIES
from app.pipeline.analyze.ollama_client import call_llm, unwrap_list
from app.pipeline.vector_store import search_bills

logger = logging.getLogger(__name__)

# Industry groups that are related (donor in one ↔ bill in the other)
_INDUSTRY_POLICY_MAP: dict[str, set[str]] = {
    "FINANCE":      {"FINANCIAL", "TAXES"},
    "PHARMA":       {"HEALTHCARE"},
    "INSURANCE":    {"HEALTHCARE"},
    "HEALTHCARE":   {"HEALTHCARE"},
    "OIL_GAS":      {"ENERGY", "ENVIRONMENT"},
    "ENERGY":       {"ENERGY", "ENVIRONMENT"},
    "DEFENSE":      {"DEFENSE"},
    "TECH":         {"TECH"},
    "TELECOM":      {"TECH"},
    "REAL_ESTATE":  {"FINANCIAL", "WELFARE"},
    "GUNS":         {"GUNS", "JUSTICE"},
    "TOBACCO":      {"HEALTHCARE"},
    "AGRIBUSINESS": {"TRADE", "ENVIRONMENT"},
    "TRANSPORT":    {"TRADE"},
    "CONSTRUCTION": {"TRADE"},
    "LAWYERS":      {"JUSTICE"},
    "LOBBYISTS":    set(),
    "GAMBLING":     set(),
    "CRYPTO":       {"FINANCIAL", "TECH"},
    "PRIVATE_PRISON": {"JUSTICE"},
    "LABOR_UNIONS": {"LABOR"},
}


# ── Algorithmic lobbying match detection ─────────────────────────


def detect_lobbying_matches(
    donors: list[dict],
    all_votes: list[dict],
) -> list[dict]:
    """Detect donor-vote industry overlaps algorithmically.

    A match is flagged when a donor's industry maps to the policy area
    of a bill the senator voted on.  Vector search further narrows by
    semantic relevance.  No LLM needed.
    """
    external = [d for d in donors if d.get("type") not in ("CandidateAffiliated", "SKIP")]
    if not external:
        return []

    vote_map = {v["billId"]: v for v in all_votes if v.get("vote") in ("Yea", "Nay")}
    if not vote_map:
        return []

    matches: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    for donor in external[:6]:
        donor_name = donor.get("name", "")
        donor_industry = donor.get("industry", "OTHER")
        if donor_industry in ("OTHER", "POLITICAL", "SMALL_DONORS", "LARGE_INDIVIDUAL"):
            continue

        related_policies = _INDUSTRY_POLICY_MAP.get(donor_industry, set())
        if not related_policies:
            continue

        relevant_bills = search_bills(
            query=f"{donor_name} {donor_industry.replace('_', ' ')} policy legislation",
            n_results=5,
        )

        for bill in relevant_bills:
            bid = bill["billId"]
            if bid not in vote_map:
                continue
            pair_key = (donor_name.upper(), bid)
            if pair_key in seen_pairs:
                continue

            vote = vote_map[bid]
            bill_policy = vote.get("policyArea", "")
            if bill_policy not in related_policies:
                continue

            seen_pairs.add(pair_key)
            matches.append({
                "lobbyistOrg": donor_name,
                "industry": donor_industry,
                "lobbyingSpend": 0,
                "donationToSenator": round(donor.get("total", 0)),
                "billsInfluenced": [bid],
                "senatorVoteAligned": vote["vote"] == "Yea",
                "description": (
                    f"{donor_name} ({donor_industry}) donated "
                    f"${donor.get('total', 0):,.0f}. Senator voted "
                    f"{vote['vote']} on {vote.get('billName', bid)[:60]} "
                    f"({bill_policy})."
                ),
            })

    matches.sort(key=lambda m: m["donationToSenator"], reverse=True)
    return matches[:8]


# ── Algorithmic key vote selection ───────────────────────────────


def select_key_votes(
    all_votes: list[dict],
    donors: list[dict],
    max_keys: int = 7,
) -> list[str]:
    """Select the most notable votes algorithmically.

    Scoring heuristic (higher = more notable):
      +3  voted against party line
      +2  policy area matches a top donor's industry
      +1  non-procedural substantive vote
      +1  recent vote (from current congress)
    """
    external = [d for d in donors if d.get("type") not in ("CandidateAffiliated", "SKIP")]
    donor_policies: set[str] = set()
    for d in external[:8]:
        ind = d.get("industry", "OTHER")
        donor_policies.update(_INDUSTRY_POLICY_MAP.get(ind, set()))

    scored: list[tuple[float, str]] = []
    for v in all_votes:
        if v.get("vote") not in ("Yea", "Nay"):
            continue
        if v.get("policyArea", "PROCEDURAL") == "PROCEDURAL":
            continue

        score = 1.0
        if v.get("votedWithParty") is False:
            score += 3.0
        if v.get("policyArea", "") in donor_policies:
            score += 2.0
        scored.append((score, v["billId"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [bid for _, bid in scored[:max_keys]]


def _extract_platform_topics(platform_text: str, max_topics: int = 6) -> list[str]:
    """Split platform text into distinct topic queries for targeted vector search.

    Rather than searching with the entire blob (which returns the same
    generic bills for every promise), we split into individual topic
    strings so each gets its own relevant bill matches.
    """
    lines = [
        ln.strip().lstrip("•-–—*123456789.)")
        for ln in platform_text.split("\n")
        if ln.strip() and len(ln.strip()) > 15
    ]

    topics = []
    for line in lines:
        cleaned = line.strip()
        if cleaned and cleaned not in topics:
            topics.append(cleaned[:150])
        if len(topics) >= max_topics:
            break

    if not topics and platform_text.strip():
        topics = [platform_text[:200]]

    return topics


# ── Public API ───────────────────────────────────────────────────


async def analyze_senator_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Analyze senators: algorithmic matching + 1 LLM call per senator.

    Args:
        batch: List of dicts with keys:
            senator, donors, keyVotes, allVotes, platformText.
        db_session: SQLAlchemy session for cache access.

    Returns:
        List of result dicts (one per senator in batch).
    """
    results: list[dict] = []

    for item in batch:
        senator = item["senator"]
        donors = item.get("donors", [])
        key_votes = item.get("keyVotes", [])
        all_votes = item.get("allVotes", [])
        platform_text = item.get("platformText", "")

        has_data = len(donors) > 0 or len(key_votes) > 0

        lobbying_matches = detect_lobbying_matches(donors, all_votes) if has_data else []
        key_vote_ids = select_key_votes(all_votes, donors) if has_data else []
        if has_data:
            llm_result = await _narrative_analysis(
                senator=senator,
                donors=donors,
                all_votes=all_votes,
                key_vote_ids=key_vote_ids,
                platform_text=platform_text,
                db_session=db_session,
            )
        else:
            llm_result = {}

        results.append({
            "senatorId": senator["id"],
            "keyVotes": key_votes,
            "lobbyingMatches": lobbying_matches,
            "flipFlopScore": llm_result.get("flipFlopScore", 50),
            "keyVoteIds": key_vote_ids,
            "reasoning": llm_result.get("reasoning", {}),
            "votingSummary": llm_result.get("votingSummary", ""),
            "pacDetails": llm_result.get("pacDetails", []),
            "platformSummary": llm_result.get("platformSummary", ""),
            "campaignPromises": llm_result.get("campaignPromises", []),
        })

    return results


# ── Single LLM call: all narrative analysis ──────────────────────

_CATEGORIES_STR = "|".join(PLATFORM_CATEGORIES.keys())


async def _narrative_analysis(
    senator: dict,
    donors: list[dict],
    all_votes: list[dict],
    key_vote_ids: list[str],
    platform_text: str,
    db_session: Any | None,
) -> dict:
    """One LLM call per senator for all narrative output.

    Produces: votingSummary, reasoning for key votes, key vote
    enrichment (description+stance), PAC identification, and
    platform promise analysis.
    """
    external = [d for d in donors if d.get("type") != "CandidateAffiliated"]
    pac_donors = [d for d in donors if d.get("type") == "PAC" and d.get("total", 0) > 0]

    substantive = [
        v for v in all_votes
        if v.get("vote") in ("Yea", "Nay")
        and v.get("policyArea", "PROCEDURAL") != "PROCEDURAL"
    ]

    # Build compact key votes section (the ones we algorithmically selected)
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

    # Compact donor list
    donor_lines = ", ".join(
        f"{d['name'][:30]}(${d.get('total',0):,.0f},{d.get('industry','?')})"
        for d in external[:5]
    )

    # PAC lines
    pac_lines = "\n".join(
        f"- {d['name']} (${d.get('total', 0):,.0f})"
        for d in pac_donors[:5]
    )

    # Platform section (trimmed to fit ctx)
    platform_section = ""
    platform_votes_text = ""
    if platform_text:
        platform_section = f"\nPLATFORM:\n{platform_text[:1200]}\n"

        # Per-topic vector search: extract distinct platform topics and
        # search for relevant bills individually so the LLM gets diverse,
        # genuinely related votes rather than the same generic matches.
        topic_chunks = _extract_platform_topics(platform_text)
        platform_bill_ids: dict[str, float] = {}
        for topic in topic_chunks:
            hits = search_bills(query=topic, n_results=5)
            for h in hits:
                dist = h.get("distance", 999)
                if dist < 1.2:
                    bid = h["billId"]
                    if bid not in platform_bill_ids or dist < platform_bill_ids[bid]:
                        platform_bill_ids[bid] = dist

        platform_vote_lines = []
        for v in substantive:
            if v["billId"] in platform_bill_ids and len(platform_vote_lines) < 12:
                platform_vote_lines.append(
                    f"{v['billId']}|{v.get('billName','')[:50]}|{v['vote']}|"
                    f"{v.get('policyArea','')}|{v.get('description','')[:60]}"
                )
        if platform_vote_lines:
            platform_votes_text = (
                "\nVOTES RELATED TO PLATFORM:\n"
                + "\n".join(platform_vote_lines)
                + "\n"
            )

    # Build the single prompt
    prompt = (
        f"Senator {senator['name']} ({senator['party']}-{senator['state']}).\n"
        f"DONORS: {donor_lines}\n"
        f"KEY VOTES:\n{key_votes_text}\n"
    )
    if pac_lines:
        prompt += f"PACs:\n{pac_lines}\n"
    prompt += platform_section
    prompt += platform_votes_text

    key_ids_str = ", ".join(f'"{k}"' for k in key_vote_ids[:5])
    prompt += (
        "\nReturn a single flat JSON object. Use actual bill IDs from the data above.\n"
        "{"
        '"votingSummary":"2 plain-English sentences about voting priorities and party independence",'
        f'"reasoning":{{<for each of [{key_ids_str}], billId: 1 sentence why notable>}},'
        '"pacDetails":[{"name":"PAC name","pacSponsor":"parent org or corporation behind the PAC","pacIndustry":"industry","pacAnalysis":"1 sentence: what policy agenda does this PAC advance? Do NOT just say they donated."}]'
    )
    if platform_text:
        prompt += (
            ',"platformSummary":"1 sentence summary of platform",'
            '"campaignPromises":['
            '{"promiseText":"EXACT quote or close paraphrase from PLATFORM above",'
            f'"category":"one of: {_CATEGORIES_STR}",'
            '"relatedBills":["only billIds with GENUINE topical connection"],'
            '"analysis":"1-2 sentences: what specific bill did the senator vote on, '
            'and how does that vote relate to this promise? No filler.",'
            '"alignment":"kept|broken|partial|unclear"}'
            "] (3-5 promises. RULES:\n"
            "1. promiseText MUST be the senator's actual words from PLATFORM — "
            "do NOT invent, rephrase, or combine separate topics.\n"
            "2. Use 'unclear' when no vote genuinely relates.\n"
            "3. Do NOT reuse the same bills across multiple promises.\n"
            "4. 'broken' means the senator VOTED AGAINST the promise. If the "
            "analysis says the senator supports or aligns with the promise, "
            "the alignment MUST be 'kept'.\n"
            "5. Do NOT mention donors, PACs, or the Army unless the platform "
            "specifically discusses them.)"
        )
    prompt += "}"

    result = call_llm(
        prompt_version="senator-unified-v8",
        system_prompt=(
            "You are a rigorous political analyst. Rules:\n"
            "1. NEVER restate obvious facts (e.g. 'X received funding from Y'). "
            "Every sentence must add insight.\n"
            "2. NEVER fabricate or hallucinate. If you don't know, say 'unclear'.\n"
            "3. Only correlate votes to promises when the bill directly relates.\n"
            "4. For PAC analysis: explain what policy agenda the PAC advances, "
            "not that it donated money.\n"
            "5. If the analysis text says 'aligned' or 'supports', the alignment "
            "MUST be 'kept', not 'broken'.\n"
            "Return ONLY valid JSON."
        ),
        user_prompt=prompt,
        cache_key={
            "senatorId": senator["id"],
            "donorCount": len(external),
            "voteCount": len(substantive),
            "keyIds": sorted(key_vote_ids),
            "platformLen": len(platform_text),
            "v": 5,
        },
        db_session=db_session,
        max_tokens=1500,
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

    # Parse PAC details — filter out generic filler
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

    promises = _parse_promises(
        result.get("campaignPromises"), all_votes, platform_text
    )

    return {
        "flipFlopScore": 50,
        "reasoning": reasoning,
        "votingSummary": str(result.get("votingSummary", ""))[:500],
        "pacDetails": pac_details,
        "platformSummary": str(result.get("platformSummary", ""))[:500],
        "campaignPromises": promises,
    }


_KEPT_SIGNALS = re.compile(
    r"(?:align(?:s|ed|ing|ment)|support(?:s|ing|ed)?(?:\s+(?:for|of|this))?"
    r"|consistent(?:\s+with)?|keeping|kept|match(?:es|ing)"
    r"|fulfill(?:s|ed|ing)?|honour|uphold|advance[sd]?"
    r"|further[sd]?|demonstrat(?:es|ing)"
    r"|indicat(?:es|ing)\s+(?:alignment|support|commitment|a\s+focus))",
    re.IGNORECASE,
)
_BROKEN_SIGNALS = re.compile(
    r"(?:contradict(?:s|ing|ed)|(?:voted|votes)\s+against"
    r"|oppos(?:es|ing|ed)|undermin(?:es|ing|ed)"
    r"|broke[n]?|fail(?:s|ed|ing)|violat(?:es|ing|ed)"
    r"|inconsistent|(?:is|are|was|were|not)\s+not\s+(?:aligned|consistent|support)"
    r"|does not (?:support|align|match))",
    re.IGNORECASE,
)

_WEAK_ANALYSIS = re.compile(
    r"which is (?:not )?(?:aligned with|related to) (?:his|her|their) "
    r"(?:stated )?platform",
    re.IGNORECASE,
)

_DONOR_AS_EVIDENCE = re.compile(
    r"(?:support for (?:the )?.*?PAC\b|donations? (?:from|to)|"
    r"contribut(?:ions?|ed) (?:from|to)|(?:PAC|donor|funding)\s+"
    r"(?:indicates?|suggests?|shows?))",
    re.IGNORECASE,
)

_FILLER_ANALYSIS = re.compile(
    r"(?:has received funding from|(?:^|[,.])\s*a political PAC"
    r"|opposes the removal of the United States Army"
    r"|which is (?:not )?(?:aligned with|related to) (?:his|her|their) (?:platform|stance|stated))",
    re.IGNORECASE,
)


def _infer_alignment_from_analysis(analysis: str, llm_label: str) -> str:
    """Derive alignment from the analysis text rather than trusting the LLM label.

    Small models write correct analysis but often pick the wrong label.
    We score the analysis text for kept/broken signals and override the
    LLM label when the evidence clearly points the other way.

    Also detects:
    - Generic filler ("which is related to his platform on X")
    - Donor-as-evidence (cites PAC/donor instead of a vote)
    Both are downgraded to "unclear" since they aren't vote-based evidence.
    """
    if not analysis:
        return llm_label if llm_label in ("kept", "broken", "partial", "unclear") else "unclear"

    if _WEAK_ANALYSIS.search(analysis):
        return "unclear"

    if _DONOR_AS_EVIDENCE.search(analysis):
        return "unclear"

    kept_hits = len(_KEPT_SIGNALS.findall(analysis))
    broken_hits = len(_BROKEN_SIGNALS.findall(analysis))

    if kept_hits > 0 and broken_hits > 0:
        # Contradictory signals in one promise's analysis means the LLM
        # is confused, not that the promise is genuinely partial.
        return "unclear"
    if kept_hits > 0:
        return "kept"
    if broken_hits > 0:
        return "broken"

    return llm_label if llm_label in ("kept", "broken", "partial", "unclear") else "unclear"


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "that", "this", "these", "those",
    "it", "its", "not", "no", "nor", "so", "if", "as", "all", "any",
    "our", "his", "her", "their", "my", "your", "who", "whom", "which",
    "what", "where", "when", "how", "about", "into", "through", "more",
    "most", "very", "also", "just", "than", "then", "can", "over", "such",
    "united", "states", "senator", "america", "american",
})


def _platform_word_overlap(promise_text: str, platform_text: str) -> float:
    """Fraction of significant words in promise_text found in platform_text.

    Returns 0.0-1.0.  Low overlap suggests the LLM hallucinated the promise.
    """
    if not platform_text:
        return 1.0  # no platform to check against

    def _significant_words(text: str) -> set[str]:
        return {
            w for w in re.findall(r"[a-z]{4,}", text.lower())
            if w not in _STOPWORDS
        }

    promise_words = _significant_words(promise_text)
    if len(promise_words) < 2:
        return 1.0  # too short to judge

    platform_words = _significant_words(platform_text)
    if not platform_words:
        return 1.0

    overlap = promise_words & platform_words
    return len(overlap) / len(promise_words)


def _parse_promises(
    promises_raw: Any,
    all_votes: list[dict] | None = None,
    platform_text: str = "",
) -> list[dict]:
    """Parse and validate campaign promises from LLM output.

    Detects and downgrades:
    - Hallucinated promises (low word overlap with actual platform text)
    - Lazy output where the same bills are force-fit to every promise
    """
    if not promises_raw or not isinstance(promises_raw, list):
        return []

    valid_alignments = {"kept", "broken", "partial", "unclear"}
    valid_categories = set(PLATFORM_CATEGORIES.keys())
    valid_bill_ids = {v["billId"] for v in (all_votes or [])}
    promises = []

    for p in promises_raw:
        if not isinstance(p, dict) or not p.get("promiseText"):
            continue

        promise_text = str(p.get("promiseText", ""))[:250]

        category_raw = p.get("category", "other")
        if isinstance(category_raw, list):
            category_raw = category_raw[0] if category_raw else "other"
        category = category_raw if category_raw in valid_categories else "other"

        alignment_raw = p.get("alignment", "unclear")
        if isinstance(alignment_raw, list):
            alignment_raw = alignment_raw[0] if alignment_raw else "unclear"
        llm_label = str(alignment_raw).lower().strip()
        if llm_label not in valid_alignments:
            for candidate in valid_alignments - {"unclear"}:
                if candidate in llm_label:
                    llm_label = candidate
                    break
            else:
                llm_label = "unclear"

        analysis = str(p.get("analysis", ""))[:300]

        # Check if the promise text is actually grounded in the platform
        overlap = _platform_word_overlap(promise_text, platform_text)
        if overlap < 0.3:
            logger.warning(
                "Hallucinated promise (%.0f%% overlap): %s",
                overlap * 100, promise_text[:80],
            )
            alignment = "unclear"
            analysis = (
                "This promise could not be verified against the senator's "
                "actual platform text."
            )
            related_bills: list[str] = []
        else:
            alignment = _infer_alignment_from_analysis(analysis, llm_label)

            related_bills = p.get("relatedBills", [])
            if not isinstance(related_bills, list):
                related_bills = []
            related_bills = [
                str(b) for b in related_bills
                if b and (not valid_bill_ids or str(b) in valid_bill_ids)
            ][:5]

        promises.append({
            "promiseText": promise_text,
            "category": category,
            "alignment": alignment,
            "relatedVotes": related_bills,
            "analysis": analysis,
        })

    # Detect lazy output: if 2+ promises share the exact same bill set,
    # the LLM is force-fitting generic votes to every promise.
    if len(promises) >= 2:
        bill_sets = [tuple(sorted(p["relatedVotes"])) for p in promises]
        counts = Counter(bill_sets)
        overused = {bs for bs, cnt in counts.items() if cnt >= 2 and bs}
        if overused:
            logger.warning(
                "Detected %d promise(s) sharing identical bill sets — downgrading",
                sum(counts[bs] for bs in overused),
            )
            for p in promises:
                if tuple(sorted(p["relatedVotes"])) in overused:
                    p["alignment"] = "unclear"
                    p["relatedVotes"] = []
                    p["analysis"] = (
                        "No votes directly related to this promise were found "
                        "in the current legislative record."
                    )

    # Filter out filler analysis text
    for p in promises:
        analysis = p["analysis"]
        if _FILLER_ANALYSIS.search(analysis):
            p["analysis"] = ""

    return promises
