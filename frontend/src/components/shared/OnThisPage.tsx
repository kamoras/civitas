"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";
import Modal from "@/components/shared/Modal";

interface Section {
  id: string;
  label: string;
  count?: string;
}

/** Fewer sections than this and a phone reader can scan the page. */
const MIN_SECTIONS = 3;
/** A page shorter than this many screens doesn't need a section bar. */
const MIN_SCREENS = 6;
/** The bar's height before it is first laid out, px: a 44px target (WCAG
 *  2.5.8) and its 3px rule. In rem, so it grows with the reader's text size;
 *  globals.css's phone scroll padding reserves the same. */
const BAR_PX = 47;

function readSections(): Section[] {
  return (
    [...document.querySelectorAll<HTMLElement>("main [data-section][id]")]
      // A heading inside a closed or hidden part isn't a place to jump to.
      .filter((el) => (typeof el.checkVisibility === "function" ? el.checkVisibility() : true))
      .map((el) => ({
        id: el.id,
        label: el.dataset.section ?? "",
        count: el.dataset.sectionCount,
      }))
  );
}

const same = (a: Section[], b: Section[]) =>
  a.length === b.length &&
  a.every((s, i) => s.id === b[i].id && s.label === b[i].label && s.count === b[i].count);

/**
 * A phone's "on this page" bar (mobile spec P5): 44px, fixed under the
 * header once the page's first section reaches it, naming the section being
 * read, with a SECTIONS button that opens every section in a bottom sheet.
 * Under 640px only; wider screens list their sections inline.
 *
 * Fixed rather than sticky: a sticky bar sticks only inside its parent, and
 * a member page's sections sit in three different ones. The sections are
 * read from the page (`onThisPage` on each heading), not passed in, so the
 * bar lists what the page actually shows, including parts that load after
 * it, and can't drift from the headings.
 *
 * A jump moves focus to the section's heading (made focusable for it), so a
 * keyboard or screen reader carries on from there; no smooth scrolling.
 */
export default function OnThisPage() {
  const [sections, setSections] = useState<Section[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [pinned, setPinned] = useState(false);
  const [long, setLong] = useState(false);
  const [open, setOpen] = useState(false);
  const list = useRef<Section[]>([]);

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const found = readSections();
      if (!same(found, list.current)) {
        list.current = found;
        setSections(found);
      }
      setLong(document.documentElement.scrollHeight > MIN_SCREENS * window.innerHeight);
      const header = document.querySelector('header[role="banner"]');
      const bar = document.querySelector<HTMLElement>("[data-on-this-page]");
      const line =
        (header ? header.getBoundingClientRect().bottom : 0) + (bar?.offsetHeight || BAR_PX) + 1;
      let at: string | null = null;
      for (const s of list.current) {
        const el = document.getElementById(s.id);
        if (el && el.getBoundingClientRect().top <= line) at = s.id;
      }
      setCurrent(at);
      setPinned(at !== null);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Sections that load after the page (a member's holdings, the votes
    // with the president) join the list when they arrive.
    const main = document.querySelector("main");
    const observer = new MutationObserver(schedule);
    if (main) observer.observe(main, { childList: true, subtree: true });
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);

  if (sections.length < MIN_SECTIONS || !long) return null;
  const here = sections.find((s) => s.id === current);

  const jump = (e: MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    setOpen(false);
    // After the sheet has closed and handed focus back to its button.
    requestAnimationFrame(() => {
      const el = document.getElementById(id);
      if (!el) return;
      if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
      history.pushState(null, "", `#${id}`);
      el.scrollIntoView({ block: "start" });
      el.focus({ preventScroll: true });
    });
  };

  return (
    <nav
      aria-label="Sections on this page"
      data-on-this-page=""
      className={`${pinned ? "flex" : "hidden"} fixed inset-x-0 z-40 transition-[top] duration-200 motion-reduce:transition-none border-b-3 border-ink-min/60 bg-surface-base sm:hidden`}
      style={{ top: "var(--header-shown-h, var(--header-h))" }}
    >
      <div className="mx-auto flex h-11 w-full items-center justify-between gap-3 px-4">
        <p className="min-w-0 truncate font-mono text-[0.8125rem] uppercase tracking-[0.08em] text-ink-hi">
          {here ? here.label : "On this page"}
          {here?.count && <span className="text-ink-lo"> · {here.count}</span>}
        </p>
        <button
          type="button"
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
          className="inline-flex h-11 shrink-0 items-center font-mono text-[0.8125rem] uppercase tracking-[0.08em] text-ink-lo hover:text-phos"
        >
          <span aria-hidden="true">[&nbsp;</span>Sections<span aria-hidden="true">&nbsp;▾ ]</span>
        </button>
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title="On this page">
        <ol className="-mx-4 -my-4 flex flex-col">
          {sections.map((s) => (
            <li key={s.id} className="border-b border-white/[0.09] last:border-0">
              <a
                href={`#${s.id}`}
                onClick={(e) => jump(e, s.id)}
                aria-current={s.id === current ? "location" : undefined}
                className={`flex min-h-13 items-center justify-between gap-3 px-4 font-sans text-[1.0625rem] hover:text-phos ${
                  s.id === current ? "bg-white/[0.06] text-ink-hi" : "text-ink"
                }`}
              >
                <span>
                  {s.id === current && (
                    <span aria-hidden="true" className="mr-2 text-ink-lo">
                      ▸
                    </span>
                  )}
                  {s.label}
                </span>
                {s.count && (
                  <span className="shrink-0 font-mono text-[0.9375rem] tabular-nums text-ink-lo">
                    {s.count}
                  </span>
                )}
              </a>
            </li>
          ))}
        </ol>
      </Modal>
    </nav>
  );
}
