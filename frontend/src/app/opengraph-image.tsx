import { ImageResponse } from "next/og";
import { loadArchivoBold } from "@/lib/ogFonts";

export const runtime = "edge";
export const alt = "CIVITAS // PUBLIC RECORD";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Same palette as tailwind.config.ts — Satori (next/og) renders its own box
// model outside Tailwind, so these are hand-copied rather than imported.
const BASE = "#0e0c0a";
const INK_HI = "#f2eee7";
const INK_LO = "#cdc7bc";
const INK_MIN = "#bbb5ac";
const CYAN = "#4de3e8";
const MAGENTA = "#ff6bd6";

const WORDMARK = "CIVITAS";
const SUBTITLE = "PUBLIC RECORD";
const DESCRIPTION = "Congressional scorecards · Campaign finance transparency · Civic actions";
const DOMAIN = "civitas-research.org";

const WORDMARK_STYLE = {
  fontSize: 96,
  fontFamily: "Archivo",
  fontWeight: 700 as const,
  letterSpacing: "0.1em",
  lineHeight: 1,
};

export default async function OgImage() {
  // The old version had no external dependency and never failed. This route
  // is hit by every crawler unfurling any page link, so an outage on
  // Google's end (or its response shape changing) degrades to Satori's
  // default face rather than 500ing every social-share preview on the site.
  let archivoBold: ArrayBuffer | null = null;
  try {
    archivoBold = await loadArchivoBold(`${WORDMARK}${SUBTITLE}${DESCRIPTION}${DOMAIN}`);
  } catch {
    // fall through with archivoBold still null
  }

  return new ImageResponse(
    (
      <div
        style={{
          background: BASE,
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "Archivo",
          padding: "60px",
          position: "relative",
        }}
      >
        {/* Records-band hairlines, not the old CRT scanline/frame combo. */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "2px",
            background: "rgba(187,181,172,0.35)",
            display: "flex",
          }}
        />
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: "2px",
            background: "rgba(187,181,172,0.35)",
            display: "flex",
          }}
        />

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "28px",
          }}
        >
          {/* Overprint wordmark: two off-register plates behind the ink
              layer — same construction and colours as .overprint on the
              site, offset scaled up from its fixed 3px (sized for a 12px
              navbar mark) to stay proportionate at 96px. */}
          <div style={{ position: "relative", display: "flex" }}>
            <div
              style={{ ...WORDMARK_STYLE, position: "absolute", top: 5, left: -5, color: MAGENTA, display: "flex" }}
            >
              {WORDMARK}
            </div>
            <div
              style={{ ...WORDMARK_STYLE, position: "absolute", top: -5, left: 5, color: CYAN, display: "flex" }}
            >
              {WORDMARK}
            </div>
            <div style={{ ...WORDMARK_STYLE, position: "relative", color: INK_HI, display: "flex" }}>
              {WORDMARK}
            </div>
          </div>

          <div
            style={{
              width: "360px",
              height: "1px",
              background: "rgba(205,199,188,0.3)",
              display: "flex",
            }}
          />

          <div
            style={{
              fontSize: "22px",
              color: INK_LO,
              letterSpacing: "0.2em",
              textAlign: "center",
              display: "flex",
            }}
          >
            {SUBTITLE}
          </div>

          <div
            style={{
              fontSize: "18px",
              color: INK_MIN,
              letterSpacing: "0.05em",
              textAlign: "center",
              maxWidth: "680px",
              lineHeight: 1.5,
              display: "flex",
            }}
          >
            {DESCRIPTION}
          </div>

          <div
            style={{
              marginTop: "12px",
              border: "1px solid rgba(187,181,172,0.3)",
              padding: "8px 24px",
              fontSize: "14px",
              color: INK_MIN,
              letterSpacing: "0.2em",
              display: "flex",
            }}
          >
            {DOMAIN}
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      // Satori requires at least one *loaded* font — passing an empty array
      // throws "No fonts are loaded", it isn't a valid empty-fallback value
      // the way [] usually is. Omitting the key entirely is what actually
      // falls back to Satori's own default face.
      ...(archivoBold
        ? { fonts: [{ name: "Archivo", data: archivoBold, weight: 700, style: "normal" }] }
        : {}),
    }
  );
}
