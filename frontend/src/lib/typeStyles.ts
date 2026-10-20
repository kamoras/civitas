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
export const EYEBROW = "font-mono text-xs text-ink-min";
