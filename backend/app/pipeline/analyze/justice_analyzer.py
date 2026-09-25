"""Analyze Supreme Court justice voting patterns for ideological consistency.

Scoring methodology adapted from quantitative judicial politics literature:

Consistency (Ideological Independence)
    Measures how much a justice's votes are predictable from their appointing
    party's ideology.  Uses the *agreement-rate differential* between own-bloc
    and opposing-bloc justices, weighted by the variance of each case's
    split, p·(1−p) with p = minority_votes / total_votes. Under a logistic
    response model that is the Fisher information one vote carries about the
    logit, so close decisions (5-4) weigh roughly 2.5× lopsided ones (8-1).
    It is a heuristic weight: Martin & Quinn (2002) estimate each case's
    discrimination as a parameter, and this does not.

    score = (1 − |own_rate − opp_rate|) × 100
    An absolute differential of 0 → 100 (agrees with both sides equally;
      votes uncorrelated with appointing party = maximally independent).
    An absolute differential of 1 → 0 (perfectly party-predictable in
      EITHER direction — a systematically counter-partisan justice is as
      predictable as a loyalist, and now scores accordingly rather than a
      spurious 100). Both bloc-based scores are shrunk toward the
      neutral 50 when backed by few cases (count-confidence).

Independence
    Continuous cross-bloc credit averaged across all non-unanimous decisions:
    per case, (fraction of the opposing bloc voting this justice's side) ×
    (1 − fraction of their own bloc voting this justice's side). Scaled so an
    average credit of 50% = 100.

    2026-07 fix: this used to threshold each case into a binary "did ≥50% of
    the opposing bloc join me AND <50% of my own bloc" event before counting
    a rate — mathematically biased by bloc size, since "<50% of own bloc"
    is a coarse, nearly all-or-nothing bar for a small bloc (e.g. needing
    BOTH of your only two bloc-mates to disagree with you) versus an easy,
    finer-grained bar for a large bloc (needing only a bare few of many
    peers to disagree). Confirmed via simulation: giving every justice on a
    6-vs-3 court an IDENTICAL individual defection rate still produced an
    8–10 point systematic independence gap favoring the larger bloc under
    the old threshold — gone once the per-case credit is continuous instead
    of booleanized before averaging.

Removed in v6.13 (docs/research/justice-scores.md; reproduce with
scripts/research_justice_scores.py — the Rehnquist Court vote matrix,
1994-2004, from Spaeth's Supreme Court Database via MCMCpack, run through
this module, plus a simulation of a 6-3 Court):

  Judicial Restraint — dissent frequency against a hand-set curve. It was
    the dissent rate with the sign flipped (Spearman -1.00 across 99
    justice-terms), and dissent rate is distance from the Court's median
    justice (rho 0.83; the median justice dissented in 17.7% of split
    cases vs 33.7% for the rest) — where a justice sits relative to the
    Court's current composition, not restraint (which the literature
    measures as deference to the elected branches). Under symmetric
    partisanship on a 6-3 Court, it scored the 3-member bloc 21 points
    lower than the 6-member bloc for being outvoted — on today's Court, a
    structural penalty on one party's appointees. Its cited calibration
    did not hold up: Haynie (1992) is about Chief Justices and the Court's
    historical consensus norm and sets no "authored dissents above 8%"
    threshold.
  Bipartisan Agreement — pairwise agreement with opposing-bloc justices.
    Spearman 0.86 with Independence (same construct) and it also averaged
    in unanimous cases, which carry no information about partisanship
    (split-variance weight 0). Its weight was folded into Independence.

Both remaining bloc measures showed no bloc-size bias in the simulation
(gaps within 2 points between a 6- and a 3-member bloc, with and without
informative party), and they measure different things (Spearman 0.19).

Academic references:
  • Segal & Cover (1989) — pre-confirmation ideology from editorials
  • Martin & Quinn (2002) — Bayesian ideal-point model from voting data
  • Martin, Quinn & Epstein (2005) — the median justice sits in the majority,
    which is why dissent rate tracks distance from the Court's median
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

    Returns 0 for unanimous decisions, ~0.25 for 5-4 splits — the
    Bernoulli variance of the split, i.e. the Fisher information about the
    logit in a logistic response model (see the module docstring).
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
        Dict with score_consistency, score_independence, and supporting
        statistics.
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

    cross_bloc_count = 0  # informational diagnostic only — see cross_bloc_pct
    cross_bloc_credit_sum = 0.0  # drives score_independence, see formula below
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
            # Recusals excluded from both blocs here too (same reasoning as
            # the pairwise pass above) — an unfiltered recusal would count
            # as "disagreeing" with this justice's side, inflating this
            # justice's apparent independence for a case where the other
            # justice simply didn't participate.
            own_in = [o for o in case_votes if o["justice_id"] in own_bloc and o["vote"] in _PARTICIPATION_VOTES]
            opp_in = [o for o in case_votes if o["justice_id"] in opp_bloc and o["vote"] in _PARTICIPATION_VOTES]
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

                # Continuous credit, not a booleanized threshold (2026-07
                # fix, see module docstring) — own_frac/opp_frac are
                # per-case fractions, so a small own bloc's coarse 0/0.5/1
                # granularity no longer gets forced through a binary cutoff
                # before being averaged across cases.
                own_frac = (own_same / len(own_in)) if own_in else 0.0
                opp_frac = opp_same / len(opp_in)
                cross_bloc_credit_sum += opp_frac * (1.0 - own_frac)

    # --- Score: Consistency (Ideological Independence) ---
    # ABSOLUTE agreement-rate differential weighted by split variance.
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
    # Average continuous cross-bloc credit across all split decisions.
    # An average credit of 50% → score 100, shrunk toward neutral when few
    # split decisions back it (2026-07) — see module docstring for why this
    # is a continuous per-case credit, not a booleanized threshold.
    if split_decisions > 0:
        raw_independence = min(100.0, (cross_bloc_credit_sum / split_decisions) * 200)
        independence = _shrink_to_neutral(raw_independence, split_decisions)
    else:
        independence = 50.0

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
                "avg_cross_bloc_credit": (
                    round(cross_bloc_credit_sum / split_decisions, 3) if split_decisions > 0 else None
                ),
                "detail": (
                    f"averaged {cross_bloc_credit_sum / split_decisions:.1%} continuous cross-bloc credit "
                    f"across {split_decisions} split decisions (full cross-bloc events: {cross_bloc_count}, "
                    f"{cross_bloc_pct:.1f}%) — scaled ×2 so a 50% average credit = 100"
                    if split_decisions > 0
                    else "no split decisions with an opposing bloc seated — neutral 50"
                ),
            },
        },
    }


def _empty_result() -> dict:
    return {
        "score_consistency": 50.0,
        "score_independence": 50.0,
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
        },
    }
