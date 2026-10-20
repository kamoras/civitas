/**
 * Chart and map colours that follow the theme.
 *
 * Every value here is a CSS custom property from globals.css (or a mix of
 * one), so a chart drawn on paper in the light theme and on carbon in the
 * dark one needs no theme check in JavaScript, server render included, and
 * a share capture (which reads computed styles) draws what the reader sees.
 * wcagContrast.test.ts resolves each against both themes' values.
 */

/** A party's mark colour on maps and charts. Anything but D or R is the
 * third-party purple. */
export function partyMark(party: string | null | undefined): string {
  if (party === "DEM" || party === "D") return "var(--map-dem)";
  if (party === "REP" || party === "R") return "var(--map-rep)";
  return "var(--map-ind)";
}

/** `color` at `strength` (0 to 1): fainter toward transparent, from the
 * theme's floor (--map-floor) up, so the faintest step still stands at 3:1
 * on paper. Full strength is the colour itself. */
export function fade(color: string, strength: number): string {
  if (strength >= 1) return color;
  const s = Number(Math.max(0, strength).toFixed(2));
  return `color-mix(in srgb, ${color} calc((var(--map-floor) + ${s} * (1 - var(--map-floor))) * 100%), transparent)`;
}

/** The theme's overlay (ink on paper, white on carbon) at `alpha`: the
 * translucent rule or wash that reads as one in both themes. */
export function overlay(alpha: number): string {
  return `color-mix(in srgb, var(--overlay) ${Math.round(alpha * 100)}%, transparent)`;
}

/** A colour from the API (industries, holdings, bill stages), tuned for
 * neither theme, as a mark: mixed toward the theme's tint so it clears 3:1
 * on both grounds. */
export function chartMark(color: string): string {
  return `color-mix(in srgb, ${color}, var(--mark-tint) var(--mark-tint-amount))`;
}
