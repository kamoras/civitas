/**
 * One style per role (2026-10): the site's type scale.
 *
 * Every piece of text plays one of these roles, and each role has exactly
 * one look, so a reader learns the page's structure once. Two faces carry
 * meaning: the serif (Source Serif 4) means "a heading or a figure", the
 * sans (Fira Sans) means "read this". Colour is never the only difference
 * between two roles.
 *
 * The sizes are the `--text-<role>` steps in globals.css's `@theme`, each a
 * desktop size and a phone size (under 640px), in rem so they follow a
 * reader's own text size. Measured before this scale: 46 family/size/weight
 * combinations in the main column of 26 pages, 22 font sizes, 51 distinct
 * h2 class strings (research mockup type-scale.html, 2026-10-21).
 *
 *   role       face    weight  desktop / phone   use
 *   display    serif   600     44 / 30           the homepage's own title only
 *   title      serif   600     36 / 28           the page's h1
 *   section    serif   600     24 / 22           h2, a part of the page
 *   boxTitle   serif   600     20 / 18           h3, a box or a list item's title
 *   kicker     sans    600     16 / 15           a short label over a title (kickerToned: in a tone)
 *   dek        sans    400     20 / 18           one line under the title: what the page is for
 *   body       sans    400     18 / 17           running text: a box's line, a finding, a note, prose
 *   small      sans    400     16 / 15           source lines, captions, units, dates
 *   label      sans    600     16 / 15           table headers, chips, controls
 *   figure     serif   600     30 / 26           a headline number (colour from its context)
 *   figureLg   serif   600     56 / 40           the lead's number
 *   id         DM Mono 400     0.92em            a record number, in its context
 *   quote      sans    400     18 / 17           a source's own words, ruled, in curly quotes
 *
 * Body and small went up two pixels (2026-10-22). Measured on production at
 * 1280px, most of the main column's text was small (14px): 95% of a member
 * page, 84% of a state ballot, 69% of the homepage, 68% of the Action
 * Center, because small carried running sentences (box lines, findings,
 * notes) as well as captions. Readability rises with size up to about 18pt
 * and 14 measured worse (Rello, Pielot & Marcos, CHI 2016). So a sentence a
 * reader reads as prose is body, and small is for what sits beside it: a
 * source line, a caption, a unit, a date, a chip, a label.
 *
 * `typeScale.test.ts` holds the code to it: no `text-[Npx]`, an h1 to h3
 * sized only from here, and `font-display` only in this file.
 */
export const TYPE = {
  display: "font-display text-display font-semibold tracking-[-0.02em] text-ink-hi",
  title: "font-display text-title font-semibold tracking-[-0.02em] text-ink-hi",
  section: "font-display text-section font-semibold text-ink-hi",
  boxTitle: "font-display text-box font-semibold text-ink-hi",
  kicker: "font-sans text-small font-semibold text-ink-lo",
  /** A kicker in a tone (RecordBox TONE) or another set colour. */
  kickerToned: "font-sans text-small font-semibold",
  dek: "font-sans text-dek text-ink",
  body: "font-sans text-body text-ink",
  small: "font-sans text-small text-ink-min",
  label: "font-sans text-small font-semibold text-ink-hi",
  figure: "font-display text-figure font-semibold tabular-nums",
  figureLg: "font-display text-figure-lg font-semibold tabular-nums",
  id: "font-id text-[0.92em] font-normal",
  quote: "border-l-3 border-ink-min/60 pl-3 font-sans text-body text-ink",
} as const;

export type TypeRole = keyof typeof TYPE;

/** A role in another colour (a party's, a section's tone): its own ink
 *  swapped out, so two text colours never compete in one class list. */
export function toned(role: TypeRole, color: string): string {
  return TYPE[role].replace(/\btext-ink(-hi|-lo|-min)?\b/, color);
}

/**
 * The one small-caps label style the site keeps (2026-10).
 *
 * Labels, buttons, links, nav items, tabs, tags and table headers are in
 * sentence case: capitals slow reading as words get longer (Tinker 1955;
 * Perea, Rosa & Marcet 2017), dyslexia guidance asks for no block capitals,
 * and a reader told us a page full of them felt intimidating. A one-to-three
 * word section eyebrow over a block, which is not a control, is the narrow
 * case the evidence leaves open (on a single short word the cost is close to
 * nil, and small capitals stay legible), so it may use this, and only this.
 * The text itself stays in sentence case in the source: CSS raises it, so a
 * screen reader reads words, never letters.
 *
 * `noAllCaps.test.ts` fails on an `uppercase` class anywhere else.
 */
export const EYEBROW = TYPE.kicker;
