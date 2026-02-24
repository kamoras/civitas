"""Analyze Senate floor speeches for advocacy alignment with campaign promises.

Purely algorithmic — **zero LLM calls**.  Uses keyword matching to determine
which policy categories a senator is actively discussing on the Senate floor.
This captures "effort" that voting records miss: in a gridlocked Senate, a
senator who can't get bills passed but keeps raising their promised issues
on the floor is still trying to represent their constituents.

The output feeds into the Promise Persistence score as a 20% advocacy bonus.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Keywords for each platform/promise category (all lowercase).
# Matching is substring-based against the combined title + speech text.
_CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "healthcare": {
        "health", "healthcare", "medical", "hospital", "drug", "prescription",
        "medicare", "medicaid", "insurance", "affordable care", "pharmaceutical",
        "patient", "vaccine", "mental health", "opioid", "nursing", "physician",
    },
    "economy": {
        "economy", "economic", "jobs", "employment", "wage", "inflation",
        "recession", "growth", "business", "tax", "fiscal", "budget",
        "deficit", "debt", "spending",
    },
    "defense": {
        "military", "defense", "pentagon", "troops", "veteran", "armed forces",
        "national security", "terrorism", "homeland", "nato", "deployment",
    },
    "environment": {
        "climate", "environment", "emission", "pollution", "clean energy",
        "epa", "renewable", "solar", "wind", "carbon", "green",
        "conservation", "wildfire", "water quality",
    },
    "immigration": {
        "immigration", "immigrant", "border", "asylum", "refugee", "visa",
        "deportation", "daca", "citizenship", "undocumented", "migrant",
    },
    "education": {
        "education", "school", "student", "teacher", "college", "university",
        "tuition", "student loan", "pell grant", "curriculum",
    },
    "labor": {
        "labor", "worker", "union", "minimum wage",
        "overtime", "workplace", "collective bargaining", "pension",
        "retirement",
    },
    "justice": {
        "justice", "crime", "criminal", "police", "prison", "sentencing",
        "court", "judge", "law enforcement", "civil rights", "voting rights",
    },
    "guns": {
        "gun", "firearm", "second amendment", "background check",
        "assault weapon", "mass shooting", "ammunition",
    },
    "tech": {
        "technology", "artificial intelligence", "data privacy", "cyber",
        "broadband", "internet", "social media", "algorithm",
    },
    "finance": {
        "wall street", "banking", "financial", "regulation", "dodd-frank",
        "consumer protection", "credit", "mortgage", "interest rate",
    },
    "energy": {
        "energy", "oil", "gas", "pipeline", "nuclear", "fossil fuel",
        "electricity", "power grid", "fracking",
    },
    "trade": {
        "trade", "tariff", "import", "export", "supply chain",
        "manufacturing",
    },
    "welfare": {
        "welfare", "snap", "food stamp", "social security", "disability",
        "housing", "homelessness", "poverty", "child care",
    },
    "infrastructure": {
        "infrastructure", "highway", "bridge", "road", "transit", "rail",
        "airport", "water system", "construction",
    },
    "civil_rights": {
        "civil rights", "discrimination", "equality", "racial",
        "diversity", "equity", "hate crime",
    },
    "foreign_policy": {
        "foreign policy", "diplomacy", "sanctions", "treaty",
        "united nations", "china", "russia", "iran", "ukraine",
        "international", "ambassador", "state department",
    },
}


def _word_boundary_match(keyword: str, text: str) -> bool:
    """Check if keyword appears as a whole word/phrase (not as a substring of another word)."""
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))


def _classify_remark_categories(text: str) -> set[str]:
    """Determine which policy categories a floor remark relates to."""
    text_lower = text.lower()
    matched: set[str] = set()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if _word_boundary_match(kw, text_lower):
                matched.add(category)
                break
    return matched


def analyze_floor_advocacy(
    senator_remarks: list[dict],
    campaign_promises: list[dict],
) -> dict:
    """Analyze whether a senator's floor remarks align with their campaign promises.

    Args:
        senator_remarks: List of remark dicts from ``parse_speaking_turns``
            (keys: ``date``, ``text``, ``title``).
        campaign_promises: List of promise dicts from platform analysis
            (keys: ``promiseText``, ``category``, ``alignment``, ...).

    Returns:
        Dict with:
            ``advocacyCoverage``: float (0–1), fraction of distinct promise
                categories that have matching floor advocacy evidence.
            ``advocatedCategories``: list of all policy categories discussed
                on the floor (may include categories beyond promises).
            ``totalRemarks``: int, total floor remarks for this senator.
            ``remarksByCategory``: dict mapping category → remark count.
    """
    if not senator_remarks:
        return {
            "advocacyCoverage": 0.0,
            "advocatedCategories": [],
            "totalRemarks": 0,
            "remarksByCategory": {},
        }

    # Classify every remark by policy category
    category_counts: dict[str, int] = {}
    for remark in senator_remarks:
        text = remark.get("text", "")
        title = remark.get("title", "")
        categories = _classify_remark_categories(f"{title} {text}")
        for cat in categories:
            category_counts[cat] = category_counts.get(cat, 0) + 1

    # Determine which *promise* categories have floor advocacy evidence
    promise_categories: set[str] = set()
    for p in campaign_promises:
        cat = p.get("category", "other")
        if cat != "other":
            promise_categories.add(cat)

    advocated_promise_cats = promise_categories & set(category_counts.keys())
    coverage = (
        len(advocated_promise_cats) / len(promise_categories)
        if promise_categories
        else 0.0
    )

    return {
        "advocacyCoverage": round(coverage, 2),
        "advocatedCategories": sorted(category_counts.keys()),
        "totalRemarks": len(senator_remarks),
        "remarksByCategory": category_counts,
    }
