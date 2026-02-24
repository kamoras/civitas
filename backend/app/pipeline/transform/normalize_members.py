"""Normalize Congress.gov member data into base senator records."""

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

STATE_NAME_TO_CODE = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}


def normalize_members(
    members: list[dict], member_details: dict[str, dict] | None = None
) -> list[dict]:
    """Normalize Congress.gov member data into base senator records.

    Args:
        members: Raw member data from Congress.gov API.
        member_details: Map of bioguideId -> detailed member info.

    Returns:
        List of base senator records.
    """
    if member_details is None:
        member_details = {}

    results = []
    for m in members:
        # Only current senators -- check member detail terms for Senate chamber
        detail = member_details.get(m.get("bioguideId", ""), {})
        detail_terms = detail.get("terms", [])
        member_terms_obj = m.get("terms") or {}
        member_terms = member_terms_obj.get("item", []) if isinstance(member_terms_obj, dict) else []
        all_terms = detail_terms + member_terms
        senate_term = any(t.get("chamber") == "Senate" for t in all_terms)
        if not senate_term and m.get("chamber") != "Senate":
            continue

        name = m.get("name") or f"{m.get('firstName', '')} {m.get('lastName', '')}"
        state = _extract_state_code(m, detail)
        party = _normalize_party(m.get("partyName") or m.get("party"))
        years_in_office = _calculate_years_in_office(m, detail)

        # Generate ID matching existing format: lastname-firstname
        name_parts = name.split()
        last_name = re.sub(r"[^a-z]", "", name_parts[-1].lower()) if name_parts else ""
        first_name = re.sub(r"[^a-z]", "", name_parts[0].lower()) if name_parts else ""
        senator_id = f"{last_name}-{first_name}"

        # Initials
        initials = "".join(
            p[0].upper()
            for p in name_parts
            if p and not p.startswith("(")
        )[:2]

        official_url = (detail.get("officialWebsiteUrl") or "").rstrip("/")

        results.append({
            "bioguideId": m.get("bioguideId", ""),
            "id": senator_id,
            "name": _clean_name(name),
            "state": state,
            "party": party,
            "yearsInOffice": years_in_office,
            "initials": initials,
            "officialWebsiteUrl": official_url,
            # These will be populated later
            "punkNickname": "",
            "representationScore": {
                "fundingIndependence": 0,
                "promisePersistence": 0,
                "independentVoting": 0,
                "transparency": 0,
                "accessibility": 0,
            },
            "funding": {
                "totalRaised": 0,
                "totalFromPACs": 0,
                "smallDonorPercentage": 0,
                "topDonors": [],
                "industryBreakdown": [],
            },
            "votingRecord": {
                "totalVotes": 0,
                "scoreableVotes": 0,
                "donorAlignedVotes": 0,
                "donorOpposedVotes": 0,
                "policyBreakdown": [],
                "keyVotes": [],
            },
            "lobbyingMatches": [],
        })

    return results


def _clean_name(name: str) -> str:
    """Clean up senator name formatting."""
    # Congress.gov returns "LastName, FirstName" format sometimes
    if "," in name:
        parts = [s.strip() for s in name.split(",", 1)]
        if len(parts) == 2:
            return f"{parts[1]} {parts[0]}"
    # Remove suffixes like Jr., III, etc. from display (keep for ID)
    # The original JS keeps the suffix but uses .replace that returns the match itself,
    # effectively a no-op. Replicate that behavior: do not strip suffixes.
    return name


def _normalize_party(party_name: str | None) -> str:
    """Normalize party name to single letter code."""
    if not party_name:
        return "I"
    lower = party_name.lower()
    if "republican" in lower:
        return "R"
    if "democrat" in lower:
        return "D"
    if "independent" in lower:
        return "I"
    # Single letter check
    if party_name == "R":
        return "R"
    if party_name == "D":
        return "D"
    return "I"


def _extract_state_code(member: dict, detail: dict) -> str:
    """Extract the two-letter state code from member data."""
    # First try stateCode from detail terms (most reliable)
    detail_terms = detail.get("terms", [])
    for term in detail_terms:
        sc = term.get("stateCode")
        if sc and len(sc) == 2:
            return sc

    # Try member terms
    member_terms_obj = member.get("terms") or {}
    member_terms = member_terms_obj.get("item", []) if isinstance(member_terms_obj, dict) else []
    for term in member_terms:
        sc = term.get("stateCode")
        if sc and len(sc) == 2:
            return sc

    # Convert full state name to code
    state_name = member.get("state") or detail.get("state")
    if state_name:
        if len(state_name) == 2:
            return state_name  # Already a code
        code = STATE_NAME_TO_CODE.get(state_name)
        if code:
            return code

    logger.warning(
        "Could not determine state code for %s",
        member.get("name") or member.get("bioguideId"),
    )
    return "??"


def _calculate_years_in_office(member: dict, detail: dict) -> int:
    """Calculate years in office from term data."""
    # Try to get the earliest Senate start date
    detail_terms_obj = detail.get("terms") or {}
    detail_terms_items = detail_terms_obj.get("item", []) if isinstance(detail_terms_obj, dict) else (
        detail_terms_obj if isinstance(detail_terms_obj, list) else []
    )
    member_terms_obj = member.get("terms") or {}
    member_terms_items = member_terms_obj.get("item", []) if isinstance(member_terms_obj, dict) else []
    terms = detail_terms_items + member_terms_items
    senate_terms = [t for t in terms if t.get("chamber") == "Senate"]

    if senate_terms:
        # Sort by start year
        sorted_terms = sorted(
            senate_terms, key=lambda t: t.get("startYear", 9999)
        )
        first_year = sorted_terms[0].get("startYear")
        if first_year:
            return datetime.now().year - first_year

    # Fallback: check depiction/service info
    depiction = member.get("depiction") or {}
    attribution = depiction.get("attribution", "")
    if attribution:
        match = re.search(r"since (\d{4})", attribution)
        if match:
            return datetime.now().year - int(match.group(1))

    logger.warning(
        "Could not determine years in office for %s",
        member.get("name") or member.get("bioguideId"),
    )
    return 0
