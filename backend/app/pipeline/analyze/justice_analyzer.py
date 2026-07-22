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

    score = (1 − |own_rate − opp_rate|) × 100
    An absolute differential of 0 → 100 (agrees with both sides equally;
      votes uncorrelated with appointing party = maximally independent).
    An absolute differential of 1 → 0 (perfectly party-predictable in
      EITHER direction — a systematically counter-partisan justice is as
      predictable as a loyalist, and now scores accordingly rather than a
      spurious 100). All three bloc-based scores are shrunk toward the
      neutral 50 when backed by few cases (count-confidence).

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

# Blocs are derived from each justice's appointing president's party —
# a historical fact carried on the fetched data — never from hand-coded
# membership sets. A prior version hardcoded justice IDs and classified
# Roberts as "SWING" by name (pinning his bloc-based scores at a neutral
# 50); that was a per-person judgment inside the scoring path, removed
# 2026-07-04 under the no-hand-fed-inputs rule. A justice who behaves as
# the court's median now EARNS high independence/consistency from their
# observed cross-bloc voting instead of being granted neutrality.


def _expected_bloc(appointing_party: str) -> str:
    """Expected ideological bloc from the appointing president's party."""
    return appointing_party if appointing_party in ("R", "D") else ""


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


# A "vote" that isn't a real participation on the merits (recusal,
# non-participation) — excluded from pairwise agreement so a shared
# non-vote value can't read as two justices "agreeing", and so recusals
# don't sit in an agreement denominator.
_PARTICIPATION_VOTES = ("majority", "minority")

# Cases needed for a bloc-behavior rate to be trusted at full strength;
# below it the rate is shrunk toward the neutral 50 midpoint. Ports the
# senator-side count-confidence pattern (min(n/threshold, 1.0)). Without
# it, one cross-bloc vote in one split decision produced independence=100
# on a 30%-weighted dimension. ~two terms of non-unanimous cases.
_MIN_CASES_FULL_CONFIDENCE = 15


def _shrink_to_neutral(score: float, n: int, threshold: int = _MIN_CASES_FULL_CONFIDENCE) -> float:
    """Shrink a rate-derived score toward 50 when it rests on few cases."""
    if threshold <= 0:
        return score
    conf = min(n / threshold, 1.0)
    return score * conf + 50.0 * (1.0 - conf)


def analyze_justice_votes(
    justice_id: str,
    appointing_party: str,
    votes: list[dict],
    all_case_votes: dict[str, list[dict]],
    party_map: dict[str, str] | None = None,
) -> dict:
    """Compute ideological consistency scores for a single justice.

    Args:
        justice_id: Oyez identifier for the justice.
        appointing_party: "R" or "D" from appointing president.
        votes: List of this justice's vote records.
        all_case_votes: Map of case_id -> list of all justices' votes for that case.
        party_map: justice_id -> appointing party for every sitting
            justice; used to derive the comparison blocs from data.

    Returns:
        Dict with score_consistency, score_independence, score_bipartisan_agreement,
        score_judicial_restraint, and supporting statistics.
    """
    if not votes:
        return _empty_result()

    party_map = party_map or {}
    r_bloc = {jid for jid, p in party_map.items() if p == "R"}
    d_bloc = {jid for jid, p in party_map.items() if p == "D"}
    all_active = set(party_map)

    expected_bloc = _expected_bloc(appointing_party)

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
        own_bloc = r_bloc - {justice_id}
        opp_bloc = d_bloc
    elif expected_bloc == "D":
        own_bloc = d_bloc - {justice_id}
        opp_bloc = r_bloc
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

        # A recusal / non-participation on this justice's own side can't
        # meaningfully agree or disagree with anyone — skip the whole
        # pairwise pass for it rather than treat it as a merits vote.
        if this_side not in _PARTICIPATION_VOTES:
            continue

        # --- Pairwise agreement (all cases) + Fisher-weighted bloc rates ---
        for other in case_votes:
            oid = other["justice_id"]
            if oid == justice_id or oid not in all_active:
                continue
            # Skip a recused/non-participating other justice: without this,
            # two justices sharing a non-vote value compare as "agreeing",
            # and a recusal sits in the agreement denominator.
            if other["vote"] not in _PARTICIPATION_VOTES:
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
            own_in = [o for o in case_votes if o["justice_id"] in own_bloc]
            opp_in = [o for o in case_votes if o["justice_id"] in opp_bloc]
            # Only count as a split decision when opposing-bloc justices were
            # seated for the case — recusals would otherwise inflate the
            # denominator and deflate independence scores spuriously.
            if opp_in:
                split_decisions += 1
                own_same = sum(1 for o in own_in if o["vote"] == this_side)
                opp_same = sum(1 for o in opp_in if o["vote"] == this_side)

                own_aligned = len(own_in) > 0 and own_same >= len(own_in) * 0.5
                opp_aligned = opp_same >= len(opp_in) * 0.5

                if opp_aligned and not own_aligned:
                    cross_bloc_count += 1

    # --- Score: Consistency (Ideological Independence) ---
    # ABSOLUTE agreement-rate differential weighted by Fisher information.
    # |differential| = 0 → score 100 (agrees with both sides equally: votes
    #   are uncorrelated with appointing party — maximally independent).
    # |differential| = 1 → score 0 (perfectly party-predictable).
    #
    # The differential is now absolute (2026-07 fix): it used to be
    # max(0, own_rate - opp_rate), which clamped the case where a justice
    # agrees MORE with the OPPOSING bloc to 0 → consistency 100 — scoring a
    # systematically counter-partisan justice identically to a genuinely
    # balanced one and hiding the inversion. Party-predictability in EITHER
    # direction is a lack of independence from party, so both tails now
    # lower the score.
    non_unanimous = total - unanimous_count
    if own_weighted_total > 0 and opp_weighted_total > 0 and expected_bloc:
        own_rate = own_weighted_agree / own_weighted_total
        opp_rate = opp_weighted_agree / opp_weighted_total
        differential = abs(own_rate - opp_rate)
        raw_consistency = max(0.0, min(100.0, (1.0 - differential) * 100))
        consistency = _shrink_to_neutral(raw_consistency, non_unanimous)
    else:
        own_rate = None
        opp_rate = None
        differential = None
        consistency = 50.0

    # --- Score: Independence ---
    # Cross-bloc voting rate across all split decisions.
    # A 50% cross-bloc rate → score 100 (truly case-by-case), shrunk toward
    # neutral when few split decisions back it (2026-07).
    if split_decisions > 0:
        raw_independence = min(100.0, (cross_bloc_count / split_decisions) * 200)
        independence = _shrink_to_neutral(raw_independence, split_decisions)
    else:
        independence = 50.0

    # --- Score: Bipartisan Agreement ---
    # Average pairwise agreement with opposing-bloc justices across ALL cases
    # (unanimous + split).  Produces differentiated scores per justice.
    # Case-weighted bipartisan rate: a justice who appeared in 200 cases with
    # Kagan gets 200 votes, not equal weight with a 3-case pairing against Jackson.
    bipartisan_total_cases = sum(agreement_totals.get(oid, 0) for oid in opp_bloc)
    bipartisan_total_agree = sum(agreement_counts.get(oid, 0) for oid in opp_bloc)
    if bipartisan_total_cases > 0:
        raw_bipartisan = bipartisan_total_agree / bipartisan_total_cases * 100
        # Shrink on the number of CASES the justice sat in (not pairings)
        # so a justice with only a handful of shared cases isn't scored at
        # full confidence off a tiny sample (2026-07).
        bipartisan = _shrink_to_neutral(raw_bipartisan, total)
    else:
        bipartisan = 50.0

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
    # Clamp authored dissent rate to actual dissent rate: opinion_type and vote
    # fields can diverge in source data (e.g. partial dissents), so authored
    # dissents can't meaningfully exceed cases where the justice voted minority.
    authored_dissent_rate = min(authored_dissent / total, dissent_rate) if total > 0 else 0.0

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
    for oid in all_active:
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
        # Breakdown math — the intermediate values behind each score above,
        # surfaced for the on-demand "show the math" score-breakdown panel.
        # Not used by any scoring path; purely explanatory.
        "breakdown": {
            "consistency": {
                "own_bloc_agreement_rate": round(own_rate, 3) if own_rate is not None else None,
                "opposing_bloc_agreement_rate": round(opp_rate, 3) if opp_rate is not None else None,
                "differential": round(differential, 3) if differential is not None else None,
                "detail": (
                    f"agrees with own bloc {own_rate:.1%} of (Fisher-weighted) cases vs. "
                    f"opposing bloc {opp_rate:.1%} — differential {differential:.1%}"
                    if own_rate is not None
                    else "no expected bloc or no weighted cases available — neutral 50"
                ),
            },
            "independence": {
                "cross_bloc_count": cross_bloc_count,
                "split_decisions": split_decisions,
                "detail": (
                    f"voted cross-bloc in {cross_bloc_count} of {split_decisions} split decisions "
                    f"({cross_bloc_pct:.1f}%, scaled ×2 so a 50% cross-bloc rate = 100)"
                    if split_decisions > 0
                    else "no split decisions with an opposing bloc seated — neutral 50"
                ),
            },
            "bipartisan_agreement": {
                "total_agree": bipartisan_total_agree,
                "total_cases": bipartisan_total_cases,
                "detail": (
                    f"agreed with opposing-bloc justices in {bipartisan_total_agree} of "
                    f"{bipartisan_total_cases} case-pairings (case-weighted, all cases incl. unanimous)"
                    if bipartisan_total_cases > 0
                    else "no opposing-bloc case pairings available — neutral 50"
                ),
            },
            "judicial_restraint": {
                "dissent_rate": round(dissent_rate, 3),
                "dissent_score": round(dissent_score, 1),
                "authored_dissent_rate": round(authored_dissent_rate, 3),
                "authored_penalty": round(authored_penalty, 1),
                "detail": (
                    f"dissent rate {dissent_rate:.1%} → curve score {dissent_score:.1f}, "
                    f"authored-dissent rate {authored_dissent_rate:.1%} → "
                    f"penalty -{authored_penalty:.1f}"
                ),
            },
        },
    }


def _empty_result() -> dict:
    return {
        "score_consistency": 50.0,
        "score_independence": 50.0,
        "score_bipartisan_agreement": 50.0,
        "score_judicial_restraint": 50.0,
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
        "breakdown": {
            "consistency": {"detail": "no vote data available — neutral 50"},
            "independence": {"detail": "no vote data available — neutral 50"},
            "bipartisan_agreement": {"detail": "no vote data available — neutral 50"},
            "judicial_restraint": {"detail": "no vote data available — neutral 50"},
        },
    }
