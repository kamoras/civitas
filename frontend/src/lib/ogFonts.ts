/** Fetches Archivo Bold, subset to only the glyphs `text` actually uses,
 *  as raw font bytes for a next/og (Satori) ImageResponse. Shared by every
 *  Satori-rendered OG image (opengraph-image.tsx, api/og/route.tsx) so
 *  they render with the same font the rest of the site now uses, not
 *  Satori's own default face.
 *
 *  Vercel's documented next/og pattern: the Google Fonts CSS2 API returns
 *  a direct woff/ttf src for a given text+weight, fetched only for the
 *  glyphs actually used rather than the whole family. next/font/google
 *  (used everywhere else on the site) isn't reachable from Satori's
 *  render path — it needs raw font bytes handed to it directly.
 *
 *  `text` has to cover every character rendered anywhere in the image,
 *  not just a headline: Satori falls back to whatever font IS registered
 *  when an element's own fontFamily isn't, and a subset built from only
 *  part of the rendered text left other parts rendering in Satori's
 *  default face — a scrambled mix, not a design choice. Callers should
 *  pass the concatenation of every string the image renders.
 *
 *  The User-Agent header is not incidental: Google's CSS2 API answers a
 *  DIFFERENT font format depending on it — truetype for a plain/absent
 *  UA, woff2 for a browser-like one — and confirmed live in this exact
 *  Next.js version, handing Satori a woff2 buffer hard-crashes the
 *  render (kills the connection outright, "failed to pipe response", no
 *  catchable JS error to fall back from) rather than failing gracefully.
 *  This pins a deliberately non-browser UA to force the truetype branch
 *  every time rather than leaving it to whatever the runtime's own
 *  fetch() happens to send. */
export async function loadArchivoBold(text: string): Promise<ArrayBuffer> {
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
