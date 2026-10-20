/**
 * Turns one section of a page, as the reader sees it, into a PNG they can
 * paste, save, or hand to their phone's share sheet.
 *
 * The section is rasterised from the live DOM (modern-screenshot draws the
 * node through an SVG foreignObject, so the browser renders its own CSS —
 * Tailwind's colours, the self-hosted fonts, the SVG charts — rather than a
 * re-implementation of it). A cropped section rarely says whose it is or
 * where it came from, so the capture is then framed on a canvas: a strip
 * naming the page's subject above it, and below it where the figures come
 * from, the page's link, the date the figures are as of, the scale any
 * score is on, and the capture date. A picture travels without its page:
 * the source and the as-of date are what let it be checked, the scale what
 * makes "72" mean something, and the link how the friend who receives it
 * gets to the rest.
 *
 * Nothing leaves the browser, and a capture requests nothing from any host
 * but this site: every photo the site shows is already same-origin
 * (`/photo/…`, lib/photos.ts), and any third-party image is left out
 * (see `captureImageData`). That guard sees images only: modern-screenshot
 * fetches fonts, stylesheets and external `<use href>` targets directly,
 * which is safe while those stay self-hosted (app/fonts.ts, compiled CSS).
 */

import type { ActionIssue } from "@/types/action";

export const SHARE_EXCLUDE_ATTR = "data-share-exclude";
export const SHARE_SECTION_ATTR = "data-share-section";
/** A section's own source, as-of date and score scale, where they differ
 *  from the page's (a profile's holdings come from the annual disclosure,
 *  not the scorecard's sources). See `shareMetaAttrs`. */
const SOURCE_ATTR = "data-share-source";
const AS_OF_ATTR = "data-share-as-of";
const SCALE_ATTR = "data-share-scale";
/** Set on a section only while it is measured and cloned; see globals.css. */
const CAPTURING_ATTR = "data-share-capturing";

/** What the frame around a captured section says. */
export interface ShareSubject {
  /** Who or what the page is about: "Jane Doe", "H.R. 1234". */
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
  /** Where the figures come from, printed under the picture ("FEC; Senate
   *  and House roll calls"). Required: a picture with no source can't be
   *  checked by whoever receives it. */
  source: string;
  /** YYYY-MM-DD (or an ISO time) the figures are as of, from the backend;
   *  null where the page reads them live, so the capture's day is theirs. */
  asOf: string | null;
  /** The top of the scale the page's scores are on (100): printed beside
   *  the badge ("72/100") and in the footer. Omitted where nothing is a
   *  score. */
  scaleMax?: number;
}

/** What one picture's footer states: the page's, or the section's own. */
export interface ShareMeta {
  source: string;
  asOf: string | null;
  scaleMax?: number;
}

/** A section's own source, as-of date and scale, as attributes to spread
 *  on the element that carries `data-share-section`. `scaleMax: null`
 *  says the section shows no score even on a page whose subject has one. */
export function shareMetaAttrs(meta: {
  source: string;
  asOf: string | null;
  scaleMax?: number | null;
}): Record<string, string> {
  return {
    [SOURCE_ATTR]: meta.source,
    [AS_OF_ATTR]: meta.asOf ?? "",
    ...(meta.scaleMax !== undefined ? { [SCALE_ATTR]: String(meta.scaleMax ?? "") } : {}),
  };
}

/** The footer's facts for a section: its own attributes where it has
 *  them, else the page subject's. */
export function sectionMeta(section: Element, subject: ShareSubject): ShareMeta {
  const scale = section.getAttribute(SCALE_ATTR);
  return {
    source: section.getAttribute(SOURCE_ATTR) || subject.source,
    asOf: section.hasAttribute(AS_OF_ATTR)
      ? section.getAttribute(AS_OF_ATTR) || null
      : subject.asOf,
    scaleMax: scale === null ? subject.scaleMax : Number(scale) || undefined,
  };
}

const MONTH_ABBR = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** "2026-10-14" or "2026-10-14T06:47:00" -> "Oct 14, 2026"; read by hand,
 *  since `new Date("2026-10-14")` is the day before west of Greenwich. */
function shortIsoDate(iso: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m && MONTH_ABBR[Number(m[2]) - 1]
    ? `${MONTH_ABBR[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`
    : null;
}

/** The footer's two lines of facts: the source, and the dates with the
 *  scale between them. */
export function footerFacts(meta: ShareMeta, today: Date): { source: string; dates: string } {
  const captured = captureDate(today);
  const asOf = meta.asOf ? shortIsoDate(meta.asOf) : null;
  return {
    source: `Source: ${meta.source}`,
    dates: [
      asOf ? `Data as of ${asOf}` : `Data as of ${captured}`,
      meta.scaleMax ? `scores out of ${meta.scaleMax}` : null,
      asOf ? `captured ${captured}` : null,
    ]
      .filter(Boolean)
      .join(" · "),
  };
}

/** A 1x1 transparent PNG: what a third-party image becomes in a capture. */
const BLANK_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

/**
 * How the capture gets each image's bytes. Same-origin (and inline) images
 * load normally — every photo the site shows is one (`/photo/…`); anything
 * else is left blank without being requested, so a capture never sends a
 * request from the visitor's browser to a third-party host. Sections mark
 * such images `data-share-exclude` so the blank takes no space.
 */
export async function captureImageData(url: string): Promise<string | false> {
  if (/^(data|blob):/.test(url)) return false;
  try {
    return new URL(url, window.location.href).origin === window.location.origin
      ? false
      : BLANK_PIXEL;
  } catch {
    return BLANK_PIXEL;
  }
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
 *  to be <body>: the root layout puts the font variables on <body>,
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
    // A single word wider than the line would otherwise be pushed whole
    // and run under the badge.
    lines.push(fitText(ctx, line, max));
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
 * `link` is the URL printed in the footer (the section's own link).
 * `withStrip` is false for a section that already names its subject (the
 * scorecard header), where the strip would repeat it.
 */
export async function captureSection(
  section: HTMLElement,
  subject: ShareSubject,
  { link, withStrip = true }: { link: string; withStrip?: boolean }
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

  const release = () => section.removeAttribute(CAPTURING_ATTR);
  section.setAttribute(CAPTURING_ATTR, "");
  let shot: HTMLCanvasElement;
  let scale: number;
  try {
    const box = section.getBoundingClientRect();
    scale = captureScale(box.width, box.height, window.devicePixelRatio || 1);
    shot = await domToCanvas(section, {
      scale,
      // modern-screenshot waits for every <img> in the section to load —
      // excluded ones too — for up to 30s by default; a publisher's photo
      // still hanging on a card shouldn't hold up a picture it isn't in.
      timeout: 8000,
      backgroundColor: surfaceBase,
      onCloneNode: release,
      filter: (node) => !(node instanceof Element && node.hasAttribute(SHARE_EXCLUDE_ATTR)),
      fetchFn: captureImageData,
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
  const badgeValue = subject.badge
    ? `${subject.badge.value}${subject.scaleMax ? `/${subject.scaleMax}` : ""}`
    : "";
  if (subject.badge) {
    ctx.font = badgeValueFont;
    const valueW = ctx.measureText(badgeValue).width;
    ctx.font = smallMono(11);
    badgeW = Math.max(valueW, ctx.measureText(subject.badge.label).width);
  }
  const titleMax = innerW - (badgeW ? badgeW + 24 * scale : 0);
  ctx.font = titleFont;
  const titleLines = withStrip ? wrapWords(ctx, subject.title, titleMax, 2) : [];
  const titleLineH = 32 * scale;
  const subtitleBaseline = 28 * scale + (titleLines.length - 1) * titleLineH + 22 * scale;
  // Once the title has wrapped, the subtitle sits below the badge and can
  // take the full width; it wraps to a second line rather than cut off
  // what comes last (a president's term).
  const subtitleMax = subtitleBaseline - 12 * scale > 56 * scale ? innerW : titleMax;
  const subtitleLineH = 18 * scale;
  ctx.font = smallMono(12);
  const subtitleLines =
    withStrip && subject.subtitle ? wrapWords(ctx, subject.subtitle, subtitleMax, 2) : [];
  const stripBottom = subtitleLines.length
    ? subtitleBaseline + (subtitleLines.length - 1) * subtitleLineH
    : subtitleBaseline - 22 * scale;
  const stripH = withStrip ? Math.max(68 * scale, stripBottom + 18 * scale) : 0;

  // The footer: the source, the link, then the dates and the scale. The
  // link is how the picture's recipient gets to the page, so it is never
  // cut short: it wraps like the rest. Measured before sizing the canvas
  // (resizing a canvas resets its context).
  const footFont = `${12 * scale}px ${mono}`;
  const facts = footerFacts(sectionMeta(section, subject), new Date());
  ctx.font = footFont;
  const sourceLines = wrapWords(ctx, facts.source, innerW, 3);
  const linkLines = wrapLink(ctx, displayUrl(link), innerW);
  const dateLines = wrapWords(ctx, facts.dates, innerW, 2);
  const lineH = 20 * scale;
  const footH = 24 * scale + (sourceLines.length + linkLines.length + dateLines.length) * lineH;

  out.width = innerW + pad * 2;
  out.height = stripH + shot.height + footH + pad * 2;

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
      ctx.fillText(subject.badge.label, right, y + 14 * scale);
      ctx.font = badgeValueFont;
      ctx.fillStyle = resolveClassStyle(subject.badge.colorClass, "color", inkHi);
      ctx.fillText(badgeValue, right, y + 50 * scale);
      ctx.textAlign = "left";
    }
    ctx.font = titleFont;
    ctx.fillStyle = inkHi;
    titleLines.forEach((line, i) => ctx.fillText(line, pad, y + 28 * scale + i * titleLineH));
    ctx.font = smallMono(12);
    ctx.fillStyle = inkLo;
    subtitleLines.forEach((line, i) =>
      ctx.fillText(line, pad, y + subtitleBaseline + i * subtitleLineH)
    );
    y += stripH;
  }

  ctx.drawImage(shot, pad, y);
  y += shot.height;

  // Footer: a hairline, then the link and the capture date. Drawn in the
  // theme's own ink at low opacity: a fixed white one vanished on the light
  // theme's background.
  ctx.fillStyle = inkMin;
  ctx.globalAlpha = 0.35;
  ctx.fillRect(pad, y + 12 * scale, innerW, Math.max(1, scale / 2));
  ctx.globalAlpha = 1;
  let baseline = y + 34 * scale;
  ctx.font = footFont;
  for (const [lines, color] of [
    [sourceLines, inkLo],
    [linkLines, inkLo],
    [dateLines, inkMin],
  ] as const) {
    ctx.fillStyle = color;
    for (const line of lines) {
      ctx.fillText(line, pad, baseline);
      baseline += lineH;
    }
  }

  return new Promise((resolve, reject) =>
    out.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG encoding failed"))), "image/png")
  );
}

// iOS Safari won't allocate a canvas over 16,777,216 pixels (toBlob then
// returns null), and a few browsers cap either side near 16k–32k. The frame
// adds padding, the title strip and the footer (source, link and dates, up
// to seven lines) around the section: at most about 330 CSS px of height,
// 40 of width.
const MAX_CANVAS_AREA = 16_000_000;
const MAX_CANVAS_SIDE = 16_000;
const FRAME_CSS = { width: 40, height: 330 };

/**
 * The pixel ratio to capture a `width` x `height` (CSS px) section at: at
 * least 2x, so text stays sharp once a phone or chat client scales the
 * picture down, up to the screen's own ratio (3x on most phones) — then
 * lowered as far as it takes for the framed image to fit a canvas every
 * browser will allocate. A tall section (a long contest drawer, a bill's
 * votes) on a phone is exactly where the uncapped ratio failed.
 */
export function captureScale(width: number, height: number, devicePixelRatio: number): number {
  const wanted = Math.min(Math.max(devicePixelRatio, 2), 3);
  const w = width + FRAME_CSS.width;
  const h = height + FRAME_CSS.height;
  return Math.min(wanted, Math.sqrt(MAX_CANVAS_AREA / (w * h)), MAX_CANVAS_SIDE / Math.max(w, h));
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

/** What a shared image of an issue says it is from: the outlets its facts
 *  quote (or the official record a developing issue was drafted from), and
 *  the day the issue's facts are of (a seat-flip count's own read time). */
export function issueShareMeta(issue: ActionIssue): { source: string; asOf: string | null } {
  const outlets = Array.from(new Set(issue.sourceNames ?? [])).filter(Boolean);
  return {
    source: outlets.length
      ? outlets.length > 4
        ? `${outlets.slice(0, 4).join("; ")} and ${outlets.length - 4} more, quoted`
        : `${outlets.join("; ")}, quoted`
      : "the official record linked on the issue's page",
    asOf: issue.countAsOf ?? issue.date,
  };
}
