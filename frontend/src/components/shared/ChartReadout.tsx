"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";
import { MARK_ATTR, type ReadoutMark } from "@/types/readout";

/** A mark the readout can name. Every word is the API's (`ReadoutMark`);
 *  `x` and `y` place it, for a chart picked along its axis. */
export interface ChartMark extends ReadoutMark {
  /** Across the chart, 0-100 (axis charts). */
  x?: number;
  /** The mark's centre, in px from the chart's top (axis charts). */
  y?: number;
  /** Or its centre as a share of the chart's height, 0-100 (a chart whose
   *  height follows the text size). */
  yPct?: number;
}

/** A mark's centre in px from the chart's top, for an axis chart. */
const yOf = (m: ChartMark, r: DOMRect): number | null =>
  m.y ?? (m.yPct != null ? (m.yPct / 100) * r.height : null);

/** How far, in px, the pointer may be from a mark and still pick it: a
 *  48px target for a lone mark, never under WCAG 2.5.8's 24. */
const HIT_PX = 24;
/** Marks PageUp and PageDown move by, for a chart of 435. */
const PAGE = 10;

// One sentence: a part that ends in its own full stop loses it inside.
const bare = (s: string) => s.replace(/\.$/, "");
const words = (m: ReadoutMark) =>
  `${bare(m.label)}${m.detail ? `, ${bare(m.detail)}` : ""}: ${
    m.context ? `${bare(m.shown)}, ${bare(m.context)}` : bare(m.shown)
  }.`;

type Box = { left: number; top: number; width: number; height: number };

type Shape = { el: Element | null; b: Box };

/** Where a mark is drawn in the chart, in px from its top left: every
 *  shape carrying its id that is on screen (a small state is drawn twice,
 *  as itself and as a box off the coast), else the first. */
function shapesOf(m: ChartMark, box: HTMLDivElement, r: DOMRect): Shape[] {
  const y = yOf(m, r);
  if (m.x != null && y != null) {
    return [{ el: null, b: { left: (m.x / 100) * r.width, top: y, width: 0, height: 0 } }];
  }
  const all = [...box.querySelectorAll(`[${MARK_ATTR}="${CSS.escape(m.id)}"]`)].map((el) => {
    const e = el.getBoundingClientRect();
    return {
      el,
      b: { left: e.left - r.left, top: e.top - r.top, width: e.width, height: e.height },
    };
  });
  const shown = all.filter((s) => s.b.width > 0 || s.b.height > 0);
  return shown.length ? shown : all.slice(0, 1);
}

/** The shape a mark's label points at: its largest on screen. */
function placeOf(m: ChartMark, box: HTMLDivElement, r: DOMRect): Shape | null {
  const shapes = shapesOf(m, box, r);
  return shapes.reduce<Shape | null>(
    (best, s) => (best && best.b.width * best.b.height >= s.b.width * s.b.height ? best : s),
    null
  );
}

/**
 * Any chart's marks, made readable one by one: the shared interaction of
 * every strip, bar, cell, line and map on the site.
 *
 * The pointer picks the nearest mark (along the axis for a strip or line,
 * whose marks carry `x`/`y`; by shape for bars, cells and regions, whose
 * drawn elements carry `data-mark={id}`), within 24px, shows its label,
 * and a click opens its page. On touch a tap shows the label and a tap on
 * the label opens the page. The label stays while the pointer moves onto
 * it and closes on Escape (WCAG 1.4.13).
 *
 * The chart is one Tab stop: arrow keys move through `marks` in the order
 * given (by value, time or place: the caller's choice), `cols` makes Up
 * and Down move by rows of a grid, Home and End go to the ends, Enter
 * opens the page, and a polite live region reads the label. The drawing
 * itself (`children`) stays hidden from assistive technology: each chart
 * keeps a table, list or text that is its full text form.
 */
export default function ChartReadout({
  label,
  marks,
  startId,
  cols,
  labelAt = "top",
  className = "",
  onActive,
  ring = true,
  onOpen,
  children,
}: {
  /** The chart's accessible name: what it measures, and of whom. */
  label: string;
  marks: ChartMark[];
  /** Where the arrow keys start: the page's own mark. */
  startId?: string;
  /** Marks per row, for a grid: Up and Down move by a row. */
  cols?: number;
  /** "top": the label above the chart (a strip, a bar); "mark": at the
   *  mark, below it in the chart's top half (a map, a grid). */
  labelAt?: "top" | "mark";
  className?: string;
  /** The open mark's id, for a chart that draws its own highlight. */
  onActive?: (id: string | null) => void;
  /** False for a chart that outlines the open mark itself (a map's state:
   *  a box around its bounds would mean nothing). */
  ring?: boolean;
  /** What Enter or a click does on a mark with no page (a pie's slice
   *  filters the list beside it). */
  onOpen?: (id: string) => void;
  children: React.ReactNode;
}) {
  const [active, setActiveIndex] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");
  const [place, setPlace] = useState<{ b: Box; circle: boolean; w: number; h: number } | null>(
    null
  );
  const box = useRef<HTMLDivElement>(null);
  const link = useRef<HTMLAnchorElement & HTMLDivElement>(null);
  const pointer = useRef("mouse");
  // Null too when the marks change under an open label (a peer-group switch).
  const m = active == null ? null : (marks[active] ?? null);

  const setActive = (i: number | null) => {
    if (i === active) return;
    setActiveIndex(i);
    onActive?.(i == null ? null : (marks[i]?.id ?? null));
    const q = i == null ? null : marks[i];
    if (q == null || !box.current) return setPlace(null);
    const r = box.current.getBoundingClientRect();
    const at = placeOf(q, box.current, r);
    setPlace(
      at && {
        b: at.b,
        circle: at.el == null || at.el.tagName.toLowerCase() === "circle",
        w: r.width,
        h: r.height,
      }
    );
  };

  useEffect(() => {
    if (active == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActive(null);
    };
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setActive(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // A label wider than its chart is kept on screen: nudged in from either
  // edge of the window (no horizontal scroll at 320px).
  useLayoutEffect(() => {
    const el = link.current;
    if (!el) return;
    el.style.marginLeft = "0px";
    const r = el.getBoundingClientRect();
    const edge = 8;
    const right = document.documentElement.clientWidth - edge;
    const dx = Math.max(edge - r.left, 0) - Math.max(r.right - right, 0);
    if (dx) el.style.marginLeft = `${dx}px`;
  }, [place]);

  const pick = (e: React.PointerEvent | React.MouseEvent): number | null => {
    const r = box.current!.getBoundingClientRect();
    const px = e.clientX - r.left;
    const py = e.clientY - r.top;
    // On a drawn shape: that one, exactly (a state's outline, not its box).
    const on = (e.target as Element).closest?.(`[${MARK_ATTR}]`);
    if (on && box.current!.contains(on)) {
      const i = marks.findIndex((q) => q.id === on.getAttribute(MARK_ATTR));
      if (i >= 0) return i;
    }
    let best: number | null = null;
    let bd = Infinity;
    let by = Infinity;
    marks.forEach((q, i) => {
      const qy = yOf(q, r);
      if (q.x != null && qy != null) {
        // Nearest along the axis; marks within a pixel go by height.
        const dx = Math.abs((q.x / 100) * r.width - px);
        const dy = Math.abs(qy - py);
        if (dx < bd - 1 || (dx <= bd + 1 && dy < by)) {
          best = i;
          bd = Math.min(bd, dx);
          by = dy;
        }
        return;
      }
      // Near a shape: the gap to its box's edge, 0 inside; among boxes the
      // pointer is inside, the smallest (the mark drawn there, likeliest).
      for (const { b } of shapesOf(q, box.current!, r)) {
        const dx = Math.max(b.left - px, 0, px - b.left - b.width);
        const dy = Math.max(b.top - py, 0, py - b.top - b.height);
        const d = Math.hypot(dx, dy);
        const area = b.width * b.height;
        if (d < bd || (d === 0 && bd === 0 && area < by)) {
          best = i;
          bd = d;
          by = area;
        }
      }
    });
    return bd <= HIT_PX ? best : null;
  };

  const onLabel = (t: EventTarget) => link.current?.contains(t as Node) ?? false;

  const onKeyDown = (e: React.KeyboardEvent) => {
    const last = marks.length - 1;
    if (last < 0) return;
    const from =
      active ??
      Math.max(
        0,
        marks.findIndex((q) => q.id === startId)
      );
    const step = (n: number) => (active == null ? from : Math.max(0, Math.min(last, active + n)));
    const row = cols ?? 1;
    let next: number;
    switch (e.key) {
      case "ArrowRight":
        next = step(1);
        break;
      case "ArrowLeft":
        next = step(-1);
        break;
      case "ArrowUp":
        next = step(cols ? -row : 1);
        break;
      case "ArrowDown":
        next = step(cols ? row : -1);
        break;
      case "PageUp":
        next = step(PAGE);
        break;
      case "PageDown":
        next = step(-PAGE);
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = last;
        break;
      case "Enter":
        if (m?.href) {
          e.preventDefault();
          link.current?.click();
        } else if (m && onOpen) {
          e.preventDefault();
          onOpen(m.id);
        }
        return;
      default:
        return;
    }
    e.preventDefault();
    setActive(next);
    setAnnounce(words(marks[next]));
  };

  const linked = marks.some((q) => q.href) || onOpen != null;
  const below =
    labelAt === "mark" && place != null && place.b.top + place.b.height / 2 < place.h / 2;
  const x = place ? place.b.left + place.b.width / 2 : 0;
  // Slid along so it never runs off either side.
  const share = place && place.w > 0 ? Math.max(0, Math.min(100, (x / place.w) * 100)) : 50;
  const labelStyle: React.CSSProperties =
    labelAt === "top"
      ? { left: `${share}%`, transform: `translateX(-${share}%)` }
      : below
        ? {
            left: `${share}%`,
            top: place!.b.top + place!.b.height + 6,
            transform: `translateX(-${share}%)`,
          }
        : {
            left: `${share}%`,
            top: (place?.b.top ?? 0) - 6,
            transform: `translate(-${share}%, -100%)`,
          };
  const labelClass = `absolute z-20 block w-max max-w-[min(18rem,calc(100vw-2rem))] border border-ink-hi bg-surface px-2 py-1 text-left text-xs leading-snug text-ink ${
    labelAt === "top" ? "bottom-full" : ""
  }`;
  const body = m && (
    <>
      <span
        className={`block font-semibold text-ink-hi ${m.href ? "underline underline-offset-2" : ""}`}
      >
        {m.label}
      </span>
      {m.detail && <span className="block text-ink-lo">{m.detail}</span>}
      <span className="block">
        {m.shown}
        {m.context ? ` · ${m.context}` : ""}
      </span>
    </>
  );

  return (
    // A composite with one Tab stop that moves among its marks itself (the
    // marks are drawn, not elements a role could sit on): its keys are
    // handled here.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      ref={box}
      role="group"
      aria-label={`${label.replace(/\.$/, "")}: ${marks.length} mark${marks.length === 1 ? "" : "s"}. Arrow keys move between them${
        marks.some((q) => q.href) ? ", Enter opens a page" : onOpen ? ", Enter selects one" : ""
      }.`}
      tabIndex={0}
      className={`relative ${linked ? "cursor-pointer" : ""} focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-hi ${className}`}
      onPointerDown={(e) => {
        pointer.current = e.pointerType;
      }}
      onPointerMove={(e) => {
        if (e.pointerType === "mouse" && !onLabel(e.target)) setActive(pick(e));
      }}
      onPointerLeave={(e) => {
        if (e.pointerType === "mouse") setActive(null);
      }}
      onClick={(e) => {
        if (onLabel(e.target)) return;
        const i = pick(e);
        // A click opens the mark the label names; a tap first shows it. A
        // mark with no page and an `onOpen` is acted on by a click, or a
        // second tap.
        if (i != null && i === active && pointer.current === "mouse" && marks[i].href)
          link.current?.click();
        else if (
          i != null &&
          onOpen &&
          !marks[i].href &&
          (i === active || pointer.current === "mouse")
        )
          onOpen(marks[i].id);
        if (!(i != null && i === active)) setActive(i);
      }}
      onKeyDown={onKeyDown}
      onBlur={(e) => {
        if (!box.current?.contains(e.relatedTarget as Node)) setActive(null);
      }}
    >
      <div aria-hidden="true">{children}</div>
      {m && place && (
        <>
          {!ring ? null : place.circle ? (
            <svg
              aria-hidden="true"
              width={22}
              height={22}
              className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: x, top: place.b.top + place.b.height / 2 }}
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            >
              <circle cx={11} cy={11} r={9} fill="none" stroke="var(--ink-hi)" strokeWidth={2} />
            </svg>
          ) : (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute outline outline-2 outline-offset-1 outline-ink-hi"
              style={{
                left: place.b.left,
                top: place.b.top,
                width: place.b.width,
                height: place.b.height,
              }}
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            />
          )}
          {/* Hidden from assistive technology, which hears the live region;
              not a Tab stop, since Enter on the chart opens it. */}
          {m.href ? (
            <Link
              ref={link}
              href={m.href}
              tabIndex={-1}
              aria-hidden="true"
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
              className={labelClass}
              style={labelStyle}
            >
              {body}
            </Link>
          ) : (
            <div
              ref={link}
              aria-hidden="true"
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
              className={labelClass}
              style={labelStyle}
            >
              {body}
            </div>
          )}
        </>
      )}
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
    </div>
  );
}
