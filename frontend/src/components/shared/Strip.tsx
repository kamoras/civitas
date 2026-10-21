"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";

/** One person's mark on a distribution strip, with what its label prints.
 *  Every word and figure is the API's; the strip only places them. */
export interface StripPoint {
  id: string;
  name: string;
  /** Party and seat ("D-CA-5") or a presidency's years; none for a justice. */
  detail?: string | null;
  shown: string;
  /** The place on the row, from the API ("3rd highest of 15"). */
  rank: string;
  href: string;
  /** Across the strip, 0-100. */
  x: number;
  /** The mark's centre, in px from the strip's top. */
  y: number;
}

/** A member's party and seat as the site prints them: "D-CA", "R-TX-5", "R-WY-AL". */
export function partySeat(
  p: { party: string; state: string; district: number | null },
  house: boolean
): string {
  return `${p.party}-${p.state}${house ? `-${p.district || "AL"}` : ""}`;
}

/** How far, in px either side, the pointer may be from a mark and still
 *  pick it: a 48px target for a lone mark, never under WCAG 2.5.8's 24. */
const HIT_PX = 24;
/** Marks PageUp and PageDown move by, for a strip of 435. */
const PAGE = 10;

const words = (p: StripPoint) =>
  `${p.name}${p.detail ? `, ${p.detail}` : ""}: ${p.shown}, ${p.rank.toLowerCase()}.`;

/**
 * The marks of a distribution strip, made readable one by one. The pointer
 * picks the nearest mark along the axis (no pixel aim in a dense strip),
 * shows its label, and a click opens the person's page. On touch a tap shows
 * the label and a tap on the label opens the page. The label stays while
 * the pointer moves onto it and closes on Escape (WCAG 1.4.13).
 *
 * The strip is one Tab stop: arrow keys move by value, Home and End go to
 * the ends, Enter opens the page, and a polite live region reads the label.
 * The drawing itself (`children`) stays hidden from assistive technology:
 * each strip's table is its full text form.
 */
export default function StripPoints({
  label,
  points,
  startId,
  className = "",
  children,
}: {
  /** The strip's accessible name: the measure and who is on it. */
  label: string;
  points: StripPoint[];
  /** Where the arrow keys start: the page's own person. */
  startId?: string;
  /** The strip's height (h-10, h-14). */
  className?: string;
  children: React.ReactNode;
}) {
  // By position, ties in the API's (value) order.
  const order = useMemo(() => [...points].sort((a, b) => a.x - b.x), [points]);
  const [active, setActive] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const link = useRef<HTMLAnchorElement>(null);
  const pointer = useRef("mouse");
  // Null too when the points change under an open label (a peer-group switch).
  const p = active == null ? null : (order[active] ?? null);

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
  }, [active]);

  const pick = (e: React.PointerEvent | React.MouseEvent): number | null => {
    const r = box.current!.getBoundingClientRect();
    const px = e.clientX - r.left;
    const py = e.clientY - r.top;
    let best: number | null = null;
    let bx = Infinity;
    let by = Infinity;
    order.forEach((q, i) => {
      const dx = Math.abs((q.x / 100) * r.width - px);
      const dy = Math.abs(q.y - py);
      // Nearest along the axis; marks within a pixel go by height.
      if (dx < bx - 1 || (dx <= bx + 1 && dy < by)) {
        best = i;
        bx = Math.min(bx, dx);
        by = dy;
      }
    });
    return bx <= HIT_PX ? best : null;
  };

  const onLabel = (t: EventTarget) => link.current?.contains(t as Node) ?? false;

  const onKeyDown = (e: React.KeyboardEvent) => {
    const last = order.length - 1;
    if (last < 0) return;
    const from =
      active ??
      Math.max(
        0,
        order.findIndex((q) => q.id === startId)
      );
    const step = (n: number) => (active == null ? from : Math.max(0, Math.min(last, active + n)));
    let next: number;
    switch (e.key) {
      case "ArrowRight":
      case "ArrowUp":
        next = step(1);
        break;
      case "ArrowLeft":
      case "ArrowDown":
        next = step(-1);
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
        if (active != null) {
          e.preventDefault();
          link.current?.click();
        }
        return;
      default:
        return;
    }
    e.preventDefault();
    setActive(next);
    setAnnounce(words(order[next]));
  };

  return (
    // A composite with one Tab stop that moves among its marks itself (the
    // marks are not elements a role could sit on): its keys are handled here.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      ref={box}
      role="group"
      aria-label={`${label}: ${order.length} marks. Arrow keys move between them, Enter opens a page.`}
      tabIndex={0}
      className={`relative cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-hi ${className}`}
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
        // A click opens the mark the label names; a tap first shows it.
        if (i != null && i === active && pointer.current === "mouse") link.current?.click();
        else setActive(i);
      }}
      onKeyDown={onKeyDown}
      onBlur={(e) => {
        if (!box.current?.contains(e.relatedTarget as Node)) setActive(null);
      }}
    >
      <div aria-hidden="true" className="absolute inset-0">
        {children}
      </div>
      {p && (
        <>
          <svg
            aria-hidden="true"
            width={22}
            height={22}
            className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${p.x}%`, top: p.y }}
            {...{ [SHARE_EXCLUDE_ATTR]: "" }}
          >
            <circle cx={11} cy={11} r={9} fill="none" stroke="var(--ink-hi)" strokeWidth={2} />
          </svg>
          {/* Hidden from assistive technology, which hears the live region;
              not a Tab stop, since Enter on the strip opens it. */}
          <Link
            ref={link}
            href={p.href}
            tabIndex={-1}
            aria-hidden="true"
            {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            className="absolute bottom-full z-20 block w-max max-w-full border border-ink-hi bg-surface px-2 py-1 text-left text-xs leading-snug text-ink"
            style={{ left: `${p.x}%`, transform: `translateX(-${p.x}%)` }}
          >
            <span className="block font-semibold text-ink-hi underline underline-offset-2">
              {p.name}
            </span>
            {p.detail && <span className="block text-ink-lo">{p.detail}</span>}
            <span className="block">
              {p.shown} · {p.rank}
            </span>
          </Link>
        </>
      )}
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
    </div>
  );
}

/**
 * Under a strip: the tick's label on its own line, at the tick (slid along
 * so it never runs off either end), then the axis's two ends. On separate
 * lines, the tick's label never sits over an end label, and never reads as
 * centred under the strip when the tick isn't.
 */
export function StripAxis({
  start,
  end,
  tick,
}: {
  start: string;
  end: string;
  tick?: { at: number; text: string } | null;
}) {
  return (
    <div aria-hidden="true" className="text-xs text-ink-min">
      {tick && (
        <span
          className="relative block w-max max-w-full font-semibold text-ink-lo"
          style={{ left: `${tick.at}%`, transform: `translateX(-${tick.at}%)` }}
        >
          {tick.text}
        </span>
      )}
      <div className="flex justify-between gap-x-3">
        <span className="min-w-0">{start}</span>
        <span className="min-w-0 text-right">{end}</span>
      </div>
    </div>
  );
}

/** A row's big figure: the number large, and any words after it ("million a
 *  year", "of 493") on a smaller line under it, so a long unit never wraps
 *  the number mid-figure. */
export function BigFigure({ shown, className }: { shown: string | null; className: string }) {
  const parts = shown == null ? null : /^([+\-−]?[\d.,]+%?)\s+(.+)$/.exec(shown);
  return (
    <p className={className}>
      {parts ? (
        <>
          {parts[1]}{" "}
          <span className="block text-sm font-semibold leading-snug text-ink-lo">{parts[2]}</span>
        </>
      ) : (
        (shown ?? "No record")
      )}
    </p>
  );
}
