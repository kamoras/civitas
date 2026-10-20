/** The light theme's tokens for Satori-rendered images (the OG cards, the
 *  app icon). Satori takes inline style objects only, no CSS custom
 *  properties, so these are copied from globals.css's :root; keep them
 *  equal (ogTheme.test.ts checks). A shared link travels into feeds of
 *  every colour, so the cards use the parchment theme, which is the
 *  site's default. */
export const OG_COLOR = {
  /** --surface-base: the page. */
  parchment: "#f5efe2",
  /** --surface: a box on the page. */
  paper: "#fbf8f1",
  /** --ink-hi: headings, figures. */
  inkHi: "#1e1a15",
  /** --ink: body prose. */
  ink: "#362f27",
  /** --ink-lo: secondary text. */
  inkLo: "#4a4238",
  /** --ink-min: captions, the floor for any text. */
  inkMin: "#5a5143",
  /** --phos: ledger green. */
  accent: "#1b5e34",
  /** --signal-amber. */
  amber: "#7a4f00",
  /** --signal-orange. */
  orange: "#9a3412",
  /** --signal-red. */
  red: "#a12a24",
  /** Box borders and axes: rule-strong in the spec (3:1 on parchment). */
  rule: "#8a7f6c",
  /** A hairline that carries no meaning. */
  hairline: "#d9cfbc",
  dem: "#2552a3",
  rep: "#a12a24",
  ind: "#6b4699",
} as const;
