"""Analyze Supreme Court justice voting patterns for ideological consistency.

Scoring methodology adapted from quantitative judicial politics literature:

Consistency (Ideological Independence)
    Measures how much a justice's votes are predictable from their appointing
    party's ideology.  Uses the *agreement-rate differential* between own-bloc
    and opposing-bloc justices, weighted by each case's Fisher information
    content — the variance of a Bernoulli with p = minority_votes / total_votes.
    Close decisions (5-4) contribute roughly 2.5× more per case than lopsided
    ones (8-1), matching the IRT insight that a case's discrimination is highest
    near the ideological center of the court (Martin & Quinn, 2002).

    score = (1 − (own_rate − opp_rate)) × 100
    A differential of 0 → 100 (equal agreement with both sides).
    A differential of 1 → 0   (only agrees with own side).

Independence
    Cross-bloc voting rate across all non-unanimous decisions — how often the
    justice votes with ≥50 % of the opposing bloc while <50 % of their own
    bloc is on the same side.  Scaled so that a 50 % cross-bloc rate = 100.

Bipartisan Agreement
    Average pairwise agreement rate with opposing-bloc justices across ALL
    cases (including unanimous).  Unanimous decisions raise every justice's
    baseline; split decisions differentiate them.

Judicial Restraint
    Dissent behaviour.  Moderate dissent rates and rare solo dissents suggest
    measured disagreement rather than ideological grandstanding.

Academic references:
  • Segal & Cover (1989) — pre-confirmation ideology from editorials
  • Martin & Quinn (2002) — Bayesian ideal-point model from voting data
  • Epstein, Landes & Posner (2013) — agreement rates and coalition analysis
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Court composition as of the 2023-2025 terms.  Update when membership
# changes.  Bloc assignments reflect appointing-party ideology, not
# observed voting — they are INPUTS to the scoring model, not outputs.
# Roberts is classified as SWING because empirical data consistently
# shows him as the court's median justice (Epstein & Jacobi 2010).
R_BLOC = {"clarence_thomas", "samuel_a_alito_jr", "neil_gorsuch", "brett_m_kavanaugh", "amy_coney_barrett"}
D_BLOC = {"sonia_sotomayor", "elena_kagan", "ketanji_brown_jackson"}
SWING = {"john_g_roberts_jr"}

ALL_ACTIVE = R_BLOC | D_BLOC | SWING


def _expected_bloc(justice_id: str, appointing_party: str) -> str:
    """Determine expected ideological bloc from appointing president's party."""
    if justice_id in R_BLOC:
        return "R"
    if justice_id in D_BLOC:
        return "D"
    if appointing_party in ("R", "D"):
        return appointing_party
    return ""


def _fisher_weight(majority_votes: int, minority_votes: int) -> float:
    """Case information weight: p·(1−p) where p = minority/total.

    Returns 0 for unanimous decisions, ~0.25 for 5-4 splits.
    This is the Fisher information for a binary response model — the
    standard IRT measure of how much a case reveals about ideology.
    """
    total = majority_votes + minority_votes
    if total <= 0:
        return 0.0
    p = minority_votes / total
    return p * (1.0 - p)


def analyze_justice_votes(
    justice_id: str,
    appointing_party: str,
    votes: list[dict],
    all_case_votes: dict[str, list[dict]],
) -> dict:
    """Compute ideological consistency scores for a single justice.

    Args:
        justice_id: Oyez identifier for the justice.
        appointing_party: "R" or "D" from appointing president.
        votes: List of this justice's vote records.
        all_case_votes: Map of case_id -> list of all justices' votes for that case.

    Returns:
        Dict with score_consistency, score_independence, score_bipartisan_agreement,
        score_judicial_restraint, and supporting statistics.
    """
    if not votes:
        return _empty_result()

    expected_bloc = _expected_bloc(justice_id, appointing_party)

    total = len(votes)
    majority_count = sum(1 for v in votes if v["vote"] == "majority")
    minority_count = sum(1 for v in votes if v["vote"] == "minority")
    unanimous_count = sum(1 for v in votes if v.get("is_unanimous"))

    authored_majority = sum(1 for v in votes if v["opinion_type"] == "majority")
    authored_dissent = sum(1 for v in votes if v["opinion_type"] == "dissent")
    authored_concurrence = sum(1 for v in votes if v["opinion_type"] == "concurrence")

    close_votes = [v for v in votes if v.get("is_close")]
    close_majority = sum(1 for v in close_votes if v["vote"] == "majority") if close_votes else 0

    if expected_bloc == "R":
        own_bloc = R_BLOC - {justice_id}
        opp_bloc = D_BLOC
    elif expected_bloc == "D":
        own_bloc = D_BLOC - {justice_id}
        opp_bloc = R_BLOC
    else:
        own_bloc = set()
        opp_bloc = set()

    # --- Accumulators ---
    agreement_counts: dict[str, int] = defaultdict(int)
    agreement_totals: dict[str, int] = defaultdict(int)

    own_weighted_agree = 0.0
    own_weighted_total = 0.0
    opp_weighted_agree = 0.0
    opp_weighted_total = 0.0

    cross_bloc_count = 0
    split_decisions = 0

    for v in votes:
        case_id = v["case_id"]
        case_votes = all_case_votes.get(case_id, [])
        this_side = v["vote"]
        is_unanimous = v.get("is_unanimous", False)

        w = _fisher_weight(v["majority_votes"], v["minority_votes"])

        # --- Pairwise agreement (all cases) + Fisher-weighted bloc rates ---
        for other in case_votes:
            oid = other["justice_id"]
            if oid == justice_id or oid not in ALL_ACTIVE:
                continue

            same_side = other["vote"] == this_side
            agreement_totals[oid] += 1
            if same_side:
                agreement_counts[oid] += 1

            if w > 0:
                agree_val = 1.0 if same_side else 0.0
                if oid in own_bloc:
                    own_weighted_agree += w * agree_val
                    own_weighted_total += w
                elif oid in opp_bloc:
                    opp_weighted_agree += w * agree_val
                    opp_weighted_total += w

        # --- Cross-bloc tracking (non-unanimous only) ---
        if not is_unanimous and expected_bloc:
            split_decisions += 1

            own_in = [o for o in case_votes if o["justice_id"] in own_bloc]
            opp_in = [o for o in case_votes if o["justice_id"] in opp_bloc]
            own_same = sum(1 for o in own_in if o["vote"] == this_side)
            opp_same = sum(1 for o in opp_in if o["vote"] == this_side)

            own_aligned = len(own_in) > 0 and own_same >= len(own_in) * 0.5
            opp_aligned = len(opp_in) > 0 and opp_same >= len(opp_in) * 0.5

            if opp_aligned and not own_aligned:
                cross_bloc_count += 1

    # --- Score: Consistency (Ideological Independence) ---
    # Agreement-rate differential weighted by Fisher information.
    # Differential = 0 → score 100 (equal agreement with both sides).
    # Differential = 1 → score 0   (only agrees with own side).
    if own_weighted_total > 0 and opp_weighted_total > 0 and expected_bloc:
        own_rate = own_weighted_agree / own_weighted_total
        opp_rate = opp_weighted_agree / opp_weighted_total
        differential = max(0.0, own_rate - opp_rate)
        consistency = max(0.0, min(100.0, (1.0 - differential) * 100))
    else:
        consistency = 50.0

    # --- Score: Independence ---
    # Cross-bloc voting rate across all split decisions.
    # A 50% cross-bloc rate → score 100 (truly case-by-case).
    if split_decisions > 0:
        independence = min(100.0, (cross_bloc_count / split_decisions) * 200)
    else:
        independence = 50.0

    # --- Score: Bipartisan Agreement ---
    # Average pairwise agreement with opposing-bloc justices across ALL cases
    # (unanimous + split).  Produces differentiated scores per justice.
    bipartisan_rates: list[float] = []
    for oid in opp_bloc:
        t = agreement_totals.get(oid, 0)
        if t > 0:
            bipartisan_rates.append(agreement_counts.get(oid, 0) / t)
    bipartisan = (
        (sum(bipartisan_rates) / len(bipartisan_rates) * 100)
        if bipartisan_rates
        else 50.0
    )

    # --- Score: Judicial Restraint ---
    dissent_rate = minority_count / total if total > 0 else 0.0
    restraint = 100.0
    if dissent_rate > 0.4:
        restraint -= (dissent_rate - 0.4) * 200
    elif dissent_rate < 0.05:
        restraint -= 10
    authored_dissent_rate = authored_dissent / total if total > 0 else 0.0
    if authored_dissent_rate > 0.15:
        restraint -= (authored_dissent_rate - 0.15) * 100
    restraint = max(0.0, min(100.0, restraint))

    # --- Agreement matrix ---
    agreement_matrix: dict[str, float] = {}
    for oid in ALL_ACTIVE:
        if oid == justice_id:
            continue
        t = agreement_totals.get(oid, 0)
        if t > 0:
            agreement_matrix[oid] = round(agreement_counts.get(oid, 0) / t * 100, 1)

    cross_bloc_pct = (cross_bloc_count / split_decisions * 100) if split_decisions > 0 else 0.0

    return {
        "score_consistency": round(consistency, 1),
        "score_independence": round(independence, 1),
        "score_bipartisan_agreement": round(bipartisan, 1),
        "score_judicial_restraint": round(restraint, 1),
        "cases_decided": total,
        "majority_pct": round(majority_count / total * 100, 1) if total else 0.0,
        "dissent_pct": round(minority_count / total * 100, 1) if total else 0.0,
        "unanimous_pct": round(unanimous_count / total * 100, 1) if total else 0.0,
        "authored_majority": authored_majority,
        "authored_dissent": authored_dissent,
        "authored_concurrence": authored_concurrence,
        "close_case_majority_pct": round(close_majority / len(close_votes) * 100, 1) if close_votes else 0.0,
        "cross_bloc_pct": round(cross_bloc_pct, 1),
        "agreement_matrix": agreement_matrix,
    }


def _empty_result() -> dict:
    return {
        "score_consistency": 0.0,
        "score_independence": 0.0,
        "score_bipartisan_agreement": 0.0,
        "score_judicial_restraint": 0.0,
        "cases_decided": 0,
        "majority_pct": 0.0,
        "dissent_pct": 0.0,
        "unanimous_pct": 0.0,
        "authored_majority": 0,
        "authored_dissent": 0,
        "authored_concurrence": 0,
        "close_case_majority_pct": 0.0,
        "cross_bloc_pct": 0.0,
        "agreement_matrix": {},
    }
