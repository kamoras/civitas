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
 * The categorical order is load-bearing, not cosmetic. It was chosen by
 * running the dataviz palette validator against the panel surface (#14110E)
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
export const SERIES = ["#3987e5", "#c98500", "#9085e9", "#d95926", "#199e70"] as const;

/** Run outcomes. Validated as a set, all pairs (worst CVD ΔE 10.2). */
export const STATUS = {
  good: "#3987e5",
  warning: "#c98500",
  critical: "#d03b3b",
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
export const GRID = "rgba(255, 255, 255, 0.09)";
/** Surface ring around markers, so a dot stays legible where lines cross. */
export const SURFACE = "#14110E";
