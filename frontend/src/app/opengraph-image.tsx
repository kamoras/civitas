import { ImageResponse } from "next/og";
import { OG_FAMILY, ogFonts } from "@/lib/ogFonts";
import { OgLockup } from "@/lib/ogLockup";
import { OG_COLOR as C } from "@/lib/ogTheme";
import { DEFAULT_OG_IMAGE, SITE_DOMAIN, SITE_TAGLINE } from "@/lib/site";

export const runtime = "nodejs";
export const alt = DEFAULT_OG_IMAGE.alt;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const DESCRIPTION =
  "Congress, the money, the votes, elections and agencies, from the public record.";
const DOMAIN = SITE_DOMAIN;

/** The site card, set like the first page of a register: the lockup as the
 *  header carries it, the promise in the title face, a ruled foot with the
 *  address in the identifier face. Sizes hold at a feed's thumbnail (about
 *  a third of this), where the tagline is still a 30 px line. */
export default async function OgImage() {
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: C.parchment,
        borderTop: `14px solid ${C.accent}`,
        padding: "56px 80px 52px",
        fontFamily: OG_FAMILY.sans,
        color: C.inkHi,
      }}
    >
      <OgLockup size={34} />
      <div style={{ display: "flex", flexDirection: "column", flex: 1, justifyContent: "center" }}>
        <div
          style={{
            display: "flex",
            fontFamily: OG_FAMILY.serif,
            fontWeight: 600,
            fontSize: 92,
            lineHeight: 1.05,
            letterSpacing: "-0.01em",
            // Breaks after "without", not before a lone "source."
            maxWidth: 800,
          }}
        >
          {SITE_TAGLINE}
        </div>
        <div
          style={{ display: "flex", fontSize: 32, lineHeight: 1.4, color: C.inkLo, marginTop: 28 }}
        >
          {DESCRIPTION}
        </div>
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderTop: `2px solid ${C.rule}`,
          paddingTop: 22,
          fontSize: 24,
          color: C.inkLo,
        }}
      >
        <span style={{ fontFamily: OG_FAMILY.mono }}>{DOMAIN}</span>
        <span style={{ fontWeight: 600 }}>An independent public record</span>
      </div>
    </div>,
    { ...size, fonts: await ogFonts() }
  );
}
