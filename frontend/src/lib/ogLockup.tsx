import { OG_FAMILY } from "./ogFonts";
import { OG_COLOR } from "./ogTheme";

/** The Navbar's lockup for a Satori image: CIVITAS in the wordmark face, a
 *  hairline, RESEARCH in the label face. `size` is the wordmark's px. */
export function OgLockup({ size, color = OG_COLOR.inkHi }: { size: number; color?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: size * 0.45, color }}>
      <span
        style={{
          fontFamily: OG_FAMILY.wordmark,
          fontWeight: 800,
          fontSize: size,
          letterSpacing: "0.14em",
        }}
      >
        CIVITAS
      </span>
      <span style={{ display: "flex", width: 2, height: size * 0.9, background: OG_COLOR.rule }} />
      <span
        style={{
          fontFamily: OG_FAMILY.sans,
          fontWeight: 600,
          fontSize: size * 0.62,
          letterSpacing: "0.2em",
        }}
      >
        RESEARCH
      </span>
    </div>
  );
}
