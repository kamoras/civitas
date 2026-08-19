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
 * The surviving answer is phosphor as border and text over a 10% wash. It
 * reads as selected without competing with the figures, it is the same
 * colour language as the tab rule below, and the wash is an established
 * idiom here already (the homepage's REQUEST A RECORD header uses it).
 *
 * Selection is never carried by colour alone: BOXED keeps a border on both
 * states and TAB keeps a 3px rule, so the shape changes too.
 */

/** A boxed control — filter pill, segmented button, pagination number. */
export const BOXED_CONTROL = {
  selected: "border-phos bg-phos/10 text-phos",
  unselected: "border-white/[0.07] text-ink-lo hover:border-white/30 hover:text-ink",
} as const;

/**
 * A tab in a strip that carries its own bottom border. Pair with
 * `border-b-3` on the tab and a `border-b` on the strip, so the selected tab
 * appears to punch through the strip's rule.
 */
export const TAB_CONTROL = {
  selected: "border-phos text-ink-hi",
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
