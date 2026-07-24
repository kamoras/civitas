"""
Bill analyzer — adaptive embedding-based classification (zero LLM calls).

Uses retrieval-augmented few-shot learning: each pipeline run builds a
reference corpus of classified bills in ChromaDB. Subsequent runs classify
new bills by kNN against that corpus, with embedding similarity against
seed policy descriptions as a cold-start fallback.

Topic/policy-area classification tiers (in priority order):
  1. Reference corpus kNN (most accurate, uses accumulated examples)
  2. Embedding similarity against policy seed descriptions (cold-start)
  3. Augmented re-embed for low-confidence cases

No hardcoded keyword lists for topic classification. The system adapts as
it processes more data. (Stance DIRECTION — pro/anti/neutral, a separate
concern from topic — is handled by derive_stance() below, which does use a
small, disclosed keyword tier; see its docstring.)

Academic rationale
------------------
Bill classification follows the standard text classification pipeline
reviewed in Grimmer & Stewart (2013, "Text as Data: The Promise and
Pitfalls of Automatic Content Analysis Methods for Political Texts,"
Political Analysis 21:3): documents are represented as dense vectors
via sentence-transformers (Reimers & Gurevych 2019, Sentence-BERT)
and classified by cosine similarity to category prototypes.

Policy area taxonomy is based on the Congressional Research Service
(CRS) policy area scheme used by Congress.gov, which organizes
legislation into standardized subject categories. Our 15-category
taxonomy maps to the top-level CRS areas with granularity calibrated
to the embedding model's discriminative resolution (validated against
118th Congress bills with known CRS labels).

Stance derivation (pro/anti/neutral) uses action-verb patterns
following the coding scheme in the Comparative Agendas Project
(Baumgartner & Jones 1993, "Agendas and Instability in American
Politics"), where legislative direction is inferred from verbs like
"expand," "restrict," "repeal," and "establish."

Party alignment is determined by content analysis against party
platform embeddings (see party_platform.py), grounded in the manifesto
analysis literature (Laver, Benoit & Garry 2003; Budge et al. 2001).
Vote tallies serve as a secondary refinement signal, not the primary
determinant — addressing the strategic-voting confound identified in
roll-call-based measures (Clinton, Jackman & Rivers 2004).

References
----------
- Grimmer, J. & Stewart, B. (2013). Text as Data. Political
  Analysis, 21(3), 267-297.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. EMNLP 2019.
- Baumgartner, F. & Jones, B. (1993). Agendas and Instability in
  American Politics. U Chicago Press.
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy
  Positions from Political Texts. APSR, 97(2).
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical
  Analysis of Roll Call Data. APSR, 98(2).
"""

import logging
import re
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

POLICY_TAXONOMY = {
    "LABOR": (
        "Labor unions, workers' rights, employment, wages, and collective bargaining. "
        "Includes minimum wage, overtime protections, NLRB, workforce safety, "
        "paid family leave, and right-to-work legislation."
    ),
    "DEFENSE": (
        "U.S. military, armed forces, national security, and veterans. "
        "Includes the National Defense Authorization Act (NDAA), Pentagon budget, "
        "troop deployments, military weapons systems procurement, defense "
        "contracts, VA benefits, and military base funding."
    ),
    "FOREIGN_POLICY": (
        "International relations, foreign aid, diplomacy, and global conflicts. "
        "Includes treaties, humanitarian aid, diplomatic sanctions, United Nations, "
        "and relations with specific foreign countries or regions (e.g. Russia, "
        "China, Ukraine, Middle East, Iran)."
    ),
    "GUNS": (
        "Domestic firearms regulation, gun control, gun rights, and the Second "
        "Amendment. Includes background checks for gun purchases, assault "
        "weapons bans, ammunition regulations, concealed carry permits and "
        "reciprocity, constitutional carry, red flag laws, firearm dealer "
        "licensing, safe storage requirements, suppressor and pistol-brace "
        "rules, gun-owner rights protections, school shootings, mass shooting "
        "response, and gun violence prevention legislation."
    ),
    "HEALTHCARE": (
        "U.S. healthcare system, medical insurance, hospitals, Medicare, and Medicaid. "
        "Includes the Affordable Care Act, prescription drug prices, public health, "
        "mental health, opioid crisis, and health system regulation."
    ),
    "ENVIRONMENT": (
        "Environment, climate change, pollution, EPA, and conservation. "
        "Includes clean air and water regulations, emissions standards, "
        "endangered species, national parks, and environmental justice."
    ),
    "TAXES": (
        "Taxes, federal budget, and government spending appropriations. "
        "Includes tax reform, IRS, deductions, credits, corporate tax, "
        "continuing resolutions, omnibus spending bills, government funding, "
        "debt ceiling, and fiscal policy."
    ),
    "IMMIGRATION": (
        "U.S. immigration, border security, asylum, and citizenship. "
        "Includes visa policy, DACA, deportation, refugee resettlement, "
        "border wall funding, and immigration courts."
    ),
    "EDUCATION": (
        "Education, schools, universities, and student loans. "
        "Includes Pell grants, Title I, STEM funding, teacher pay, "
        "school choice, and higher education access."
    ),
    "FINANCIAL": (
        "U.S. financial regulation, banking oversight, and consumer protection. "
        "Includes Wall Street reform, SEC, Dodd-Frank, CFPB, "
        "cryptocurrency regulation, and banking compliance."
    ),
    "ENERGY": (
        "Energy production, utilities, and power grid. "
        "Includes renewable energy, solar, wind, nuclear, fossil fuels, "
        "pipeline construction, drilling permits, electricity grid modernization, "
        "and energy subsidies."
    ),
    "TECH": (
        "Technology, internet, data privacy, and cybersecurity. "
        "Includes artificial intelligence regulation, social media oversight, "
        "antitrust for big tech, surveillance, and broadband access."
    ),
    "JUSTICE": (
        "Criminal justice, law enforcement, courts, and civil rights. "
        "Includes police reform, sentencing reform, prison conditions, "
        "bail reform, executive authority, national emergencies, "
        "District of Columbia governance, and constitutional powers."
    ),
    "ECONOMY": (
        "Macroeconomic conditions, financial markets, and broad economic policy. "
        "Includes stock market volatility, market selloffs, recession fears, "
        "inflation, consumer prices, cost of living, GDP, Federal Reserve, "
        "interest rates, monetary policy, jobs report, unemployment rate, "
        "economic outlook, and supply chain disruptions."
    ),
    "TRADE": (
        "International trade, tariffs, economic sanctions, and commerce. "
        "Includes import/export policy, USMCA, trade agreements, "
        "trade wars, and economic diplomacy."
    ),
    "ABORTION": (
        "Abortion and reproductive health policy. Includes abortion access "
        "and abortion restrictions, gestational limits, contraception, IVF "
        "and fertility treatment, family planning funding, parental "
        "notification and consent requirements, protections for the unborn, "
        "Hyde Amendment and public funding rules, and conscience protections "
        "for healthcare providers."
    ),
    "WELFARE": (
        "Social safety net, housing assistance, and disaster relief. "
        "Includes SNAP, food assistance, housing, unemployment benefits, "
        "Social Security, retirement, disability, postal service, "
        "infrastructure, FEMA, and disaster assistance."
    ),
    "PROCEDURAL": (
        "Procedural motions with no substantive policy content. "
        "Includes cloture votes, motions to table, motions to proceed, "
        "quorum calls, adjournment, journal reading, naming buildings, "
        "commemorations, and parliamentary procedure."
    ),
}

_PROCEDURAL_PROTOTYPE = (
    "naming building commemorating honoring designating week month "
    "electing member relative to death fixing daily hour authorizing rotunda "
    "technical corrections renaming post office awarding medal tribute memorial "
    "congressional record adjournment quorum call cloture motion to table"
)
_procedural_emb: np.ndarray | None = None

def _augmented_embedding_classify(text: str) -> str:
    """Second-pass embedding classification with augmented context.

    When the first embedding pass is below the confidence threshold,
    this function tries again with a richer query that includes
    contextual framing to help the model distinguish substantive
    legislation from procedural votes.
    """
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    policy_embs = _get_policy_embeddings()

    augmented = f"This legislation concerns: {text}"
    query_emb = model.encode([augmented[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_area = "PROCEDURAL"
    best_score = 0.0

    for area, area_emb in policy_embs.items():
        if area == "PROCEDURAL":
            continue
        score = float(np.dot(query_emb, area_emb))
        if score > best_score:
            best_score = score
            best_area = area

    # 2026-07 fix (O1): was 0.18, below the measured floor for this same
    # augmented-prefix comparison (600 real titles: mean=0.772, p10=0.734,
    # min=0.649) — recalibrated the same way as EMBEDDING_CONFIDENCE_THRESHOLD
    # just above (same corpus, same lack of a clean genuine/noise gap).
    if best_score > 0.72:
        return best_area
    return "PROCEDURAL"


# Each pattern: (keywords, description_template, stance_direction)
# "pro" = bill supports/expands the policy area
# "anti" = bill restricts/opposes the policy area
# "neutral" = directional intent is ambiguous
_STANCE_PROTOTYPES = {
    "pro": (
        "protect strengthen expand extend increase fund invest establish create "
        "mandate require reauthorize support promote enhance authorize appropriation "
        "improve safeguard guarantee ensure provide preserve advance empower "
        "a bill to provide for the expansion and protection of rights and services"
    ),
    "anti": (
        "ban prohibit restrict limit block repeal eliminate remove defund rescind "
        "cut reduce rollback revoke abolish dismantle oppose curtail suspend "
        "withdraw terminate penalize sanction end halt prevent stop "
        "a bill to repeal and restrict regulations and reduce spending"
    ),
    "neutral": (
        "reform modernize update overhaul study review assess examine "
        "amend modify restructure reorganize transition rename designate"
    ),
}
_stance_embs: dict[str, np.ndarray] | None = None

_policy_embeddings: dict[str, np.ndarray] = {}


def clear_bill_embedding_cache() -> None:
    """Clear cached bill/policy embeddings (call between pipeline runs)."""
    _policy_embeddings.clear()


def _get_policy_embeddings() -> dict[str, np.ndarray]:
    """Pre-compute embeddings for each policy area description."""
    if _policy_embeddings:
        return _policy_embeddings

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    for area, description in POLICY_TAXONOMY.items():
        emb = model.encode([description], show_progress_bar=False)[0]
        _policy_embeddings[area] = emb / np.linalg.norm(emb)

    return _policy_embeddings


# ── Policy area classification (embeddings) ──────────────────────


# 2026-07 fix (platform-review O1): this sat at 0.25, far below the
# model's real similarity floor for this comparison. Live-measured
# (600 real bill titles vs the 16 non-procedural POLICY_TAXONOMY
# prototypes, no prompt_name="query" — matching this function's actual,
# unfixed encoding): top-1 score mean=0.759, p10=0.722, min=0.641. At
# 0.25 the augmented-reembed fallback below was dead code — nothing
# ever scored that low. There is no clean gap between a genuine top-1
# match and the runner-up category here (runner-up mean=0.740, p90=0.772)
# — this floor was never going to separate "right category" from
# "plausible wrong category"; recalibrating it can only restore its
# actual job, catching the small tail of genuinely low-signal bills
# (short/vague titles) and giving them a second, augmented-context pass.
# 0.70 sits just below the measured p10.
EMBEDDING_CONFIDENCE_THRESHOLD = 0.70

# Below this reference-corpus share, a kNN vote against a seed-anchor
# alternative in that category isn't trusted to override it — see
# reference_corpus_label_share's docstring in bill_learning.py. POLICY_
# TAXONOMY has 16 non-procedural categories; a uniform corpus would put
# every one at ~6.3% (1/16), so 3% is roughly "less than half its fair
# share" — a deliberately loose bar that only catches categories that
# are genuinely near-absent (a 2026-07 audit's actual failure cases were
# at 0-0.9% representation).
MIN_SEED_CORPUS_SHARE_FOR_KNN_TRUST = 0.03


def _is_procedural_seed_match(text: str, threshold: float = 0.74) -> tuple[bool, float]:
    """Check if text is procedural via embedding similarity to the procedural prototype.

    Uses cosine similarity (Reimers & Gurevych 2019) instead of keyword
    substring matching. The procedural prototype captures the semantic
    signature of ceremonial/administrative bills.

    Returns (is_match, score) — 2026-07 (O3): used to return just the bool,
    with the caller hardcoding confidence 1.0 for any match. Live-measured
    (600 real bill titles + 6 known-procedural cases): genuinely procedural
    titles score 0.772-0.848, but a real sample of other bills (which
    itself includes plenty of ceremonial day/week-designation resolutions)
    has p90=0.753/max=0.811 against the same prototype — only a thin ~0.02
    gap around this threshold, not the clean separation the 0.75 industry/
    policy gate has. A marginal 0.75 match and a comfortable 0.85 match are
    not equally certain; hardcoding 1.0 for both fed an overconfident label
    into the exact-match learning store (lookup_exact treats it as ground
    truth forever) regardless of which side of that thin gap it landed on.
    """
    global _procedural_emb
    if not text or len(text.strip()) < 5:
        return True, 1.0

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    if _procedural_emb is None:
        emb = model.encode([_PROCEDURAL_PROTOTYPE], show_progress_bar=False)[0]
        _procedural_emb = emb / np.linalg.norm(emb)

    query_emb = model.encode([text[:300]], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    score = float(np.dot(query_emb, _procedural_emb))
    return score >= threshold, score


def classify_policy_area(
    text: str,
    bill_id: str | None = None,
    db_session: Session | None = None,
) -> tuple[str, float]:
    """Classify policy area using adaptive tiered classification.

    Tiers:
      1. Reference corpus kNN (accumulated from prior pipeline runs) —
         only trusted over tier 2 when the corpus has meaningful
         representation of whatever tier 2 would otherwise pick; see
         MIN_SEED_CORPUS_SHARE_FOR_KNN_TRUST.
      2. Embedding similarity against policy seed descriptions
      3. Augmented re-embed for low-confidence cases

    When db_session is provided, results are stored in the learning store
    for future exact-match lookups. The ChromaDB reference corpus is
    populated separately by embed_bills() in the orchestrator.
    """
    if not text or len(text.strip()) < 5:
        return "PROCEDURAL", 0.0

    # Tier 0: exact match from learning store (instant)
    if bill_id and db_session:
        from app.pipeline.analyze.bill_learning import lookup_exact
        exact = lookup_exact(db_session, bill_id)
        if exact:
            return exact  # already a (policy_area, confidence) tuple

    # Seed check for trivially procedural items (cold-start safety net)
    is_procedural, procedural_score = _is_procedural_seed_match(text)
    if is_procedural:
        return "PROCEDURAL", procedural_score

    # Tier 1: kNN against reference corpus (prior classified bills)
    from app.pipeline.analyze.bill_learning import (
        classify_bill_by_reference,
        reference_corpus_label_share,
    )
    ref_area, ref_confidence = classify_bill_by_reference(text)

    # Tier 2: embedding similarity against seed policy descriptions.
    # Computed unconditionally (not just as a tier-1 fallback) because a
    # confident tier-1 vote still needs a candidate to cross-check against
    # — see the corpus-share gate below.
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    policy_embs = _get_policy_embeddings()

    query_emb = model.encode([text[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_area = "PROCEDURAL"
    best_score = 0.0

    for area, area_emb in policy_embs.items():
        if area == "PROCEDURAL":
            continue
        score = float(np.dot(query_emb, area_emb))
        if score > best_score:
            best_score = score
            best_area = area

    if ref_area and ref_area != "PROCEDURAL" and ref_confidence > 0.45:
        # Trust the kNN vote unless it's overriding a seed-anchor pick in
        # a category the reference corpus barely has any examples of.
        # kNN can only ever vote for labels it has seen, so disagreement
        # with a near-absent category isn't real evidence against it —
        # a 2026-07 audit found this exact pattern silently misrouting
        # bills genuinely about near-absent categories (WELFARE, TECH)
        # into whichever common category the corpus happened to be
        # thickest around, even when the seed anchor for the correct
        # category was a strong, unambiguous match.
        if ref_area == best_area or reference_corpus_label_share(best_area) >= MIN_SEED_CORPUS_SHARE_FOR_KNN_TRUST:
            return ref_area, ref_confidence
        # Else: fall through and let tier 2 (below) decide instead.

    # If reference corpus suggested PROCEDURAL but seed embedding disagrees,
    # trust the embedding (reference corpus may have bad labels from prior runs).
    # 2026-07 (O1): reuses EMBEDDING_CONFIDENCE_THRESHOLD rather than its own
    # independent magic number (was 0.20, also dead) — "confident enough to
    # override a PROCEDURAL vote" is the same bar as "confident enough to
    # accept outright" just below.
    if ref_area == "PROCEDURAL" and best_area != "PROCEDURAL" and best_score > EMBEDDING_CONFIDENCE_THRESHOLD:
        return best_area, best_score

    if best_score < EMBEDDING_CONFIDENCE_THRESHOLD:
        augmented_area = _augmented_embedding_classify(text)
        return augmented_area, best_score

    return best_area, best_score


def classify_policy_areas_multi(
    text: str,
    bill_id: str | None = None,
    db_session: Session | None = None,
) -> list[dict]:
    """Classify policy area(s) for a bill using embedding similarity.

    Real legislation often addresses more than one policy dimension (the
    Comparative Agendas Project — Baumgartner & Jones 1993, 2002 — codes
    bills with both a primary and secondary topic), and this used to try
    to detect secondary areas by requiring a candidate's cosine
    similarity to be within a gap ratio of the primary area's. A 2026-07
    audit measured that gap across 60 real texts and found it doesn't
    exist to detect: median gap between the top-scoring and 2nd-place
    category was 0.018 (p90 0.053) — every category anchor clusters
    within a few hundredths of each other for almost any input,
    regardless of genuine relevance. Real examples: "Murder Trial of
    Alex Murdaugh Resumes" scored JUSTICE, TECH, GUNS, LABOR, IMMIGRATION,
    and DEFENSE all within 0.05 of each other; "KPMG's Self-Destruction"
    (an accounting story) scored TECH secondary while never surfacing
    FINANCIAL confidently enough to note it as unusual. No threshold
    value can separate genuine secondary relevance from this noise
    floor, because both live in the same narrow band — this is the same
    noise-floor phenomenon policy_alignment.py already documents for
    this embedding model (measured ~0.55-0.87 for genuinely unrelated
    text), just worse here because the candidate pool is a fixed set of
    16 broad category paragraphs rather than real bill/vote text.

    Returns a single-element list (same shape callers already expect,
    including the len(areas) > 1 "multi-area" checks, which now always
    evaluate false) so no caller needed to change. Kept as a function
    rather than inlined at call sites in case a genuinely discriminating
    secondary-area signal — e.g. requiring corroboration from the kNN
    reference corpus rather than raw anchor-paragraph similarity —
    replaces this later.
    """
    area, confidence = classify_policy_area(text, bill_id=bill_id, db_session=db_session)
    return [{"area": area, "confidence": confidence}]


# ── Stance derivation (embedding-based) ──────────────────────────


def _get_stance_embeddings() -> dict[str, np.ndarray]:
    """Cache and return stance prototype embeddings."""
    global _stance_embs
    if _stance_embs is not None:
        return _stance_embs

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    _stance_embs = {}
    for direction, proto in _STANCE_PROTOTYPES.items():
        emb = model.encode([proto], show_progress_bar=False)[0]
        _stance_embs[direction] = emb / np.linalg.norm(emb)
    return _stance_embs


def derive_stance(bill_name: str, summary: str, policy_area: str) -> tuple[str, str]:
    """Derive a brief stance description and direction from bill name and summary.

    Primarily embedding cosine similarity against stance direction
    prototypes (pro/anti/neutral) — but disclosed exception: a small tier-0
    keyword check runs first (see below) as a precision fix for a measured
    embedding-model weakness on short phrases. It never classifies alone;
    it only breaks ties/lowers the acceptance margin when the embedding
    result is already ambiguous, and it draws from the exact same word set
    used to build the embedding prototypes (_STANCE_PROTOTYPES above), not
    an independent hardcoded ruleset. Measured impact (2026-07, n=2979 real
    cached bill titles): removing this tier changes the outcome for 1.5%
    of bills, always by rescuing a genuinely directional bill ("STOP CCP
    Act", "End Veterans Overdose Act") that the embedding alone scored as
    neutral — never the reverse. Kept for that reason; see README
    "Classification Strategy" for how this fits the project's broader
    embeddings-first, disclosed-exceptions approach.

    Returns:
        (stance_text, stance_direction) where direction is "pro", "anti", or "neutral".
        "pro"  = bill supports/expands the policy area (Yea = supporting)
        "anti" = bill restricts/opposes the policy area (Nay = supporting)
        "neutral" = directional intent is ambiguous
    """
    area = policy_area.lower().replace("_", " ")

    # Tier 0: keyword prefix check for unambiguous stance verbs.
    # Uses the same word lists that define the stance prototypes, so this is
    # consistent with the embedding-based tier. Dense embedding models compress
    # scores toward 0.75-0.80 for short directional phrases, so a word-list
    # tier-0 provides higher precision for clear-cut cases (e.g. "A bill to
    # repeal X" is always anti, regardless of X's domain).
    _ANTI_VERBS = frozenset({
        "ban", "prohibit", "restrict", "limit", "block", "repeal",
        "eliminate", "remove", "defund", "rescind", "cut", "reduce",
        "rollback", "revoke", "abolish", "dismantle", "oppose",
        "curtail", "suspend", "withdraw", "terminate", "penalize",
        "sanction", "end", "halt", "prevent", "stop",
    })
    _PRO_VERBS = frozenset({
        "protect", "strengthen", "expand", "extend", "increase", "fund",
        "invest", "establish", "create", "mandate", "require",
        "reauthorize", "support", "promote", "enhance", "authorize",
        "improve", "safeguard", "guarantee", "ensure", "provide",
        "preserve", "advance", "empower",
    })
    # Normalise: strip leading articles/prepositions ("a bill to", "to", etc.)
    _stub = re.sub(r"^(a\s+)?bill\s+to\s+", "", bill_name.lower().strip())
    _first_word = _stub.split()[0] if _stub.split() else ""
    if _first_word in _ANTI_VERBS:
        keyword_dir: str | None = "anti"
    elif _first_word in _PRO_VERBS:
        keyword_dir = "pro"
    else:
        keyword_dir = None

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    stance_embs = _get_stance_embeddings()

    query_text = bill_name
    if summary and len(summary) > 30:
        query_text = f"{bill_name} {summary[:200]}"

    query_emb = model.encode([query_text[:300]], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    scores: dict[str, float] = {}
    for direction, emb in stance_embs.items():
        scores[direction] = float(np.dot(query_emb, emb))

    best_dir = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best_dir]

    # Require minimum absolute similarity to classify at all.
    # 2026-07 fix (O1): was 0.10, far below the measured floor — live
    # data (28 real bill titles with an unambiguous tier-0 directional
    # verb, so the "true" direction is known) scored best_score
    # mean=0.779, p10=0.739, min=0.673 even on these genuinely directional
    # titles; nothing was ever going to fall below 0.10. The real
    # precision work here is the margin-over-neutral check just below
    # (neutral-prototype score on these same directional titles is
    # already mean=0.734 — almost as high as best_score itself, so the
    # absolute floor was never going to be the thing separating
    # directional from ambiguous). 0.65 sits below the measured min so it
    # doesn't reject known-good cases, while still catching genuinely
    # degenerate/off-topic text below this model's typical floor.
    if best_score < 0.65:
        best_dir = "neutral"
    elif best_dir != "neutral":
        # For pro/anti, require a margin over neutral to avoid false positives.
        # If the keyword tier agreed with the embedding tier, accept even a
        # smaller margin to avoid over-predicting neutral.
        neutral_score = scores.get("neutral", 0.0)
        margin = best_score - neutral_score
        min_margin = 0.005 if keyword_dir == best_dir else 0.03
        if margin < min_margin:
            best_dir = "neutral"

    # If embedding disagrees with the keyword tier, defer to the keyword
    # result only when scores are very close (within 0.01) — the keyword is
    # more reliable for short, explicitly-framed bill titles.
    if keyword_dir and best_dir == "neutral":
        anti_score = scores.get("anti", 0.0)
        pro_score = scores.get("pro", 0.0)
        if abs(anti_score - pro_score) < 0.01:
            best_dir = keyword_dir

    if summary and len(summary) > 30:
        first_sentence = summary.split(".")[0].strip()
        first_sentence = re.sub(r"<[^>]+>", "", first_sentence).strip()
        if len(first_sentence) > 20:
            return first_sentence[:150], best_dir

    direction_labels = {"pro": "strengthen", "anti": "restrict", "neutral": "reform"}
    return f"{direction_labels[best_dir]} {area} policy", best_dir


# ── Main classification functions ────────────────────────────────


async def classify_all_bills(
    bills: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify bills using adaptive tiered multi-area classification.

    Each bill receives multiple policy area classifications reflecting
    the reality that legislation typically spans 2-4 policy domains
    (Adler & Wilkerson 2012). Party alignment is computed per-area and
    aggregated with confidence weights, producing a nuanced alignment
    score rather than a binary party label.

    Tiers: reference corpus kNN → embedding similarity → augmented re-embed.
    """
    if not bills:
        return []

    from app.pipeline.analyze.party_platform import (
        classify_party_alignment_multi,
    )

    logger.info("Classifying %d bills (adaptive multi-area, zero LLM)...", len(bills))
    classified = []
    procedural_count = 0
    multi_area_count = 0

    for b in bills:
        bill_name = b["billName"]
        bill_id = b["billId"]
        bill_text = _build_classification_text(b)
        bill_date = _extract_bill_date(b.get("actions", []))
        summary = b.get("summary", "")

        areas = classify_policy_areas_multi(
            bill_text, bill_id=bill_id, db_session=db_session,
        )
        policy_area = areas[0]["area"]
        confidence = areas[0]["confidence"]

        # 2026-07 (O3): PROCEDURAL only ever reaches here two ways — the
        # seed match (real score, always >= 0.74; see
        # _is_procedural_seed_match) or the low-confidence tier-2/3
        # fallback (score always < EMBEDDING_CONFIDENCE_THRESHOLD=0.70 by
        # construction, since that's the branch that triggers it). These
        # checks used to compare against a hardcoded 1.0/0.9 that only
        # ever matched the seed match (which used to always return exactly
        # 1.0); now that it returns its real score, EMBEDDING_CONFIDENCE_
        # THRESHOLD is the actual dividing line between the two cases.
        if policy_area == "PROCEDURAL" and confidence < EMBEDDING_CONFIDENCE_THRESHOLD:
            name_areas = classify_policy_areas_multi(bill_name)
            if name_areas[0]["area"] != "PROCEDURAL":
                areas = name_areas
                policy_area = areas[0]["area"]
                confidence = 0.5

        if policy_area == "PROCEDURAL" and confidence >= EMBEDDING_CONFIDENCE_THRESHOLD:
            proc = _make_procedural(b)
            proc["date"] = bill_date
            proc["policyAreas"] = [{"area": "PROCEDURAL", "confidence": confidence, "party": "bipartisan"}]
            proc["partyAlignmentWeight"] = 0.0
            classified.append(proc)
            procedural_count += 1
        else:
            if policy_area == "PROCEDURAL":
                policy_area = _augmented_embedding_classify(bill_text)
                areas = [{"area": policy_area, "confidence": confidence}]
            _stance_text, stance_direction = derive_stance(b["billName"], summary, policy_area)

            description = _clean_summary(summary, b["billName"], b.get("officialTitle", ""))

            multi_alignment = classify_party_alignment_multi(
                bill_text, areas, stance_direction,
            )

            content_alignment = multi_alignment["overall"]
            alignment_weight = multi_alignment["weight"]

            area_parties = {
                a["area"]: a["party"] for a in multi_alignment["areas"]
            }
            policy_areas_enriched = [
                {
                    "area": a["area"],
                    "confidence": a["confidence"],
                    "party": area_parties.get(a["area"], "bipartisan"),
                }
                for a in areas
            ]

            if len(areas) > 1:
                multi_area_count += 1

            classified.append({
                "billId": bill_id,
                "billName": bill_name,
                "congress": b["congress"],
                "date": bill_date,
                "description": description,
                "policyArea": policy_area,
                "policyAreas": policy_areas_enriched,
                "stance": stance_direction,
                "partyLeaning": content_alignment,
                "partyAlignmentWeight": alignment_weight,
            })

        _record_if_possible(db_session, bill_id, bill_text, policy_area, confidence)

    _validate_classifications(classified)
    substantive = len(classified) - procedural_count
    logger.info(
        "Classified %d/%d bills (%d substantive, %d procedural, %d multi-area)",
        len(classified), len(bills), substantive, procedural_count, multi_area_count,
    )
    return classified


def recent_roll_call_key(rc: dict) -> str:
    """Unique join key for one Senate roll call: congress-session-rollNumber.

    documentName (the billId shown in the UI) is NOT unique — the Senate
    votes on the same document repeatedly (motion to proceed, cloture,
    passage; cloture + confirmation for nominations), and the same document
    can be voted on in both sessions of a congress. Deduplicating or keying
    roll calls by documentName silently discarded every vote on a document
    except the newest one. Shared by classify_recent_votes (stamped on each
    classified dict as "rcKey") and senate_pipeline's dedupe/recent_rc_map.
    """
    return (
        f"{rc.get('congress', '')}-{rc.get('session', '')}-{rc.get('rollNumber', '')}"
    )


async def classify_recent_votes(
    roll_calls: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify recent roll call votes using adaptive multi-area classification.

    Key design: the Senate.gov question field describes the *parliamentary
    mechanism* ("On the Cloture Motion"), not the bill's policy content.
    We use learned motion type classification to separate the mechanism
    from the content, then classify the bill on its own merit with
    multi-area support.
    """
    if not roll_calls:
        return []

    logger.info("Classifying %d recent votes (adaptive multi-area, zero LLM)...", len(roll_calls))
    from app.pipeline.analyze.bill_learning import classify_motion_type
    from app.pipeline.analyze.party_platform import (
        classify_party_alignment_multi,
    )

    classified = []
    procedural_count = 0

    for rc in roll_calls:
        bill_id = (
            rc.get("documentName")
            or f"Roll-{rc.get('congress', '')}-{rc.get('session', '')}-{rc['rollNumber']}"
        )
        # Unique per roll call, unlike documentName/billId: the Senate votes
        # on the same document repeatedly (motion to proceed, cloture,
        # passage; cloture + confirmation for a "PN" nomination), so billId
        # alone collapses distinct votes. rcKey is the join key back to the
        # parsed roll-call data (senate_pipeline's recent_rc_map and
        # senator_votes); billId stays the display/storage identifier.
        rc_key = recent_roll_call_key(rc)
        name = rc.get("documentTitle") or rc.get("voteTitle") or "Unknown"
        question = (rc.get("question") or "")[:200]
        vote_date = rc.get("voteDate", "")

        motion_type = classify_motion_type(question) if question else "unknown"

        description = name
        if question and question.lower() != name.lower() and len(question) > 15:
            description = f"{name} — {question}"
            if len(description) > 200:
                description = description[:200].rsplit(" ", 1)[0] + "..."
        bill_content = name

        proc_areas = [{"area": "PROCEDURAL", "confidence": 0.95, "party": "bipartisan"}]

        # 2026-07 (O7): a third signal, a regex matching nomination-style
        # document-name phrasing ("X, of Y, to be Z"), used to run here
        # too. Live-measured against 182 real key-vote rows (53 genuine
        # PN-prefixed nominations): it caught zero cases that motion_type
        # and the PN prefix both missed, and itself missed a real one
        # ("...of the Virgin Islands, to be Judge...") — its single-word
        # \w+ state-name pattern doesn't match multi-word territories.
        # Removed rather than kept as a "disclosed exception": that bar
        # requires a measured failure mode the embedding/structural
        # signals don't already cover, and this measurement showed the
        # opposite — no unique value, plus its own bug.
        is_nomination = (
            motion_type == "nomination"
            or bill_id.startswith("PN")
        )

        if is_nomination:
            classified.append({
                "billId": bill_id,
                "rcKey": rc_key,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "policyAreas": proc_areas,
                "partyAlignmentWeight": 0.0,
                "stance": "nomination",
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
            _record_if_possible(db_session, bill_id, bill_content, "PROCEDURAL", 0.95)
            continue

        areas = classify_policy_areas_multi(
            bill_content, bill_id=bill_id, db_session=db_session,
        )
        policy_area = areas[0]["area"]
        confidence = areas[0]["confidence"]

        # See classify_all_bills' matching check (O3) for why this compares
        # against EMBEDDING_CONFIDENCE_THRESHOLD rather than a hardcoded 0.9.
        if policy_area == "PROCEDURAL" and confidence >= EMBEDDING_CONFIDENCE_THRESHOLD:
            classified.append({
                "billId": bill_id,
                "rcKey": rc_key,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "policyAreas": proc_areas,
                "partyAlignmentWeight": 0.0,
                "stance": "procedural",
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
        else:
            if policy_area == "PROCEDURAL":
                policy_area = _augmented_embedding_classify(bill_content)
                areas = [{"area": policy_area, "confidence": confidence}]
            _stance_text, stance_direction = derive_stance(name, question, policy_area)

            multi_alignment = classify_party_alignment_multi(
                bill_content, areas, stance_direction,
            )

            content_alignment = multi_alignment["overall"]
            alignment_weight = multi_alignment["weight"]

            area_parties = {
                a["area"]: a["party"] for a in multi_alignment["areas"]
            }
            policy_areas_enriched = [
                {
                    "area": a["area"],
                    "confidence": a["confidence"],
                    "party": area_parties.get(a["area"], "bipartisan"),
                }
                for a in areas
            ]

            classified.append({
                "billId": bill_id,
                "rcKey": rc_key,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": policy_area,
                "policyAreas": policy_areas_enriched,
                "partyAlignmentWeight": alignment_weight,
                "stance": stance_direction,
                "partyLeaning": content_alignment,
            })

        _record_if_possible(db_session, bill_id, bill_content, policy_area, confidence)

    _validate_classifications(classified)
    substantive = len(classified) - procedural_count
    logger.info(
        "Classified %d/%d recent votes (%d substantive, %d procedural)",
        len(classified), len(roll_calls), substantive, procedural_count,
    )
    return classified


# ── Helpers ──────────────────────────────────────────────────────


def _build_classification_text(b: dict) -> str:
    """Build semantically rich text for bill classification.

    Bills with uninformative short titles (named after people, acronyms)
    carry almost no policy signal for the embedding model.  This function
    assembles the richest available text from multiple sources:

      1. Bill name (always present)
      2. Official title from Congress.gov (e.g., 'A bill to prevent the
         purchase of ammunition by prohibited purchasers')
      3. CRS policy area (e.g., 'Crime and Law Enforcement')
      4. Summary text (when available)
      5. First portion of full bill text (fallback when summary is thin)

    The assembled text is truncated to 500 characters to fit within the
    sentence-transformer's effective context window.
    """
    parts = [b["billName"]]

    official_title = b.get("officialTitle", "")
    if official_title and official_title.lower() != b["billName"].lower():
        parts.append(official_title)

    crs_area = b.get("crsPolicyArea", "")
    if crs_area:
        parts.append(crs_area)

    summary = b.get("summary", "")
    if summary and len(summary.strip()) > 20:
        clean = re.sub(r"<[^>]+>", "", summary).strip()
        parts.append(clean[:300])

    if len(" ".join(parts)) < 60:
        full_text = b.get("fullText", "")
        if full_text and len(full_text.strip()) > 30:
            parts.append(full_text[:300])

    return " ".join(parts)[:500]


def _extract_bill_date(actions: list[dict]) -> str:
    """Extract the most relevant date from bill actions (e.g. when signed or passed)."""
    if not actions:
        return ""
    for action in actions:
        text = (action.get("text") or "").lower()
        if any(kw in text for kw in ("became public law", "signed by president", "passed senate")):
            date_str = action.get("actionDate") or action.get("date") or ""
            if date_str:
                return date_str
    if actions:
        return actions[0].get("actionDate") or actions[0].get("date") or ""
    return ""


def _make_procedural(b: dict) -> dict:
    official = b.get("officialTitle", "")
    desc = official if official and len(official) > 20 and official.lower() != b["billName"].lower() else b["billName"]
    return {
        "billId": b["billId"],
        "billName": b["billName"],
        "congress": b.get("congress", 0),
        "date": "",
        "description": desc,
        "policyArea": "PROCEDURAL",
        "stance": "procedural",
        "partyLeaning": "bipartisan",
    }


def _clean_summary(summary: str, bill_name: str, official_title: str = "") -> str:
    """Extract a meaningful description from CRS summary, official title, or bill name.

    Prefers CRS summary (most detailed), then official title (e.g. "A bill
    to prevent the purchase of ammunition by prohibited purchasers"), then
    falls back to the bill short title.
    """
    if summary and len(summary.strip()) > 10:
        clean = re.sub(r"<[^>]+>", "", summary).strip()
        if len(clean) > 200:
            cut = clean[:200].rsplit(" ", 1)[0]
            return cut + "..."
        if clean:
            return clean

    if official_title and len(official_title) > 20 and official_title.lower() != bill_name.lower():
        if len(official_title) > 200:
            cut = official_title[:200].rsplit(" ", 1)[0]
            return cut + "..."
        return official_title

    return bill_name


def _record_if_possible(
    db_session: Any | None,
    bill_id: str,
    text: str,
    policy_area: str,
    confidence: float,
) -> None:
    """Store classification in the learning store if a DB session is available."""
    if db_session is None:
        return
    try:
        from app.pipeline.analyze.bill_learning import record_classification
        record_classification(
            db_session, bill_id, text, policy_area, confidence, source="embedding",
        )
    except Exception:
        # record_classification issues a Core execute with no internal
        # rollback, so a failed write (e.g. "database is locked") leaves the
        # shared session in a failed state; roll back here or the next bill's
        # lookup_exact query raises PendingRollbackError.
        try:
            db_session.rollback()
        except Exception:
            pass
        logger.debug("Failed to record classification for bill %s", bill_id, exc_info=True)


def _validate_classifications(bills: list[dict]) -> None:
    """Validate and fix classification fields in-place."""
    for bill in bills:
        if not bill.get("policyArea") or not isinstance(bill.get("policyArea"), str):
            bill["policyArea"] = "PROCEDURAL"
        bill["policyArea"] = bill["policyArea"].strip().upper()

        if not bill.get("stance") or not isinstance(bill.get("stance"), str):
            bill["stance"] = "neutral"
        bill["stance"] = bill["stance"].strip().lower()
        if bill["stance"] not in ("pro", "anti", "neutral", "procedural", "nomination"):
            bill["stance"] = "neutral"

        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
