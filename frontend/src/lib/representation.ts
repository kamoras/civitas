// Score-to-presentation helpers only. Overall scores are computed by the
// backend (score_calculator.compute_overall_score / president_scorer.
// compute_president_overall_score / justice_service._build_score) and sent
// as the `overall` field on each score breakdown — the frontend must never
// recompute a weighted sum from sub-scores itself (see git history for the
// bug this caused: a hardcoded fallback-weights object here went stale
// after a backend scoring-dimension merge and silently mis-weighted scores
// until /api/config loaded).

// Same reasoning as getScoreLabel's bottom band: the measure is how
// consistently a justice votes with an ideological bloc, which is a degree,
// not a motive. "DEEPLY PARTISAN" named the motive.
export function getJusticeLabel(score: number): string {
  if (score >= 75) return "HIGHLY CONSISTENT";
  if (score >= 55) return "MODERATELY CONSISTENT";
  if (score >= 35) return "IDEOLOGICALLY PREDICTABLE";
  return "HIGHLY PREDICTABLE";
}

// Labels describe how well a member represents their constituents, not
// general virtue — "GOOD" previously read as a moral judgment on the
// politician rather than what the score actually measures (see the north
// star note in score_calculator.py: scores measure representation, not
// intrinsic goodness).
//
// The bottom band was "DEEPLY CAPTURED" until it got the same treatment for
// the same reason. That label had two problems the rest of the ladder does
// not. It asserts a MECHANISM — capture, by donors or by an industry — where
// the score only measures an outcome, and nothing in the three scored
// dimensions can distinguish a captured member from one who is simply
// ineffective, thinly documented, or out of step with their state. And it is
// the one rung that editorialises: every other label states a degree of
// representation and lets the reader draw the conclusion.
//
// "NOT REPRESENTATIVE" completes the same ladder in the same vocabulary and
// makes no claim the evidence cannot carry. The number is unchanged and the
// colour tier is unchanged; only the accusation is gone.
export function getScoreLabel(score: number): string {
  if (score >= 81) return "STRONGLY REPRESENTATIVE";
  if (score >= 61) return "REPRESENTATIVE";
  if (score >= 41) return "MIXED REPRESENTATION";
  if (score >= 21) return "WEAKLY REPRESENTATIVE";
  return "NOT REPRESENTATIVE";
}

// Presidential scores measure performance in office, not constituent
// representation, so the senator "REPRESENTATIVE / CAPTURED" ladder is the
// wrong vocabulary — a low-scoring president was being labelled "DEEPLY
// CAPTURED" and a high one "STRONGLY REPRESENTATIVE", neither of which is
// what the presidential score means. Same numeric tiers as getScoreColor.
export function getPresidentLabel(score: number): string {
  if (score >= 81) return "STRONG PERFORMANCE";
  if (score >= 61) return "EFFECTIVE";
  if (score >= 41) return "MIXED PERFORMANCE";
  if (score >= 21) return "WEAK PERFORMANCE";
  return "FAILING";
}

export function getScoreColor(score: number): string {
  if (score >= 81) return "text-phos";
  if (score >= 61) return "text-signal-cyan";
  if (score >= 41) return "text-signal-amber";
  if (score >= 21) return "text-signal-orange";
  return "text-signal-red";
}

// bg-* counterpart of getScoreColor, for progress-bar fills — same
// thresholds/colors so a score renders identically whether it's shown as
// text or a bar, on any page. Several per-metric bars (senator/president/
// justice detail pages, the leaderboard) previously reimplemented this
// with drifting cutoffs (70/40, 70/50/35, 75/55/35/15...) and drifting
// colors (bg-cyan-400 instead of the site's actual neon-cyan), so the
// same score could render a different tier depending on which page you
// were on.
export function getScoreBgColor(score: number): string {
  if (score >= 81) return "bg-phos";
  if (score >= 61) return "bg-signal-cyan";
  if (score >= 41) return "bg-signal-amber";
  if (score >= 21) return "bg-signal-orange";
  return "bg-signal-red";
}

// ASCII progress bar for a 0-100 score in the terminal aesthetic: 20 cells,
// each worth 5 points. The compare view and the per-metric RepresentationScore
// bar computed this identically inline before this extraction.
export function asciiScoreBar(score: number): string {
  const filled = Math.round(score / 5);
  const empty = 20 - filled;
  return "█".repeat(filled) + "░".repeat(empty);
}
