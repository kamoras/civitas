/**
 * Shared party (D/R/I) styling constants — Tailwind class names keyed by
 * party code. Copy-pasted verbatim across ~6 components before this
 * extraction (ActionPreview, ElectionsTab, MyRepsTab, SenatorCard,
 * app/action, app/compare) — a single source of truth here means a color
 * or opacity change can't silently drift out of sync between them.
 *
 * President/Justice cards use a different shape (no Independent option,
 * different label text, different opacity values) and are left as their
 * own local constants rather than forced into this one.
 *
 * Record<string, string>, not keyed by a "D"|"R"|"I" literal union: most
 * call sites index with a plain `string`-typed party field (e.g.
 * senator.party), so a stricter key type would just force a cast at
 * every call site for no real safety gain.
 */

export const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-signal-red",
  I: "text-ind-purple",
};

export const PARTY_BORDER: Record<string, string> = {
  D: "border-dem-blue/40",
  R: "border-signal-red/40",
  I: "border-ind-purple/40",
};

export const PARTY_BG: Record<string, string> = {
  D: "bg-dem-blue/5",
  R: "bg-signal-red/5",
  I: "bg-ind-purple/5",
};

export const PARTY_LABELS: Record<string, string> = {
  D: "DEMOCRAT",
  R: "REPUBLICAN",
  I: "INDEPENDENT",
};

// Combined text+border+bg badge/pill className, keyed by party code (plus
// "bipartisan" for bill-sponsorship badges — a different concept than an
// individual member's party, but the same visual treatment as I). Copy-
// pasted verbatim (VotingRecord.tsx, SponsoredBills.tsx) or reimplemented
// with a drifting bg/border opacity (BillRow.tsx used bg-*/10 already
// matching this; leaderboard/page.tsx used bg-*/20 + border-*/40, an
// undocumented, more-saturated variant of the same badge) before this
// extraction.
export const PARTY_BADGE: Record<string, { label: string; className: string }> = {
  D: { label: "D", className: `${PARTY_COLORS.D} ${PARTY_BORDER.D} bg-dem-blue/10` },
  R: { label: "R", className: `${PARTY_COLORS.R} ${PARTY_BORDER.R} bg-signal-red/10` },
  I: { label: "I", className: `${PARTY_COLORS.I} ${PARTY_BORDER.I} bg-ind-purple/10` },
  bipartisan: { label: "BP", className: `${PARTY_COLORS.I} ${PARTY_BORDER.I} bg-ind-purple/10` },
};

// Policy-area chips on a vote or a sponsored bill, keyed by the area's own
// party alignment (`a.party`) rather than by a member's registration. Same
// visual language as PARTY_BADGE for R and D; the third case is not
// "Independent" but "neither side owns this area", which is why it is amber
// rather than PARTY_BADGE.I's purple.
//
// Extracted for the same reason PARTY_BADGE was, from the same two files —
// VotingRecord and SponsoredBills carried identical copies of this ternary,
// and both had been left half-migrated: the R arm was rewritten to
// `text-signal-red` while the D arm kept `text-blue-400/70` (4.18:1, under
// the floor) with stock-Tailwind `border-blue-400/30 bg-blue-400/5` around
// it. Splitting a two-armed ternary across two palettes is how that survives
// review.
export function policyAreaBadgeClass(party: string | null | undefined): string {
  if (party === "R") return PARTY_BADGE.R.className;
  if (party === "D") return PARTY_BADGE.D.className;
  return "text-signal-amber border-signal-amber/40 bg-signal-amber/10";
}
