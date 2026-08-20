/**
 * What a selected control looks like. One definition, because there were
 * three.
 *
 * "This filter is on" / "this tab is showing" was expressed three different
 * ways across the app before this: a solid `bg-phos` pill with dark text
 * (BranchSelector, the leaderboard's ALL pill and its pagination), a cyan
 * tint (bills, compare, politicians, ShareButtons, ElectionsTab), and a
 * phosphor bottom rule (the Action Center's tab bar). Same meaning, three
 * answers, so a reader learning one page learned nothing about the next.
 *
 * The solid fill was also the one thing the palette forbids outright —
 * "Data and wayfinding only — status, rules, figures, link underlines. Never
 * a fill behind a call to action" (tailwind.config.ts) — and it made the
 * loudest mark on a leaderboard the word ALL rather than any of the data.
 *
 * The surviving answer is INK, not phosphor: a bright rule, bright text and a
 * faint wash, against a dim rule and dim text.
 *
 * Phosphor was the obvious choice and it was wrong. Green on the furniture —
 * selected filters, tab rules, the band's edge, the scrollbar — is what made
 * the site read as a terminal rather than a register, whatever the words
 * said. The rule now is that phosphor lands on the DATA (a score, a live
 * figure, a run that completed) and never on the chrome around it. That
 * leaves a leaderboard whose loudest green is the score column, which is
 * where a reader should be looking anyway.
 *
 * Selection is never carried by colour alone: BOXED keeps a border on both
 * states and TAB keeps a 3px rule, so weight and shape change too — which is
 * also why an ink treatment is legible at all.
 */

/** A boxed control — filter pill, segmented button, pagination number. */
export const BOXED_CONTROL = {
  selected: "border-ink-lo bg-white/[0.06] text-ink-hi",
  unselected: "border-white/[0.07] text-ink-lo hover:border-white/30 hover:text-ink",
} as const;

/**
 * A tab in a strip that carries its own bottom border. Pair with
 * `border-b-3` on the tab and a `border-b` on the strip, so the selected tab
 * appears to punch through the strip's rule.
 */
export const TAB_CONTROL = {
  selected: "border-ink-hi text-ink-hi",
  unselected: "border-transparent text-ink-min hover:text-ink-lo",
} as const;

/** `BOXED_CONTROL` as a ternary, for the common inline case. */
export function boxedControl(selected: boolean): string {
  return selected ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected;
}

/** `TAB_CONTROL` as a ternary, for the common inline case. */
export function tabControl(selected: boolean): string {
  return selected ? TAB_CONTROL.selected : TAB_CONTROL.unselected;
}
