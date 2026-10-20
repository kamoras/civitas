import { overlay } from "@/lib/chartColors";

/**
 * Chart colours for the admin dashboard.
 *
 * These are MARK colours, deliberately darker than the site's text tokens in
 * tailwind.config.ts. Those tokens are tuned to clear APCA contrast as 12px
 * text on the near-black surface (OKLCH L 0.75–0.89), which is far too light
 * for a 2px line: on a dark chart every series at that lightness reads as the
 * same bright glow, and lightness stops separating them. Each value below is
 * the same hue family stepped into the dark-mode mark band (L 0.48–0.67).
 *
 * Each value is a theme variable (globals.css, --series-*): the dark set
 * below, and a darker set on paper that clears 3:1 there
 * (wcagContrast.test.ts checks both).
 *
 * The categorical order is load-bearing, not cosmetic. It was chosen by
 * running the dataviz palette validator against the dark panel surface
 * and keeping an ordering that clears every gate — lightness band, chroma
 * floor, ≥3:1 against the surface, and adjacent colour-vision-deficiency
 * separation (worst adjacent ΔE 9.4, target ≥ 8). Re-ordering can fail that
 * last check, so re-run the validator if you change it.
 *
 * Phosphor green and magenta are absent on purpose: the site reserves both
 * for status (a run that completed / failed), so a series drawn in them would
 * read as a verdict. Status series use STATUS below, and always ship with a
 * text label so colour never carries the meaning alone.
 */
export const SERIES = [
  "var(--series-1)", // dark #3987e5
  "var(--series-2)", // dark #c98500
  "var(--series-3)", // dark #9085e9
  "var(--series-4)", // dark #d95926
  "var(--series-5)", // dark #199e70
] as const;

/** Run outcomes. Validated as a set, all pairs (worst CVD ΔE 10.2). */
export const STATUS = {
  good: SERIES[0],
  warning: SERIES[1],
  critical: "var(--series-critical)",
} as const;

/** A pipeline keeps its colour on every chart, whichever others are shown. */
export const PIPELINE_COLORS: Record<string, string> = {
  senate: SERIES[0],
  house: SERIES[1],
  supplementary: SERIES[2],
  stock_trades: SERIES[3],
  election: SERIES[4],
};

export const PIPELINE_LABELS: Record<string, string> = {
  senate: "Senate",
  house: "House",
  supplementary: "Supplementary",
  stock_trades: "Stock trades",
  election: "Election",
};

/** Hairline grid and axis rule — one step off the surface, never dashed. */
export const GRID = overlay(0.09);
/** Surface ring around markers, so a dot stays legible where lines cross. */
export const SURFACE = "var(--surface)";
