"""Data-driven "highlights" summary shared by the Senate and House detail
routes — no LLM, pure data. Senator.model_dump(by_alias=True) and
representative_service.build_rep_response() intentionally emit the same
camelCase key names (see the "key shared with senator schema" comments in
build_rep_response), so one function reading the wire-format dict covers
both chambers rather than each route carrying its own copy.
"""

from app.config_definitions import SCORE_WEIGHTS


def build_highlights(entity: dict) -> list[str]:
    """Generate factual, data-driven insights from a senator/representative
    detail response dict."""
    funding = entity["funding"]
    score = entity["representationScore"]
    name = entity["name"]
    hints: list[tuple[int, str]] = []  # (priority, text)

    total = funding["totalRaised"]
    small_pct = funding.get("smallDonorPercentage") or 0
    pac_total = funding.get("totalFromPACs") or 0
    pac_pct_raw = pac_total / total * 100 if total > 0 else 0.0
    pac_pct_str = "<1" if 0 < pac_pct_raw < 1 else f"{pac_pct_raw:.0f}"

    # --- Funding highlights ---
    if small_pct >= 50:
        hints.append((10, (
            f"Grassroots funded: {small_pct:.0f}% of {name}'s "
            f"${total / 1e6:.1f}M raised comes from small donors (under $200), "
            f"suggesting broad constituent support."
        )))
    elif small_pct < 15 and total > 0:
        small_str = "<1" if 0 < small_pct < 1 else f"{small_pct:.0f}"
        hints.append((10, (
            f"Only {small_str}% of {name}'s "
            f"${total / 1e6:.1f}M came from small donors — "
            f"the vast majority flows from large donors and organizations."
        )))

    if pac_pct_raw > 40:
        hints.append((9, (
            f"PAC-heavy: {pac_pct_str}% of funding (${pac_total:,.0f}) "
            f"comes from political action committees."
        )))
    elif pac_pct_raw < 5 and total > 500_000:
        hints.append((9, (
            f"Virtually PAC-free: Only ${pac_total:,.0f} "
            f"({pac_pct_str}%) came from PACs — an unusually low amount."
        )))

    # Top industry donor
    industry_donors = [
        d for d in funding["topDonors"]
        if d["type"] not in ("CandidateAffiliated",) and d["industry"] not in (
            "POLITICAL", "SMALL_DONORS", "LARGE_INDIVIDUAL", "OTHER"
        )
    ]
    if industry_donors:
        top = industry_donors[0]
        hints.append((5, (
            f"Largest industry donor: {top['name']} "
            f"(${top['total']:,.0f}, {top['industry'].replace('_', ' ').title()})."
        )))

    # --- Lobbying matches ---
    matches = entity.get("lobbyingMatches") or []
    aligned_matches = sum(1 for m in matches if m.get("senatorVoteAligned"))
    if len(matches) > 3 and aligned_matches > 2:
        hints.append((7, (
            f"Found {len(matches)} donor-vote connections where a major donor's "
            f"industry overlaps with legislation — {aligned_matches} votes went "
            f"the donor's way."
        )))
    elif len(matches) == 0:
        hints.append((3, (
            "No direct donor-vote industry connections detected in tracked legislation."
        )))

    # --- Promise fulfillment ---
    promises = entity.get("campaignPromises") or []
    kept = sum(1 for p in promises if p["alignment"] == "kept")
    broken = sum(1 for p in promises if p["alignment"] == "broken")
    if len(promises) > 0:
        if kept > 0 and broken == 0:
            hints.append((6, (
                f"Platform follow-through: {kept} of {len(promises)} tracked "
                f"campaign promises rated as kept, with none broken."
            )))
        elif broken > kept and len(promises) >= 3:
            hints.append((6, (
                f"Promise gap: {broken} campaign promises rated as broken "
                f"versus only {kept} kept out of {len(promises)} tracked."
            )))

    # --- Overall score ---
    total_score = round(sum(score[key] * weight for key, weight in SCORE_WEIGHTS.items()))
    if total_score >= 80:
        hints.append((2, (
            f"Overall representation score: {total_score}/100 — "
            f"strong marks across funding transparency, voting independence, "
            f"and promise fulfillment."
        )))
    elif total_score <= 40:
        hints.append((2, (
            f"Overall representation score: {total_score}/100 — "
            f"significant concerns across funding sources, voting patterns, "
            f"or promise fulfillment."
        )))

    hints.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in hints]
