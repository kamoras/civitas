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
 * Record<string, string>, not Record<Party, string>: most call sites index
 * with a plain `string`-typed party field (e.g. senator.party), not the
 * narrower "D"|"R"|"I" literal union, so a stricter key type would just
 * force `as Party` casts at every call site for no real safety gain.
 */

export type Party = "D" | "R" | "I";

export const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

export const PARTY_BORDER: Record<string, string> = {
  D: "border-dem-blue/30",
  R: "border-rep-red/30",
  I: "border-ind-purple/30",
};

export const PARTY_BG: Record<string, string> = {
  D: "bg-dem-blue/5",
  R: "bg-rep-red/5",
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
  R: { label: "R", className: `${PARTY_COLORS.R} ${PARTY_BORDER.R} bg-rep-red/10` },
  I: { label: "I", className: `${PARTY_COLORS.I} ${PARTY_BORDER.I} bg-ind-purple/10` },
  bipartisan: { label: "BP", className: `${PARTY_COLORS.I} ${PARTY_BORDER.I} bg-ind-purple/10` },
};
