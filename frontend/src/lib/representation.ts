// Score-to-presentation helpers only. Overall scores are computed by the
// backend (score_calculator.compute_overall_score / president_scorer.
// compute_president_overall_score / justice_service._build_score) and sent
// as the `overall` field on each score breakdown — the frontend must never
// recompute a weighted sum from sub-scores itself (see git history for the
// bug this caused: a hardcoded fallback-weights object here went stale
// after a backend scoring-dimension merge and silently mis-weighted scores
// until /api/config loaded).

export function getJusticeLabel(score: number): string {
  if (score >= 75) return "HIGHLY CONSISTENT";
  if (score >= 55) return "MODERATELY CONSISTENT";
  if (score >= 35) return "IDEOLOGICALLY PREDICTABLE";
  return "DEEPLY PARTISAN";
}

// Labels describe how well a member represents their constituents, not
// general virtue — "GOOD" previously read as a moral judgment on the
// politician rather than what the score actually measures (see the north
// star note in score_calculator.py: scores measure representation, not
// intrinsic goodness).
export function getScoreLabel(score: number): string {
  if (score >= 81) return "STRONGLY REPRESENTATIVE";
  if (score >= 61) return "REPRESENTATIVE";
  if (score >= 41) return "MIXED REPRESENTATION";
  if (score >= 21) return "WEAKLY REPRESENTATIVE";
  return "DEEPLY CAPTURED";
}

export function getScoreColor(score: number): string {
  if (score >= 81) return "text-matrix-green";
  if (score >= 61) return "text-neon-cyan";
  if (score >= 41) return "text-yellow-500";
  if (score >= 21) return "text-orange-500";
  return "text-red-500";
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
  if (score >= 81) return "bg-matrix-green";
  if (score >= 61) return "bg-neon-cyan";
  if (score >= 41) return "bg-yellow-500";
  if (score >= 21) return "bg-orange-500";
  return "bg-red-500";
}
