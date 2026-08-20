import { ImageResponse } from "next/og";

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

/** Vercel's documented next/og pattern: the Google Fonts CSS2 API returns a
 * direct woff/ttf src for a given text+weight, fetched only for the glyphs
 * this image actually uses rather than the whole family. next/font/google
 * (used everywhere else on the site) isn't reachable from an edge function's
 * ImageResponse — it needs raw font bytes handed to it directly.
 *
 * `text` has to cover every character rendered anywhere in the image, not
 * just the wordmark: Satori falls back to whatever font IS registered when
 * an element's own fontFamily isn't, and a subset built from "CIVITAS" alone
 * left it rendering stray glyphs elsewhere in bold Archivo and everything
 * else in its default face — a scrambled mix, not a design choice. One font
 * for the whole image reads as consistent even where the site itself would
 * use a second (monospace) face for labels; this is a compact preview
 * asset, not the site chrome.
 *
 * The User-Agent header is not incidental: Google's CSS2 API answers a
 * DIFFERENT font format depending on it — truetype for a plain/absent UA,
 * woff2 for a browser-like one — and confirmed live in this exact Next.js
 * version, handing Satori a woff2 buffer hard-crashes the edge function
 * (kills the connection outright, "failed to pipe response", no catchable
 * JS error to fall back from) rather than failing gracefully. An edge
 * runtime's own fetch() can plausibly send a browser-like default UA, so
 * this pins a deliberately non-browser one to force the truetype branch
 * every time rather than leaving it to whatever the runtime happens to
 * send. */
async function loadArchivoBold(text: string): Promise<ArrayBuffer> {
  const css = await (
    await fetch(
      `https://fonts.googleapis.com/css2?family=Archivo:wght@700&text=${encodeURIComponent(text)}`,
      { headers: { "User-Agent": "civitas-og-image-generator" } }
    )
  ).text();
  const src = css.match(/src: url\(([^)]+)\) format\('(?:opentype|truetype)'\)/);
  if (!src) throw new Error("Archivo font source not found in Google Fonts CSS");
  const res = await fetch(src[1]);
  return res.arrayBuffer();
}

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
