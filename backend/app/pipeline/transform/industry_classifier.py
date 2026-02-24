"""Hybrid industry classifier using a tiered strategy.

Classification tiers (in priority order):
1. Learning store lookup (instant, highest confidence)
2. Sentence-transformer embedding cosine similarity against industry
   descriptions (fast, no LLM, generalizes to unseen entities)
3. Returns "OTHER" — the LLM reclassifier in the pipeline handles
   unknowns in batch and feeds results back into the learning store.

The embedding approach is academically grounded: cosine similarity
in a dense embedding space is a standard text classification technique
(cf. sentence-BERT, Reimers & Gurevych 2019). It generalizes far
better than keyword lists because it captures semantic meaning.
"""

import logging
import numpy as np
from sqlalchemy.orm import Session

from app.models import LearnedClassification

logger = logging.getLogger(__name__)

INDUSTRY_DESCRIPTIONS: dict[str, str] = {
    "PHARMA": "pharmaceutical drugs biotech medicine vaccines clinical trials drug manufacturing biopharmaceutical Pfizer Merck AbbVie Eli Lilly Johnson & Johnson Novartis Sanofi AstraZeneca Amgen Gilead Moderna Regeneron",
    "INSURANCE": "insurance underwriting coverage premiums actuarial health insurance property casualty Anthem Cigna Humana UnitedHealth Aetna Blue Cross MetLife Aflac Progressive Allstate",
    "OIL_GAS": "oil gas petroleum drilling fracking pipeline fossil fuel refinery crude natural gas exploration Exxon Chevron ConocoPhillips BP Shell Halliburton Koch Marathon Valero",
    "DEFENSE": "defense military weapons aerospace missiles contractors armed forces naval army air force Lockheed Raytheon Boeing Northrop Grumman General Dynamics BAE L3Harris Leidos",
    "FINANCE": "banking investment securities hedge fund private equity venture capital financial services Wall Street asset management brokerage wealth management credit lending credit union savings loan mutual savings federal credit union community bank Goldman Sachs Morgan Stanley JPMorgan Citigroup Bank of America Wells Fargo BlackRock Fidelity Vanguard",
    "REAL_ESTATE": "real estate property housing mortgage realty homebuilder REIT commercial property development National Association of Realtors",
    "TECH": "technology software internet cloud computing artificial intelligence data silicon valley digital platform Google Alphabet Meta Facebook Apple Microsoft Amazon Oracle Salesforce Intel Nvidia",
    "TELECOM": "telecommunications wireless broadband cable internet service provider cellular network satellite AT&T Verizon T-Mobile Comcast Charter",
    "AGRIBUSINESS": "agriculture farming crop livestock dairy grain agribusiness ranching fertilizer seed Monsanto Cargill Archer Daniels Deere John Deere Bayer crop",
    "ENERGY": "energy utility electric power renewable solar wind nuclear coal generation transmission Duke Energy Dominion Exelon NextEra Southern Company",
    "CONSTRUCTION": "construction building contractor engineering infrastructure cement architecture development general contractor",
    "TRANSPORT": "transportation airline aviation railroad shipping trucking logistics freight maritime FedEx UPS Delta United Airlines American Airlines Southwest",
    "LAWYERS": "law firm attorney legal litigation counsel solicitor legal services LLP PLLC Skadden Jones Day Kirkland Latham Sidley Covington Greenberg",
    "LOBBYISTS": "lobbying government relations public affairs advocacy political consulting Akin Gump Brownstein",
    "GAMBLING": "casino gambling gaming sports betting lottery wagering Las Vegas Sands MGM Wynn Caesars",
    "GUNS": "firearm gun rifle ammunition weapons manufacturer second amendment NRA National Rifle Association Smith & Wesson Remington",
    "TOBACCO": "tobacco cigarette vaping e-cigarette nicotine smoking Altria Philip Morris Reynolds JUUL",
    "CRYPTO": "cryptocurrency bitcoin blockchain digital currency decentralized finance web3 Coinbase Binance",
    "PRIVATE_PRISON": "prison corrections incarceration detention correctional facility CoreCivic GEO Group",
    "LABOR_UNIONS": "union labor workers organized labor collective bargaining AFL-CIO SEIU Teamsters AFSCME United Auto Workers UAW carpenters electricians plumbers",
    "EDUCATION": "university college school education academic teaching research higher education",
    "MEDIA": "media broadcast television news publishing journalism entertainment studio film",
    "RETAIL": "retail store merchandise consumer goods shopping wholesale distribution",
    "MANUFACTURING": "manufacturing factory production industrial assembly plant fabrication",
    "HEALTHCARE": "hospital health clinic medical healthcare nursing patient care physician medical center health system medical association dental nurses association optometric chiropractic podiatry American Medical Association AMA HCA Kaiser Permanente Mayo Clinic",
    "POLITICAL": "political party campaign committee victory fund leadership fund election national committee Emily's List Club for Growth DSCC NRSC Democratic Republican senatorial congressional",
}

_KEYWORD_INDUSTRY_RULES: list[tuple[list[str], str]] = [
    (["CREDIT UNION", "FEDERAL CREDIT", "SAVINGS BANK", "SAVINGS & LOAN", "COMMUNITY BANK"], "FINANCE"),
    (["MEDICAL", "HOSPITAL", "HEALTH SYSTEM", "HEALTH CENTER", "CLINIC", "PHYSICIANS", "NURSES", "DENTAL", "OPTOMETRIC", "CHIROPRACTIC", "PODIATRY", "SURGICAL", "ORTHOPEDIC", "ANESTHESI", "RADIOLOG", "EMERGENCY MEDICINE", "DERMATOLOG", "OPHTHALM", "PEDIATRIC", "OBSTET", "UROLOG", "ONCOLOG", "CARDIO", "NEURO", "GASTRO", "PULMON", "NEPHRO", "PATHOLOG", "PSYCHIATRIC"], "HEALTHCARE"),
    (["PHARMA", "PHARMACEUTICAL", "BIOTECH", "BIOPHARMA"], "PHARMA"),
    (["INSURANCE", "UNDERWRITER", "CASUALTY", "MUTUAL LIFE", "BENEFIT PLAN"], "INSURANCE"),
    (["OIL ", "PETROLEUM", "NATURAL GAS", "PIPELINE", "REFINER", "DRILLING"], "OIL_GAS"),
    (["ELECTRIC COOPERATIVE", "ELECTRIC CO-OP", "POWER COMPANY", "UTILITY", "SOLAR", "WIND ENERGY"], "ENERGY"),
    (["REALT", "HOMEBUILDER", "MORTGAGE", "NATIONAL ASSOCIATION OF REALTORS"], "REAL_ESTATE"),
    (["LAW OFFICE", "LAW FIRM", "ATTORNEYS AT LAW", " LLP", " PLLC"], "LAWYERS"),
    (["AFL-CIO", "TEAMSTER", "CARPENTERS", "ELECTRICIAN", "PLUMBER", "LABORERS", "STEELWORKER", "MACHINISTS", "FIREFIGHTER", "TEACHERS UNION", "SEIU", "AFSCME", "IBEW"], "LABOR_UNIONS"),
    (["LOCKHEED", "RAYTHEON", "NORTHROP", "GENERAL DYNAMICS", "BAE SYSTEMS", "L3HARRIS", "LEIDOS"], "DEFENSE"),
    (["NATIONAL RIFLE", "GUN OWNERS", "FIREARM", "SECOND AMENDMENT"], "GUNS"),
]


def _keyword_industry(name_upper: str) -> str | None:
    """Fast keyword-based industry classification for unambiguous cases.

    Runs before the embedding classifier to catch obvious patterns like
    "XYZ CREDIT UNION PAC" or "ABC MEDICAL CENTER PAC" that the embedder
    misclassifies as POLITICAL because of the word "PAC" in the name.
    """
    for keywords, industry in _KEYWORD_INDUSTRY_RULES:
        if any(kw in name_upper for kw in keywords):
            return industry
    return None


_embeddings_cache: dict[str, np.ndarray] = {}
_model_ref = None
SIMILARITY_THRESHOLD = 0.35


def _get_model():
    """Lazy-load the shared sentence-transformer model."""
    global _model_ref
    if _model_ref is None:
        from app.pipeline.vector_store import get_embedding_model
        _model_ref = get_embedding_model()
    return _model_ref


def clear_industry_embedding_cache() -> None:
    """Clear cached industry embeddings (call after updating descriptions)."""
    _embeddings_cache.clear()


def _get_industry_embeddings() -> dict[str, np.ndarray]:
    """Pre-compute and cache industry description embeddings."""
    if _embeddings_cache:
        return _embeddings_cache

    model = _get_model()
    for industry, description in INDUSTRY_DESCRIPTIONS.items():
        embedding = model.encode([description], show_progress_bar=False)[0]
        _embeddings_cache[industry] = embedding / np.linalg.norm(embedding)

    logger.info("Computed embeddings for %d industry descriptions", len(_embeddings_cache))
    return _embeddings_cache


def classify_industry(org_name: str | None) -> str:
    """Classify a single org name, keyword rules first then embedding similarity.

    Args:
        org_name: Organization or employer name.

    Returns:
        Industry code string, or "OTHER" if below similarity threshold.
    """
    if not org_name or len(org_name.strip()) < 2:
        return "OTHER"

    keyword_hit = _keyword_industry(org_name.upper().strip())
    if keyword_hit:
        return keyword_hit

    industry_embs = _get_industry_embeddings()
    model = _get_model()

    query_emb = model.encode([org_name], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_industry = "OTHER"
    best_score = SIMILARITY_THRESHOLD

    for industry, ind_emb in industry_embs.items():
        score = float(np.dot(query_emb, ind_emb))
        if score > best_score:
            best_score = score
            best_industry = industry

    return best_industry


def classify_industries_batch(org_names: list[str]) -> dict[str, str]:
    """Batch-classify org names: keyword rules first, then embedding similarity.

    Much more efficient and stable than calling classify_industry() per name.
    """
    if not org_names:
        return {}

    results: dict[str, str] = {name: "OTHER" for name in org_names}

    # Fast keyword pass — resolve obvious cases before embedding
    needs_embedding: list[tuple[int, str]] = []
    keyword_resolved = 0
    for i, name in enumerate(org_names):
        if not name or len(name.strip()) < 2:
            continue
        hit = _keyword_industry(name.upper().strip())
        if hit:
            results[name] = hit
            keyword_resolved += 1
        else:
            needs_embedding.append((i, name))

    if not needs_embedding:
        logger.info("Batch classified %d org names (all %d via keywords)", len(org_names), keyword_resolved)
        return results

    industry_embs = _get_industry_embeddings()
    ind_keys = list(industry_embs.keys())
    ind_matrix = np.stack([industry_embs[k] for k in ind_keys])

    model = _get_model()
    valid_names = [name for _, name in needs_embedding]

    _ENCODE_BATCH = 256
    all_embeddings = []
    for start in range(0, len(valid_names), _ENCODE_BATCH):
        batch = valid_names[start : start + _ENCODE_BATCH]
        embs = model.encode(batch, show_progress_bar=False, batch_size=min(64, len(batch)))
        all_embeddings.append(embs)
    query_embs = np.vstack(all_embeddings)
    norms = np.linalg.norm(query_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    query_embs = query_embs / norms

    scores = query_embs @ ind_matrix.T
    best_indices = np.argmax(scores, axis=1)
    best_scores = scores[np.arange(len(scores)), best_indices]

    for j, (_, name) in enumerate(needs_embedding):
        if best_scores[j] > SIMILARITY_THRESHOLD:
            results[name] = ind_keys[best_indices[j]]

    logger.info(
        "Batch classified %d org names (%d keywords, %d embedding)",
        len(org_names),
        keyword_resolved,
        int(np.sum(best_scores > SIMILARITY_THRESHOLD)),
    )
    return results


def classify_with_learning(
    org_name: str,
    db_session: Session | None = None,
) -> tuple[str, str]:
    """Classify using the full tiered strategy.

    Returns:
        (industry_code, source) where source is "learned", "embedding", or "unknown"
    """
    if not org_name or len(org_name.strip()) < 2:
        return "OTHER", "unknown"

    normalized = org_name.upper().strip()

    if db_session is not None:
        learned = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == normalized,
                LearnedClassification.entity_type == "industry",
            )
            .first()
        )
        if learned:
            return learned.value, "learned"

    industry = classify_industry(org_name)
    if industry != "OTHER":
        if db_session is not None:
            _store_classification(db_session, normalized, "industry", industry, 0.9, "embedding")
        return industry, "embedding"

    return "OTHER", "unknown"


def classify_batch_with_learning(
    org_names: list[str],
    db_session: Session | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Batch classify with learning store.

    Returns:
        (results_dict, unknowns_list) where unknowns need LLM classification.
    """
    results: dict[str, str] = {}
    unknowns: list[str] = []

    known_from_db: dict[str, str] = {}
    if db_session is not None:
        unique_names = list({n.upper().strip() for n in org_names if n})
        if unique_names:
            rows = (
                db_session.query(LearnedClassification)
                .filter(
                    LearnedClassification.entity_name.in_(unique_names),
                    LearnedClassification.entity_type == "industry",
                )
                .all()
            )
            known_from_db = {r.entity_name: r.value for r in rows}

    for name in org_names:
        if not name:
            results[name] = "OTHER"
            continue

        normalized = name.upper().strip()

        if normalized in known_from_db:
            results[name] = known_from_db[normalized]
            continue

        industry = classify_industry(name)
        results[name] = industry

        if industry != "OTHER":
            if db_session is not None:
                _store_classification(db_session, normalized, "industry", industry, 0.9, "embedding")
        else:
            unknowns.append(name)

    return results, unknowns


def store_llm_classifications(
    classifications: dict[str, str],
    db_session: Session,
) -> None:
    """Store LLM-derived classifications in the learning store.

    Called after the LLM reclassifier processes unknowns.
    Next pipeline run will find these via the learning store (tier 1).
    """
    for name, industry in classifications.items():
        normalized = name.upper().strip()
        _store_classification(db_session, normalized, "industry", industry, 0.7, "llm")
    db_session.flush()


def _store_classification(
    db_session: Session,
    entity_name: str,
    entity_type: str,
    value: str,
    confidence: float,
    source: str,
) -> None:
    """Upsert a classification into the learning store."""
    from datetime import datetime
    existing = (
        db_session.query(LearnedClassification)
        .filter(
            LearnedClassification.entity_name == entity_name,
            LearnedClassification.entity_type == entity_type,
        )
        .first()
    )
    if existing:
        if confidence >= existing.confidence:
            existing.value = value
            existing.confidence = confidence
            existing.source = source
            existing.learned_at = datetime.utcnow()
    else:
        db_session.add(LearnedClassification(
            entity_name=entity_name,
            entity_type=entity_type,
            value=value,
            confidence=confidence,
            source=source,
        ))
    db_session.commit()
