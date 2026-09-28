/**
 * Turns one section of a page, as the reader sees it, into a PNG they can
 * paste, save, or hand to their phone's share sheet.
 *
 * The section is rasterised from the live DOM (modern-screenshot draws the
 * node through an SVG foreignObject, so the browser renders its own CSS —
 * Tailwind's colours, the self-hosted fonts, the SVG charts — rather than a
 * re-implementation of it). A cropped section rarely says whose it is or
 * where it came from, so the capture is then framed on a canvas: a strip
 * naming the page's subject above it, and the page's link and the capture
 * date below. The date matters because the numbers change nightly; the link
 * is how the friend who receives the picture gets to the rest of it.
 *
 * Nothing leaves the browser. The one network request a capture can add is
 * a member photo, fetched through this site's own `/photo/bioguide/…` route
 * (see `proxiedImageUrl`).
 */

export const SHARE_EXCLUDE_ATTR = "data-share-exclude";
export const SHARE_SECTION_ATTR = "data-share-section";
/** Set on a section only while it is measured and cloned; see globals.css. */
const CAPTURING_ATTR = "data-share-capturing";

/** What the frame around a captured section says. */
export interface ShareSubject {
  /** Who or what the page is about: "Tim Burchett", "H.R. 1234". */
  title: string;
  /** One line under it: "Representative · TN-2 · Republican". */
  subtitle?: string;
  /** A headline figure shown at the right of the strip, e.g. the overall
   *  score. `colorClass` is the Tailwind text-colour class the page uses
   *  for it, resolved to a real colour at capture time so the image and the
   *  page cannot disagree. */
  badge?: { label: string; value: string; colorClass?: string };
  /** Absolute URL of the page (a section anchor is appended per section). */
  url: string;
}

// bioguide.congress.gov sends no CORS headers and sits behind a Cloudflare
// challenge, so the browser can neither read those photos into a canvas
// nor fetch them itself. The site's own route fetches them server-side
// (`app/photo/bioguide/[id]/route.ts`); only bioguide ids are accepted
// there, so it is not an open proxy.
const BIOGUIDE_PHOTO =
  /^https:\/\/bioguide\.congress\.gov\/bioguide\/photo\/[A-Z]\/([A-Z]\d{6})\.jpg$/;

/** Same-origin URL for an image the capture must read, or null when it can
 *  be read directly (same-origin, or a host that sends CORS headers). */
export function proxiedImageUrl(url: string): string | null {
  const m = BIOGUIDE_PHOTO.exec(url);
  return m ? `/photo/bioguide/${m[1]}` : null;
}

/** "civitas-research.org/politicians/tim-burchett#funding" — the URL as
 *  printed in the footer, without the scheme nobody types. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

/** Download name: "civitas-tim-burchett-funding.png". */
export function shareFileName(url: string, sectionId: string): string {
  const path = displayUrl(url).split("#")[0].split("?")[0];
  const slug = path.split("/").filter(Boolean).pop() ?? "page";
  const clean = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  return `civitas-${clean(slug)}-${clean(sectionId)}.png`;
}

/** The URL for one section: the page link plus the section's anchor. */
export function sectionUrl(pageUrl: string, sectionId: string): string {
  return `${pageUrl.split("#")[0]}#${sectionId}`;
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** A computed style of a Tailwind class, read off a throwaway element in
 *  <body> — the palette and the fonts live in CSS, not in this file. It has
 *  to be <body>: next/font puts its family variables on the body's class,
 *  not on :root, so a `var(--font-…)` read from the root comes back empty. */
function resolveClassStyle(
  className: string | undefined,
  prop: "color" | "fontFamily",
  fallback: string
): string {
  if (!className) return fallback;
  const probe = document.createElement("span");
  probe.className = className;
  probe.style.display = "none";
  document.body.appendChild(probe);
  const value = getComputedStyle(probe)[prop];
  probe.remove();
  return value || fallback;
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** Breaks a URL into lines no wider than `max`, after a "/" or "#" where it
 *  can, so the link printed in the picture is always whole. */
function wrapLink(ctx: CanvasRenderingContext2D, url: string, max: number): string[] {
  const parts = url.split(/(?<=[/#-])/);
  const lines: string[] = [];
  let line = "";
  for (const part of parts) {
    if (line && ctx.measureText(line + part).width > max) {
      lines.push(line);
      line = "";
    }
    line += part;
  }
  if (line) lines.push(line);
  return lines;
}

/** Word-wraps `text` into at most `maxLines` lines no wider than `max`,
 *  the last one ellipsised if the text runs longer. */
function wrapWords(
  ctx: CanvasRenderingContext2D,
  text: string,
  max: number,
  maxLines: number
): string[] {
  const lines: string[] = [];
  let line = "";
  const words = text.split(/\s+/).filter(Boolean);
  for (let i = 0; i < words.length; i++) {
    const next = line ? `${line} ${words[i]}` : words[i];
    if (!line || ctx.measureText(next).width <= max) {
      line = next;
      continue;
    }
    if (lines.length === maxLines - 1) {
      return [...lines, fitText(ctx, `${line} ${words.slice(i).join(" ")}`, max)];
    }
    lines.push(line);
    line = words[i];
  }
  if (line) lines.push(fitText(ctx, line, max));
  return lines;
}

/** Shrinks `text` with an ellipsis until it fits in `max` pixels. */
function fitText(ctx: CanvasRenderingContext2D, text: string, max: number): string {
  if (ctx.measureText(text).width <= max) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > max) cut = cut.slice(0, -1);
  return `${cut.trimEnd()}…`;
}

function captureDate(now: Date): string {
  return now.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

/**
 * Renders `section` to a PNG framed with `subject`.
 *
 * `withStrip` is false for a section that already names its subject (the
 * scorecard header), where the strip would repeat it.
 */
export async function captureSection(
  section: HTMLElement,
  subject: ShareSubject,
  {
    sectionId,
    withStrip = true,
    anchored = true,
  }: { sectionId: string; withStrip?: boolean; anchored?: boolean }
): Promise<Blob> {
  const { domToCanvas } = await import("modern-screenshot");
  await document.fonts.ready;

  const surfaceBase = cssVar("--surface-base", "#0e0c0a");
  const inkHi = cssVar("--ink-hi", "#f2eee7");
  const inkLo = cssVar("--ink-lo", "#cdc7bc");
  const inkMin = cssVar("--ink-min", "#bbb5ac");
  const sans = resolveClassStyle("font-sans", "fontFamily", "sans-serif");
  const mono = resolveClassStyle("font-mono", "fontFamily", "monospace");
  // A canvas draws with whatever face is loaded right now; it never waits.
  await Promise.all([
    document.fonts.load(`800 16px ${sans}`),
    document.fonts.load(`16px ${mono}`),
  ]).catch(() => {});

  // At least 2x, so the text stays sharp once a phone or chat client
  // scales the picture down; capped so a very wide section on a 3x screen
  // stays inside every browser's canvas limit.
  const scale = Math.min(Math.max(window.devicePixelRatio || 1, 2), 3);

  const release = () => section.removeAttribute(CAPTURING_ATTR);
  section.setAttribute(CAPTURING_ATTR, "");
  let shot: HTMLCanvasElement;
  try {
    shot = await domToCanvas(section, {
      scale,
      backgroundColor: surfaceBase,
      onCloneNode: release,
      filter: (node) => !(node instanceof Element && node.hasAttribute(SHARE_EXCLUDE_ATTR)),
      fetchFn: async (url) => {
        const proxied = proxiedImageUrl(url);
        if (!proxied) return false;
        try {
          const res = await fetch(proxied);
          if (!res.ok) return false;
          return await blobToDataUrl(await res.blob());
        } catch {
          return false;
        }
      },
    });
  } finally {
    release();
  }

  const pad = 20 * scale;
  const innerW = shot.width;
  const out = document.createElement("canvas");
  const ctx = out.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context unavailable");

  // The title strip: the badge (if any) on the right, the title beside it
  // on up to two lines — a bill's title is the part a reader recognises —
  // and the subtitle under it. Laid out before the canvas is sized.
  const titleFont = `800 ${26 * scale}px ${sans}`;
  const badgeValueFont = `800 ${34 * scale}px ${sans}`;
  const smallMono = (px: number) => `${px * scale}px ${mono}`;
  let badgeW = 0;
  if (subject.badge) {
    ctx.font = badgeValueFont;
    const valueW = ctx.measureText(subject.badge.value).width;
    ctx.font = smallMono(11);
    badgeW = Math.max(valueW, ctx.measureText(subject.badge.label.toUpperCase()).width);
  }
  const titleMax = innerW - (badgeW ? badgeW + 24 * scale : 0);
  ctx.font = titleFont;
  const titleLines = withStrip ? wrapWords(ctx, subject.title, titleMax, 2) : [];
  const titleLineH = 32 * scale;
  const subtitleBaseline = 28 * scale + (titleLines.length - 1) * titleLineH + 22 * scale;
  const stripH = withStrip
    ? Math.max(
        68 * scale,
        (subject.subtitle ? subtitleBaseline : subtitleBaseline - 22 * scale) + 18 * scale
      )
    : 0;

  // The footer's link is how the picture's recipient gets to the page, so
  // it is never cut short: it wraps, and when it and the date don't fit on
  // one line, the date drops below it. Measured before sizing the
  // canvas (resizing a canvas resets its context).
  const footFont = `${12 * scale}px ${mono}`;
  const link = displayUrl(anchored ? sectionUrl(subject.url, sectionId) : subject.url);
  const date = `Captured ${captureDate(new Date())}`;
  ctx.font = footFont;
  const linkLines = wrapLink(ctx, link, innerW);
  const oneLine =
    linkLines.length === 1 &&
    ctx.measureText(link).width + ctx.measureText(date).width + 24 * scale <= innerW;
  const lineH = 20 * scale;
  const footH = 44 * scale + (oneLine ? 0 : linkLines.length * lineH);

  out.width = innerW + pad * 2;
  out.height = stripH + shot.height + footH + pad * (withStrip ? 2 : 1);

  ctx.fillStyle = surfaceBase;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.textBaseline = "alphabetic";

  let y = pad;

  if (withStrip) {
    if (subject.badge) {
      const right = pad + innerW;
      ctx.textAlign = "right";
      ctx.font = smallMono(11);
      ctx.fillStyle = inkMin;
      ctx.fillText(subject.badge.label.toUpperCase(), right, y + 14 * scale);
      ctx.font = badgeValueFont;
      ctx.fillStyle = resolveClassStyle(subject.badge.colorClass, "color", inkHi);
      ctx.fillText(subject.badge.value, right, y + 50 * scale);
      ctx.textAlign = "left";
    }
    ctx.font = titleFont;
    ctx.fillStyle = inkHi;
    titleLines.forEach((line, i) => ctx.fillText(line, pad, y + 28 * scale + i * titleLineH));
    if (subject.subtitle) {
      ctx.font = smallMono(12);
      ctx.fillStyle = inkLo;
      ctx.fillText(
        fitText(ctx, subject.subtitle.toUpperCase(), titleMax),
        pad,
        y + subtitleBaseline
      );
    }
    y += stripH;
  }

  ctx.drawImage(shot, pad, y);
  y += shot.height;

  // Footer: a hairline, then the link and the capture date.
  ctx.fillStyle = "rgba(255,255,255,0.12)";
  ctx.fillRect(pad, y + 12 * scale, innerW, Math.max(1, scale / 2));
  let baseline = y + 34 * scale;
  ctx.font = footFont;
  ctx.fillStyle = inkLo;
  linkLines.forEach((line, i) => ctx.fillText(line, pad, baseline + i * lineH));
  if (!oneLine) baseline += linkLines.length * lineH;
  ctx.textAlign = oneLine ? "right" : "left";
  ctx.fillStyle = inkMin;
  ctx.fillText(date, oneLine ? pad + innerW : pad, baseline);
  ctx.textAlign = "left";

  return new Promise((resolve, reject) =>
    out.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG encoding failed"))), "image/png")
  );
}

/** Whether this browser can put an image on the clipboard. */
export function canCopyImage(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.ClipboardItem !== "undefined" &&
    typeof navigator.clipboard?.write === "function"
  );
}

/** Whether the system share sheet accepts an image file (phones, mostly). */
export function canShareImage(): boolean {
  if (typeof navigator === "undefined" || typeof navigator.canShare !== "function") return false;
  try {
    const probe = new File([new Uint8Array(1)], "probe.png", { type: "image/png" });
    return navigator.canShare({ files: [probe] });
  } catch {
    return false;
  }
}
