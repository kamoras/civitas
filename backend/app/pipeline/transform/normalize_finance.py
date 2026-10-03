"""Normalize FEC financial data into the Senator funding shape.

Donor type and industry classification are handled by the AI classifier
pipeline (donor_classifier_ai.py) using FEC metadata, sentence-transformer
embeddings, and a kNN learning store. This module focuses purely on
normalizing the financial data structure — it does NOT re-derive donor
types from hardcoded patterns.

When the AI classifier has a result, it's used directly. For entities
not in the AI results (edge cases), it falls back to the embedding-based
industry classifier.
"""

import logging

from app.pipeline.fetch.fec import committee_id_of, is_political_committee, select_recent_elections
from app.pipeline.transform.candidate_names import is_candidate_self_donor
from app.pipeline.transform.industry_classifier import classify_with_learning, primed_industry_lookups
from app.pipeline.transform.occupation_industry import industry_of_occupation
from app.pipeline.analyze.donor_classifier_ai import (
    classify_donor_type_semantic,
    classify_employer_skips_batch,
    classify_transfer_memos_batch,
    is_skip_entity,
    skip_entities_batch,
)

logger = logging.getLogger(__name__)


def _is_contribution_row(receipt: dict) -> bool:
    """True when a Schedule A row is an actual third-party contribution.

    Schedule A on candidate filings (Form 3) itemizes every receipt, not
    just donations. Only line 11 is contributions (11AI individuals, 11B
    party committees, 11C other committees, 11D the candidate). The rest
    is structurally not donor money and was polluting top-donor lists
    fleet-wide (2026-07 audit): line 12 transfers from joint-fundraising
    committees ($768M cached), 13A/13B loans ($219M — banks that lent to
    campaigns listed as "donors"), 14 offsets to operating expenditures
    ($33M — media-buy refunds, e.g. a vendor as a senator's top donor),
    15 other receipts ($73M — bank interest), and 17A conduit totals
    (WinRed aggregates whose underlying gifts are already itemized on
    11AI). Rows without a line_number are kept — the memo-text and
    donor-type classifiers still screen those.
    """
    line = receipt.get("line_number")
    if line is None or line == "":
        return True
    return str(line).startswith("11")


# The FEC's itemized totals write a missing employer as the text "NULL" —
# a data-format convention for an absent value, like "SELF-EMPLOYED" is
# one for a status, not something to classify. Read as a name, it was a
# top donor on 354 of the members' live scorecards (2026-10-03, usually
# rank 1, median $25,400), typed Org/Employees: "Null" listed above every
# real organization that gave. Its four letters also pass the employer
# skip classifier's three-character floor.
MISSING_VALUE_TEXT = frozenset({"NULL"})


def _employer_values_not_organizations(
    employers: list[dict], occupations: list[dict] | None,
) -> set[str]:
    """Employer-field values (upper-cased) that name no organization: the
    missing-value text, and any value the same committee's donors wrote
    as their OCCUPATION at least as often, by dollars, as their employer
    ("OWNER", "PRESIDENT", "ATTORNEY", "HOUSEWIFE": a job, entered in the
    wrong box). That is the FEC's own two fields testifying, not a word
    list: a real organization is an employer far more than an occupation.
    The embedding classifier can't make this call: measured on the
    17,812 live donor names (2026-10-03), the best occupation-vs-
    organization prototype pair reached AUC 0.94, and catching half of 42
    occupation values would have dropped 279 real organizations."""
    as_employer: dict[str, float] = {}
    for r in employers:
        name = (r.get("employer") or "").upper().strip()
        if name:
            as_employer[name] = as_employer.get(name, 0.0) + (r.get("total") or 0)
    as_occupation: dict[str, float] = {}
    for r in occupations or []:
        name = (r.get("occupation") or "").upper().strip()
        if name:
            as_occupation[name] = as_occupation.get(name, 0.0) + (r.get("total") or 0)
    return {
        name for name, dollars in as_employer.items()
        if name in MISSING_VALUE_TEXT or as_occupation.get(name, 0.0) >= dollars > 0
    }


def _is_candidate_line(receipt: dict) -> bool:
    """Line 11D — contributions from the candidate themselves."""
    return str(receipt.get("line_number") or "") == "11D"


def summarize_election_totals(recent_cycles: list[dict]) -> dict:
    """Dollar totals for one election window (select_recent_elections'
    rows), shared by the pipeline and scripts/rescore.py so the two can't
    compute funding differently.

    `total_raised` is FEC's `receipts` — the headline figure, shown as
    "raised". It also contains money that isn't a contribution to this
    candidate: transfers in from joint fundraising committees and other
    authorized committees, bank loans, refunds and offsets. Shares (PAC %,
    small-donor %) are computed over `total_contributions` instead: FEC's
    `contributions` (individuals + PACs + party committees + the
    candidate's own contributions) plus loans the candidate made to their
    own campaign — money from contributors or from the candidate. Using
    receipts understated PAC dependency for exactly the members who raise
    most heavily through joint fundraising committees (typically party
    leaders): the transfer dollars sat in the denominator while their PAC
    content was invisible. JFC transfers are excluded from both sides —
    FEC totals don't break them down by source, so the shares describe
    money the campaign received directly. Falls back to receipts when a
    cached row predates the `contributions` field.

    Every sum floors at 0: FEC's per-cycle totals can be genuinely negative
    (a just-opened next-cycle committee with more refunds than receipts —
    2026-07 audit), and "negative dollars raised" is never meaningful.
    """
    def total(field: str) -> float:
        return max(0.0, sum(c.get(field, 0) or 0 for c in recent_cycles))

    total_raised = total("receipts")
    contributions = total("contributions") + total("loans_made_by_candidate")
    return {
        "total_raised": total_raised,
        "total_contributions": contributions if contributions > 0 else total_raised,
        "total_from_pacs": total("other_political_committee_contributions"),
        "small_individual": total("individual_unitemized_contributions"),
        "large_individual": total("individual_itemized_contributions"),
    }


def normalize_finance(
    candidate: dict | None,
    financials: list[dict],
    individual_receipts: list[dict],
    pac_receipts: list[dict],
    aggregated_contributors: list[dict],
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
    committee_meta_map: dict[str, dict] | None = None,
    detail: dict | None = None,
) -> dict:
    """Normalize FEC financial data into the Senator funding shape.

    Args:
        candidate: FEC candidate record.
        financials: FEC financial totals (by cycle).
        individual_receipts: Individual contribution receipts (Schedule A, is_individual=true).
        pac_receipts: PAC/committee contribution receipts (Schedule A, is_individual=false).
        aggregated_contributors: Top contributors by total.
        ai_classifications: Optional AI classifications for donors (type + industry).
        committee_meta_map: Optional contributor_id -> the FEC committee
            master's {"type", "designation", "connectedOrg"} (see
            fec.resolve_committee_meta). The type is reported on the donor
            (not scored). A committee the FEC registers as a
            party, candidate, joint-fundraising or leadership committee is
            political money (industry POLITICAL) whatever its name reads
            like, and a PAC's connected organization names who sponsors it.
        detail: the complete contribution detail (fetch_contribution_detail):
            {"pacs": {committee_id: dollars} from the FEC's bulk file,
            "committees": the committee master for those ids, "occupations"
            and "employers": the FEC's itemized totals}. Each part that is
            None (its source couldn't be read) falls back to the sampled
            receipts above, which cover a sliver of a large campaign.

    Returns:
        Normalized funding object matching Senator.funding type.
    """
    # Sum across the candidate's most recent election only (their current
    # mandate's campaign) — see select_recent_elections for rationale and
    # why raw [:2] double-counted.
    # office bounds how stale a "completed election" may be — see
    # select_recent_elections. The FEC candidate record carries it.
    recent_cycles = select_recent_elections(
        financials, office=(candidate or {}).get("office"))
    totals = summarize_election_totals(recent_cycles)
    total_raised = totals["total_raised"]
    contribution_base = totals["total_contributions"]
    total_from_pacs = totals["total_from_pacs"]
    small_individual = totals["small_individual"]
    large_individual = totals["large_individual"]
    small_donor_percentage = (
        round((small_individual / contribution_base) * 100) if contribution_base > 0 else 0
    )

    # Build top donors: PACs first, then employer-grouped individuals
    candidate_name = (candidate or {}).get("name", "")
    detail = detail or {}
    committees = detail.get("committees") or {}
    # One batch encode for the names the industry classifier would otherwise
    # encode one at a time, in both passes below.
    with primed_industry_lookups(
        [committee_donor_name(committees.get(cid), cid) for cid in detail.get("pacs") or {}]
        + [(r.get("employer") or "").upper().strip() for r in detail.get("employers") or []],
        db_session,
    ):
        top_donors = build_top_donors(
            pac_receipts,
            individual_receipts,
            aggregated_contributors,
            candidate_name,
            ai_classifications=ai_classifications,
            db_session=db_session,
            committee_meta_map=committee_meta_map,
            detail=detail,
        )

        # Build industry breakdown: individuals get explicit buckets, PACs get industry-classified
        industry_breakdown = _build_industry_breakdown(
            pac_receipts=pac_receipts,
            individual_receipts=individual_receipts,
            aggregated_contributors=aggregated_contributors,
            small_individual_total=small_individual,
            large_individual_total=large_individual,
            contribution_base=contribution_base,
            ai_classifications=ai_classifications,
            db_session=db_session,
            candidate_name=candidate_name,
            committee_meta_map=committee_meta_map,
            detail=detail,
        )

    computed_pac_total = sum(
        d["total"] for d in top_donors
        if d.get("type") in ("PAC", "SuperPAC", "Party/Ideological")
    )

    # The FEC's cycle totals (other_political_committee_contributions,
    # Schedule A) are the authoritative PAC figure. The classifier-derived
    # sum systematically undercounts when committee donors are typed as
    # Org/Employees — the 2026-06 audit found $34.8K recorded vs millions
    # actual for senior senators, which then scored as funding independence.
    # Use the classifier sum only when the FEC total is missing.
    if total_from_pacs > 0:
        final_pac_total = min(total_from_pacs, contribution_base)
    else:
        final_pac_total = min(computed_pac_total, contribution_base)

    return {
        "totalRaised": round(total_raised),
        "totalContributions": round(contribution_base),
        "totalFromPACs": round(final_pac_total),
        "smallDonorPercentage": small_donor_percentage,
        "topDonors": top_donors,
        "industryBreakdown": industry_breakdown,
    }


def committee_donor_name(meta: dict | None, committee_id: str) -> str:
    """How a giving committee is listed and classified: its sponsor when it
    is an organization's fund (the industry is the sponsor's, and the
    lobbying registry lists the sponsor), otherwise its own name."""
    meta = meta or {}
    return meta.get("connectedOrg") or meta.get("name") or committee_id


def committee_donor_type(meta: dict | None) -> str:
    """A giving committee's donor type from its FEC registration (tier 1): a
    party, candidate, joint-fundraising or leadership committee is
    political, any other committee a PAC. Every giver in the bulk file is a
    committee, so no name is read to decide it."""
    return "Party/Ideological" if is_political_committee(meta) else "PAC"


def build_top_donors(
    pac_receipts: list[dict],
    individual_receipts: list[dict],
    aggregated_contributors: list[dict],
    candidate_name: str,
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
    committee_meta_map: dict[str, dict] | None = None,
    detail: dict | None = None,
) -> list[dict]:
    """Build top donors list prioritizing PAC/corporate money.

    Donor type and industry come from the AI classifier (embedding-based).
    When no AI classification exists, falls back to the embedding-based
    industry classifier and semantic donor-type classifier.

    committee_meta_map: contributor_id -> the FEC committee master's
    {"type", "designation", "connectedOrg"} (see fec.resolve_committee_meta)
    for each PAC that appears in pac_receipts. The type ("Q"=Qualified/
    multicandidate, "N"=Nonqualified, ...) is reported on the donor, and
    type and designation decide the political-committee rule.

    detail (normalize_finance): when its "pacs" are known, every committee
    that gave is listed from the FEC's bulk file and the sampled PAC
    receipts supply only the candidate's own contributions; when its
    "employers" are known, the employee side comes from the FEC's employer
    totals instead of the 100 largest receipts.
    """
    donor_map: dict[str, dict] = {}
    ai_classifications = ai_classifications or {}
    committee_meta_map = committee_meta_map or {}
    detail = detail or {}
    pacs = detail.get("pacs")
    employers = detail.get("employers")

    # Pre-compute embedding-based skip sets for employers and memo texts.
    # This replaces hardcoded SKIP_EMPLOYERS and keyword-based memo filtering
    # with semantic similarity against learned prototypes.
    unique_employers = list({
        (r.get("contributor_employer") or "").upper().strip()
        for r in individual_receipts
        if (r.get("contributor_employer") or "").strip()
    })
    skip_employer_set = classify_employer_skips_batch(unique_employers)

    unique_memos = list({
        (r.get("memo_text") or "").upper().strip()
        for r in pac_receipts
        if (r.get("memo_text") or "").strip()
    })
    transfer_memo_set = classify_transfer_memos_batch(unique_memos)

    def _classify_fallback(name: str, name_upper: str) -> tuple[str, str]:
        """Embedding-based fallback for donors not in AI classifications."""
        dtype = classify_donor_type_semantic(
            name, candidate_name=candidate_name
        ) or "Org/Employees"
        industry, _ = classify_with_learning(name, db_session)
        return dtype, industry

    def _get_classification(name: str, name_upper: str) -> tuple[str, str, bool]:
        """Return (donor_type, industry, should_skip) from AI or fallback."""
        ai_class = ai_classifications.get(name_upper)
        if ai_class:
            if ai_class.get("skip"):
                return "SKIP", "OTHER", True
            return (
                ai_class.get("type", "Org/Employees"),
                ai_class.get("industry", "OTHER"),
                False,
            )
        if is_skip_entity(name_upper):
            return "SKIP", "OTHER", True
        dtype, industry = _classify_fallback(name, name_upper)
        return dtype, industry, False

    # 1a. Every committee that gave, from the bulk file.
    if pacs is not None:
        committees = detail.get("committees") or {}
        pac_skips = skip_entities_batch([committee_donor_name(committees.get(cid), cid).upper().strip() for cid in pacs])
        for cid, amount in pacs.items():
            meta = committees.get(cid)
            name = committee_donor_name(meta, cid)
            key = name.upper().strip()
            ai_class = ai_classifications.get(key) or {}
            if ai_class.get("skip") or key in pac_skips:
                continue
            political = is_political_committee(meta)
            industry = "POLITICAL" if political else (
                ai_class.get("industry") or classify_with_learning(name, db_session)[0]
            )
            existing = donor_map.setdefault(key, {
                "name": name, "total": 0, "type": committee_donor_type(meta), "industry": industry,
            })
            existing["total"] += amount
            existing["isCommittee"] = True
            if meta and meta.get("type") is not None:
                existing["committeeType"] = meta["type"]
            if meta and meta.get("connectedOrg"):
                existing["connectedOrg"] = meta["connectedOrg"]

    # 1b. PAC/committee receipts: the sampled rows, or with the bulk file in
    # hand only the candidate's own contributions it doesn't carry.
    for r in pac_receipts:
        if not _is_contribution_row(r):
            continue
        if pacs is not None and not _is_candidate_line(r):
            continue

        name = r.get("contributor_name") or ""
        if not name:
            committee = r.get("committee") or {}
            name = committee.get("name", "")
        if not name or name == "Unknown":
            continue

        name_upper = name.upper().strip()

        memo_upper = (r.get("memo_text") or "").upper().strip()
        if memo_upper and memo_upper in transfer_memo_set:
            continue

        if _is_candidate_line(r):
            # Line 11D is the candidate's own money by FEC definition —
            # no name heuristic needed.
            donor_type, industry, skip = "Self-Funded", "OTHER", False
        else:
            donor_type, industry, skip = _get_classification(name, name_upper)
        if skip:
            continue

        existing = donor_map.get(name_upper, {
            "name": name, "total": 0, "type": donor_type, "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        # The contributor is itself a committee (fec.committee_id_of: any of
        # the FEC's committee entity types, not only "COM").
        cid = committee_id_of(r)
        if cid:
            # A committee's own name is not how the lobbying registry lists
            # anyone (fetch/lda.py), so a search under it proves nothing.
            existing["isCommittee"] = True
            meta = committee_meta_map.get(cid)
            # Reported for every committee contributor; no score reads it
            # since v6.22 removed the PAC-cap utilization signal.
            if meta and meta.get("type") is not None:
                existing["committeeType"] = meta["type"]
            if meta and meta.get("connectedOrg"):
                existing["connectedOrg"] = meta["connectedOrg"]
            # Tier 1 (FEC structured metadata) outranks the name classifier:
            # the NRSC's name embeds near nothing political enough, and it
            # was headlining a senator's "gun industry" donor-vote match.
            if is_political_committee(meta) and existing.get("type") not in (
                "Self-Funded", "CandidateAffiliated", "SKIP",
            ):
                existing["industry"] = "POLITICAL"
        donor_map[name_upper] = existing

    # 2a. Employees, from the FEC's employer totals.
    if employers is not None:
        names = [r["employer"] for r in employers if r.get("employer")]
        skips = (
            classify_employer_skips_batch(names)
            | skip_entities_batch([n.upper().strip() for n in names])
            | _employer_values_not_organizations(employers, detail.get("occupations"))
        )
        for r in employers:
            employer = (r.get("employer") or "").upper().strip()
            if not employer or employer in skips:
                continue
            ai_class = ai_classifications.get(employer) or {}
            if ai_class.get("skip"):
                continue
            industry = ai_class.get("industry") or classify_with_learning(employer, db_session)[0]
            existing = donor_map.setdefault(employer, {
                "name": employer, "total": 0, "type": ai_class.get("type", "Org/Employees"), "industry": industry,
            })
            existing["total"] += r.get("total") or 0

    # 2b. Individual contributions grouped by employer (the sampled rows,
    # when the employer totals couldn't be read)
    for r in individual_receipts if employers is None else []:
        if not _is_contribution_row(r):
            continue
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in skip_employer_set or employer in MISSING_VALUE_TEXT:
            continue

        ai_class = ai_classifications.get(employer)
        if ai_class:
            donor_type = ai_class.get("type", "Org/Employees")
            industry = ai_class.get("industry", "OTHER")
        else:
            donor_type = "Org/Employees"
            industry, _ = classify_with_learning(r.get("contributor_employer", ""), db_session)

        existing = donor_map.get(employer, {
            "name": r.get("contributor_employer", ""),
            "total": 0, "type": donor_type, "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        if existing["type"] != "PAC":
            existing["type"] = donor_type
        donor_map[employer] = existing

    # 3. Aggregated contributors as fallback
    for c in aggregated_contributors:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue

        normalized_name = name.upper().strip()
        if normalized_name not in donor_map:
            donor_type, industry, skip = _get_classification(name, normalized_name)
            if skip:
                continue
            donor_map[normalized_name] = {
                "name": name,
                "total": c.get("total", 0) or 0,
                "type": donor_type,
                "industry": industry,
            }

    # The candidate's own money (self-loans recorded as "Lastname,
    # Firstname") is frequently mistyped Org/Employees by the semantic
    # classifier — the 2026-07 audit found 19 senators listed as their own
    # top donor ($19M for one). Deterministically reclassify so downstream
    # consumers (FI concentration, donor-vote matches) can exclude it.
    for d in donor_map.values():
        if d["type"] not in ("Self-Funded", "CandidateAffiliated") and \
                is_candidate_self_donor(d["name"], candidate_name):
            d["type"] = "Self-Funded"

    sorted_donors = sorted(donor_map.values(), key=lambda d: d["total"], reverse=True)
    return [
        {
            "name": _clean_donor_name(d["name"]),
            "total": round(d["total"]),
            "type": d["type"],
            "industry": d.get("industry", "OTHER"),
            "committeeType": d.get("committeeType"),
            "connectedOrg": d.get("connectedOrg"),
            "isCommittee": bool(d.get("isCommittee")),
        }
        for d in sorted_donors
        if d["total"] > 0 and len(d["name"].strip()) >= 3
        # Candidate-affiliated committees (the candidate's own campaign,
        # joint fundraising, and victory committees) are transfers, not
        # donors. Listing them made routine JFC users look captured by
        # their own committees in the concentration score and the UI.
        and d.get("type") != "CandidateAffiliated"
    ][:100]


def _clean_donor_name(name: str) -> str:
    """Convert FEC ALL CAPS names to title case, preserving acronyms."""
    if name == name.upper():
        acronyms = {"llc", "inc", "pac", "corp", "co", "ltd", "lp", "pllc"}
        words = name.lower().split()
        return " ".join(
            word.upper() if word in acronyms else word[0].upper() + word[1:]
            if word else word
            for word in words
        )
    return name


def _build_industry_breakdown(
    pac_receipts: list[dict],
    individual_receipts: list[dict],
    aggregated_contributors: list[dict],
    small_individual_total: float,
    large_individual_total: float,
    contribution_base: float,
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
    candidate_name: str = "",
    committee_meta_map: dict[str, dict] | None = None,
    detail: dict | None = None,
) -> list[dict]:
    """Build a funding breakdown showing all sources by industry.

    PAC money: every committee that gave (detail["pacs"], the FEC's bulk
    file), political by the FEC's registration or else the sponsor's
    industry. Itemized individual money: by the donor's stated occupation
    (detail["occupations"], transform/occupation_industry); an occupation
    the data gives no industry (a generic role, retired, not employed) stays
    in LARGE_INDIVIDUAL. Each part falls back to the sampled receipts when
    its source couldn't be read. Until 2026-10 only the samples existed, and
    under 1% of a large campaign's money was ever industry-classified.
    """
    industry_totals: dict[str, dict] = {}
    ai_classifications = ai_classifications or {}
    counted_donors: set[str] = set()
    detail = detail or {}
    pacs = detail.get("pacs")
    occupations = detail.get("occupations")

    def _add(industry: str, amount: float) -> None:
        existing = industry_totals.setdefault(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount

    if small_individual_total > 0:
        industry_totals["SMALL_DONORS"] = {
            "industry": "SMALL_DONORS", "name": "SMALL_DONORS",
            "total": small_individual_total,
        }

    # Pre-compute employer skip set for this breakdown (same approach as build_top_donors)
    unique_employers_bd = list({
        (r.get("contributor_employer") or "").upper().strip()
        for r in individual_receipts
        if (r.get("contributor_employer") or "").strip()
    })
    skip_employer_bd = classify_employer_skips_batch(unique_employers_bd)

    def _get_industry(name: str, name_upper: str) -> str:
        ai_class = ai_classifications.get(name_upper)
        if ai_class:
            return ai_class.get("industry", "OTHER")
        industry, _ = classify_with_learning(name, db_session)
        return industry

    def _should_skip_for_breakdown(name_upper: str) -> bool:
        ai_class = ai_classifications.get(name_upper)
        if ai_class and (
            ai_class.get("skip")
            or ai_class.get("type") in ("CandidateAffiliated", "Self-Funded")
        ):
            return True
        if is_skip_entity(name_upper):
            return True
        # The candidate's own money is not industry money — the semantic
        # classifier misses "Lastname, Firstname" self-loans.
        if candidate_name and is_candidate_self_donor(name_upper, candidate_name):
            return True
        return False

    if pacs is not None:
        committees = detail.get("committees") or {}
        for cid, amount in pacs.items():
            meta = committees.get(cid)
            name = committee_donor_name(meta, cid)
            key = name.upper().strip()
            if _should_skip_for_breakdown(key):
                continue
            _add("POLITICAL" if is_political_committee(meta) else _get_industry(name, key), amount)
            counted_donors.add(key)

    for r in pac_receipts if pacs is None else []:
        if not _is_contribution_row(r) or _is_candidate_line(r):
            continue
        org = r.get("contributor_name") or r.get("contributor_organization_name") or ""
        if not org:
            continue
        org_upper = org.upper().strip()

        if _should_skip_for_breakdown(org_upper):
            continue

        amount = r.get("contribution_receipt_amount", 0) or 0
        meta = (
            (committee_meta_map or {}).get(r.get("contributor_id") or "")
            if committee_id_of(r) else None
        )
        # Same tier-1 rule as build_top_donors: the FEC's registration, not
        # the name, decides that a party/candidate/leadership committee's
        # money is political rather than an industry's.
        industry = "POLITICAL" if is_political_committee(meta) else _get_industry(org, org_upper)

        existing = industry_totals.get(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount
        industry_totals[industry] = existing
        counted_donors.add(org_upper)

    classified_individual_total = 0.0
    if occupations is not None:
        stated = sum(r.get("total") or 0 for r in occupations)
        # The occupation totals are the FEC's own sum of the same itemized
        # contributions large_individual_total counts; scaled down only if
        # they ever run over it, so no dollar is counted twice.
        scale = min(1.0, large_individual_total / stated) if stated > 0 else 0.0
        for r in occupations:
            industry = industry_of_occupation(r.get("occupation"))
            if industry:
                amount = (r.get("total") or 0) * scale
                _add(industry, amount)
                classified_individual_total += amount

    employer_totals: dict[str, float] = {}
    for r in individual_receipts if occupations is None else []:
        if not _is_contribution_row(r):
            continue
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in skip_employer_bd or employer in MISSING_VALUE_TEXT:
            continue
        if _should_skip_for_breakdown(employer):
            continue
        employer_totals[employer] = employer_totals.get(employer, 0) + (
            r.get("contribution_receipt_amount", 0) or 0
        )

    for employer, amount in employer_totals.items():
        industry = _get_industry(employer, employer)

        existing = industry_totals.get(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount
        industry_totals[industry] = existing
        counted_donors.add(employer)
        classified_individual_total += amount

    unclassified_large = large_individual_total - classified_individual_total
    if unclassified_large > 1000:
        industry_totals["LARGE_INDIVIDUAL"] = {
            "industry": "LARGE_INDIVIDUAL", "name": "LARGE_INDIVIDUAL",
            "total": unclassified_large,
        }

    # The top contributors by name fill in only for the sampled path: with
    # the complete detail every dollar is already counted above.
    for c in aggregated_contributors if pacs is None and occupations is None else []:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue
        normalized_name = name.upper().strip()
        if normalized_name in counted_donors:
            continue
        if _should_skip_for_breakdown(normalized_name):
            continue

        amount = c.get("total", 0) or 0
        industry = _get_industry(name, normalized_name)

        existing = industry_totals.get(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount
        industry_totals[industry] = existing

    # Add an UNCLASSIFIED bucket for money not captured by any classification
    raw_total = sum(ind["total"] for ind in industry_totals.values())
    unclassified = contribution_base - raw_total
    if unclassified > contribution_base * 0.01:
        industry_totals["UNCLASSIFIED"] = {
            "industry": "UNCLASSIFIED", "name": "UNCLASSIFIED",
            "total": unclassified,
        }
        raw_total = contribution_base

    denom = raw_total if raw_total > 0 else 1
    breakdown = []
    for ind in industry_totals.values():
        total = round(ind["total"])
        percentage = round((ind["total"] / denom) * 100) if denom > 0 else 0
        if total > 0:
            breakdown.append({
                "industry": ind["industry"],
                "name": ind["industry"].replace("_", " "),
                "total": total,
                "percentage": percentage,
            })

    breakdown.sort(key=lambda x: x["total"], reverse=True)
    return breakdown[:20]
