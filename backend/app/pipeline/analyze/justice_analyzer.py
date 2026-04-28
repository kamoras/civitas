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
    Dissent behaviour scored against empirically documented historical norms.
    Uses a continuous curve rather than binary threshold penalties:

      - Optimal dissent range 8–15%: scores 100.  The lower bound follows
        Caldeira & Zorn (1998) who argue some dissent validates the review
        process; the upper bound equals the historical mean documented by
        Epstein, Landes & Posner (2013).
      - Below 2%: mild conformist penalty (score 80), per Caldeira & Zorn
        (1998) who note near-zero dissent signals strategic conformism.
      - 2–8%: rising linearly from 80 to 100.
      - 15–25%: linear decline from 100 to 70 (within ≈1.3 SD of mean per
        ELP 2013 historical SD ≈ 8%).
      - Above 25%: steeper decline, floor 0.

    An authored-dissent penalty activates above 8% authored dissents
    (Haynie 1992), reflecting a distinction between joining dissents
    (principled disagreement) and authoring them (vocal ideology
    signaling).

Academic references:
  • Segal & Cover (1989) — pre-confirmation ideology from editorials
  • Martin & Quinn (2002) — Bayesian ideal-point model from voting data
  • Epstein, Landes & Posner (2013) — agreement rates, coalition analysis,
    and historical dissent rate distribution (mean ≈15%, SD ≈8%)
  • Caldeira & Zorn (1998) — consensual norms; some dissent validates review
  • Haynie (1992) — authored dissent above ≈8% signals vocal ideology
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
    #
    # Scored against empirically documented historical norms rather than
    # hard binary thresholds.  Sources:
    #
    #   Epstein, L., Landes, W.M., & Posner, R.A. (2013). The Behavior of
    #     Federal Judges. Harvard UP.  — Historical dissent mean ≈15%, SD ≈8%.
    #   Caldeira, G.A., & Zorn, C.J.W. (1998). Of Time and Consensual Norms
    #     in the United States Supreme Court. AJPS 42(3), 874–902.
    #     — Near-zero dissent signals strategic conformism, not restraint.
    #   Haynie, S.L. (1992). Leadership and Consensus on the U.S. Supreme
    #     Court. Journal of Politics 54(4), 1158–1169.
    #     — Authored dissents above ≈8% of cases signal vocal disagreement.
    #
    # Constants derived from the above:
    #   DISSENT_HIST_MEAN = 0.15   (ELP 2013 Table 4 historical mean)
    #   DISSENT_MIN       = 0.02   (Caldeira & Zorn 1998: minimum healthy)
    #   DISSENT_OPT_LOW   = 0.08   (lower bound of restrained range,
    #                                midpoint between DISSENT_MIN and DISSENT_HIST_MEAN)
    #   DISSENT_ELEV      = 0.25   (≈ mean + 1.25 SD per ELP 2013, "notably elevated")
    #   AUTHORED_NORM     = 0.08   (Haynie 1992: authored dissents >8% = vocal)
    #   AUTHORED_SCALE    = 150.0  (maps a 7-point excess above 8% to ≈10-pt penalty)
    DISSENT_MIN     = 0.02
    DISSENT_OPT_LOW = 0.08
    DISSENT_OPT_HIGH = 0.15   # historical mean (ELP 2013)
    DISSENT_ELEV    = 0.25
    AUTHORED_NORM   = 0.08
    AUTHORED_SCALE  = 150.0

    dissent_rate = minority_count / total if total > 0 else 0.0
    authored_dissent_rate = authored_dissent / total if total > 0 else 0.0

    if dissent_rate < DISSENT_MIN:
        # Near-zero: slight conformist penalty per Caldeira & Zorn (1998)
        dissent_score = 80.0
    elif dissent_rate <= DISSENT_OPT_LOW:
        # Rising 80→100 as dissent reaches lower bound of optimal range
        dissent_score = 80.0 + (
            (dissent_rate - DISSENT_MIN) / (DISSENT_OPT_LOW - DISSENT_MIN)
        ) * 20.0
    elif dissent_rate <= DISSENT_OPT_HIGH:
        # Plateau at 100 within the historically restrained range
        dissent_score = 100.0
    elif dissent_rate <= DISSENT_ELEV:
        # Linear decline 100→70 between historical mean and elevated threshold
        dissent_score = 100.0 - (
            (dissent_rate - DISSENT_OPT_HIGH) / (DISSENT_ELEV - DISSENT_OPT_HIGH)
        ) * 30.0
    else:
        # Steeper decline beyond notably elevated threshold
        dissent_score = max(0.0, 70.0 - (dissent_rate - DISSENT_ELEV) / 0.35 * 70.0)

    # Authored-dissent penalty: activates above Haynie (1992) norm
    authored_penalty = max(
        0.0,
        (authored_dissent_rate - AUTHORED_NORM) * AUTHORED_SCALE,
    )

    restraint = max(0.0, min(100.0, dissent_score - authored_penalty))

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
