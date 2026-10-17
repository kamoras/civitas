"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useState, useEffect, useRef, useCallback } from "react";
import { SECTIONS, inSection, sectionSubpages, type SectionKey } from "@/lib/routes";
import { BSKY_PROFILE_URL } from "@/lib/site";
import RecordsBand, { RunStatusSquare } from "./RecordsBand";
import SiteSearch from "@/components/search/SiteSearch";
import YourStatePicker from "./YourStatePicker";

// The main navigation is the SECTIONS table (lib/routes.ts): each section's
// hub, with its other pages one step below it. Leaderboard and Compare are
// Politicians' pages, not two more top-level links: seven sections fit a
// 768 px row with a menu button each, nine did not.
const NAV = Object.keys(SECTIONS) as SectionKey[];
// The Action Center is the one section drawn in the brighter ink.
const ACCENT: SectionKey = "action";

/**
 * One definition of what a nav link looks like, for all three rows.
 *
 * The current page was a solid `bg-phos` pill with dark text. That is the one
 * thing the palette says phosphor must never be — "Data and wayfinding only —
 * status, rules, figures, link underlines. Never a fill behind a call to
 * action" (tailwind.config.ts) — and it made the loudest element on a page
 * about public records the fact that you were already on it. It is a rule
 * now, which is both the documented use and the same treatment the Action
 * Center's tab bar already gives its selected tab.
 *
 * The rule, not the colour, is what carries the state: colour alone would be
 * the only distinguisher for a reader who cannot see it, and `aria-current`
 * only reaches assistive tech.
 *
 * Three rows, one function, because they previously carried three copies of
 * this string that had already drifted apart on padding — `px-1.5 py-0.5`
 * against `px-2 py-0.5` against `self-start px-2 py-0.5` — for no reason
 * anyone recorded.
 */
function navLinkClass(active: boolean, accent: boolean | undefined, size: "sm" | "xs"): string {
  const base = `font-mono ${size === "xs" ? "text-xs" : "text-sm"} uppercase whitespace-nowrap border-b-2 pb-0.5 transition-colors`;
  if (active) return `${base} border-ink-hi text-ink-hi`;
  return `${base} border-transparent ${accent ? "text-ink-hi hover:text-phos" : "text-ink-lo hover:text-ink-hi"}`;
}

/** The path a tab's href names, for comparing with usePathname (which never
 * carries a query string or fragment). */
function pathOf(href: string): string {
  return href.split(/[?#]/)[0];
}

/** A sub-page is the current page when its path is, and no other page of
 * the section shares that path (the Action Center's three tabs are one
 * path, told apart by the query string, which this can't read without
 * suspending every page). */
function currentSubpage(section: SectionKey, href: string, pathname: string): boolean {
  const path = pathOf(href);
  return (
    path === pathname && SECTIONS[section].tabs.filter((t) => pathOf(t.href) === path).length === 1
  );
}

// Where the desktop row replaces MENU, and where it takes its full
// treatment, in rem so both move with the reader's text size (a larger
// default font or zoom reaches MENU sooner, never an overflowing row).
// Measured under `next build` at the default size: the compact row is
// 599 px beside a 116 px wordmark, so from 56rem (896 px) it has 149 px to
// spare; the full row is 839 px beside a 132 px wordmark, 261 px to spare
// from 80rem (1280 px). The margins are for a longer label or a wider
// fallback face; past them, useRowFits below switches to MENU.
// Written out in full where used (min-[56rem]:…, xl:…): Tailwind finds
// classes by scanning for whole strings, never a built one.
// The least space kept between the wordmark and the row.
const MIN_GAP_PX = 24;

/**
 * Whether the desktop row fits its bar, measured: the rem breakpoints
 * above are the layout, this is the guard behind them. If the row (or a
 * translated label, or a fallback face) is wider than the bar leaves, the
 * header takes the MENU layout at that width instead of overflowing or
 * wrapping past `--header-clearance`. A row that doesn't fit stays laid out
 * (invisible and inert), so it is still measured, and comes back as soon as
 * it fits. Without ResizeObserver (a test, an old browser) the
 * breakpoints alone decide.
 */
export function useRowFits(
  bar: React.RefObject<HTMLElement | null>,
  logo: React.RefObject<HTMLElement | null>,
  row: React.RefObject<HTMLElement | null>
): boolean {
  const [fits, setFits] = useState(true);
  useEffect(() => {
    const b = bar.current;
    if (!b || typeof ResizeObserver === "undefined") return;
    let needed = 0;
    const check = () => {
      const r = row.current;
      if (r && r.offsetWidth > 0)
        needed = (logo.current?.offsetWidth ?? 0) + r.scrollWidth + MIN_GAP_PX;
      const style = getComputedStyle(b);
      const room = b.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      setFits(needed === 0 || needed <= room);
    };
    const ro = new ResizeObserver(check);
    ro.observe(b);
    if (row.current) ro.observe(row.current);
    check();
    return () => ro.disconnect();
  }, [bar, logo, row]);
  return fits;
}

const SUBLINK =
  "flex min-h-11 items-center font-mono text-xs uppercase tracking-[0.12em] transition-colors";

/**
 * A section in the desktop row: one trigger, the section's name, that opens
 * a panel of all its pages (the WAI-ARIA disclosure navigation pattern: a
 * button with aria-expanded and a plain list of links, no menu role, so Tab
 * walks it like any other links). Escape closes it and returns focus to the
 * button; so does focus or a pointer leaving it. A section with one page
 * (Explore) is a plain link.
 *
 * It was the section's link with a separate 24 px ▾ button beside it, and
 * a narrow box floating under the label: two controls that looked like one,
 * a list that didn't line up with anything, and no hub in it. The panel is
 * now a band under the header's rule, on the page's own grid, as wide as
 * the content: the section's name and every one of its pages (the hub
 * first), in columns, the current page marked by a rule (Congress.gov and
 * GovTrack open section menus the same way: one trigger, the overview
 * inside). The links run down each column (CSS columns), the order Tab
 * walks them. Tab stops in the row went from 13 to 7.
 */
function SectionItem({
  section,
  active,
  open,
  onToggle,
  onClose,
  pathname,
}: {
  section: SectionKey;
  active: boolean;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  pathname: string;
}) {
  const { label, href, tabs } = SECTIONS[section];
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // While open: a pointer or focus leaving the item closes it, and Escape
  // closes it and returns focus to the button. Listeners, not handlers on
  // the item's <div>, which is not itself interactive.
  useEffect(() => {
    const item = ref.current;
    if (!open || !item) return;
    const onPointer = (e: PointerEvent) => {
      if (!item.contains(e.target as Node)) onClose();
    };
    const onFocusOut = (e: FocusEvent) => {
      if (!item.contains(e.relatedTarget as Node | null)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const inside = item.contains(document.activeElement);
      onClose();
      if (inside) buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    item.addEventListener("focusout", onFocusOut);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
      item.removeEventListener("focusout", onFocusOut);
    };
  }, [open, onClose]);

  const labelClass = `tracking-normal xl:tracking-widest ${navLinkClass(active, section === ACCENT, "xs")}`;
  if (tabs.length < 2) {
    return (
      <Link href={href} aria-current={active ? "page" : undefined} className={labelClass}>
        {label.toUpperCase()}
      </Link>
    );
  }
  const panelId = `nav-pages-${section}`;
  // `pathOf` match on the hub too: the hub is a page of the section like
  // any other here.
  const isCurrent = (h: string) =>
    h === href ? pathname === pathOf(href) : currentSubpage(section, h, pathname);
  return (
    // Not positioned: the panel spans the header (the nav is its
    // containing block), not this item.
    <div ref={ref} className="flex items-center">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className={`inline-flex min-h-6 items-center gap-1.5 ${labelClass} aria-expanded:border-ink-hi aria-expanded:text-ink-hi`}
      >
        {label.toUpperCase()}
        {active && <span className="sr-only">{", current section"}</span>}
        <span
          aria-hidden="true"
          className={`inline-block text-[0.6rem] text-ink-min transition-transform ${open ? "rotate-180" : ""}`}
        >
          &#9662;
        </span>
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="absolute inset-x-0 top-[calc(100%+3px)] z-10 border-b-3 border-white/15 bg-surface-base"
      >
        <div className="mx-auto grid max-w-7xl grid-cols-[12rem_minmax(0,1fr)] gap-x-9 px-4 py-5 sm:px-6">
          <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">{label}</p>
          <ul className="max-w-3xl columns-2 gap-x-9 xl:columns-3">
            {tabs.map((t) => {
              const current = isCurrent(t.href);
              return (
                <li key={t.key} className="break-inside-avoid">
                  <Link
                    href={t.href}
                    aria-current={current ? "page" : undefined}
                    onClick={() => onClose()}
                    className={`flex min-h-11 items-center border-l-2 pl-3 font-display text-sm transition-colors ${
                      current
                        ? "border-ink-hi text-ink-hi"
                        : "border-white/15 text-ink-lo hover:border-ink-lo hover:text-ink-hi"
                    }`}
                  >
                    {t.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}

/**
 * A phone's header hides while the reader scrolls down and comes back on any
 * scroll up (mobile spec P8): at rest it was 10% of every screen. Never
 * within the first screen, never with reduced motion asked for (it stays
 * put), never while focus is in it (any focus there shows it, WCAG 2.4.11),
 * and never while the menu or a sheet is open. Only in the MENU layout
 * (under 56rem). Tells the page through `data-header-hidden` on <html>, so
 * what sits under the header (the section bar, the Action Center's tabs)
 * moves up to the top edge with it.
 */
export function useHideOnScroll(
  header: React.RefObject<HTMLElement | null>,
  menuOpen: boolean
): boolean {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const el = header.current;
    if (!el || typeof window.matchMedia !== "function") return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)");
    const phone = window.matchMedia("(max-width: 55.99rem)");
    let last = window.scrollY;
    let frame = 0;
    const blocked = () =>
      menuOpen ||
      still.matches ||
      !phone.matches ||
      el.contains(document.activeElement) ||
      document.body.style.overflow === "hidden";
    const check = () => {
      frame = 0;
      const y = window.scrollY;
      if (blocked() || y < window.innerHeight) setHidden(false);
      else if (y > last + 4) setHidden(true);
      else if (y < last - 4) setHidden(false);
      last = y;
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(check);
    };
    const show = () => setHidden(false);
    window.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("focusin", show);
    still.addEventListener?.("change", show);
    phone.addEventListener?.("change", show);
    if (blocked()) show();
    return () => {
      window.removeEventListener("scroll", onScroll);
      el.removeEventListener("focusin", show);
      still.removeEventListener?.("change", show);
      phone.removeEventListener?.("change", show);
      cancelAnimationFrame(frame);
    };
  }, [header, menuOpen]);
  useEffect(() => {
    document.documentElement.toggleAttribute("data-header-hidden", hidden);
    return () => document.documentElement.removeAttribute("data-header-hidden");
  }, [hidden]);
  return hidden;
}

export default function Navbar() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  // The section whose pages are open in the phone menu: null for the
  // current section's, "" for none.
  const [shownPages, setShownPages] = useState<SectionKey | "" | null>(null);
  // The desktop section whose pages are showing, and the page it was opened
  // on: a navigation by any route (a link, Back) closes it without an effect.
  const [opened, setOpened] = useState<{ section: SectionKey; path: string } | null>(null);
  const openSection = opened && opened.path === pathname ? opened.section : null;
  const closeSection = useCallback(() => setOpened(null), []);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const logoRef = useRef<HTMLAnchorElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const rowFits = useRowFits(barRef, logoRef, rowRef);
  const headerRef = useRef<HTMLElement>(null);
  const hidden = useHideOnScroll(headerRef, menuOpen);
  // MENU's breakpoint, or always MENU when the row doesn't fit.
  const menuOnly = rowFits ? "min-[56rem]:hidden" : "";

  const closeMenu = useCallback(() => {
    setMenuOpen(false);
    setShownPages(null);
    toggleRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!menuOpen || !menuRef.current) return;
    // The panel itself, named "Site menu", so Tab's first stop is the
    // first section, then the rest, then the state picker after them. Never
    // the picker: a phone browser opens a native select's list when it takes
    // focus, so MENU popped the state list open on every tap.
    menuRef.current.focus();
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeMenu();
        return;
      }
      if (e.key !== "Tab" || !menuRef.current) return;
      const focusable = menuRef.current.querySelectorAll<HTMLElement>(
        "a[href], button:not([disabled]), select"
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const onPanel = document.activeElement === menuRef.current;
      if (e.shiftKey && (onPanel || document.activeElement === first)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen, closeMenu]);

  return (
    <header
      ref={headerRef}
      role="banner"
      // Solid, not a translucent blur: the header is paper, not glass.
      className={`fixed top-0 left-0 right-0 z-50 bg-surface-base transition-transform duration-200 motion-reduce:transition-none ${
        hidden ? "-translate-y-full" : ""
      }`}
    >
      {/*
        The band lives inside the fixed header rather than above it so every
        page picks it up without touching its own layout: the header is ~79px
        tall with the band, and all 19 pages already clear a fixed navbar with
        `--header-clearance` (6rem). Keep it to one line on mobile at ordinary sizes; at
        a large text size the bar wraps and HeaderClearance follows it.
      */}
      <RecordsBand aside={<YourStatePicker />} />
      {/* The skip link is the root layout's, ahead of everything: a second
          one here cost keyboard users an extra Tab on every page. */}
      <nav aria-label="Main navigation" className="relative border-b-3 border-white/15">
        <div
          ref={barRef}
          className="@container max-w-7xl mx-auto px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-y-2 [&>:last-child]:ml-auto"
        >
          {/* The lockup: the pixel mark, the site's one use of that face,
              and RESEARCH beside it in the mono, after a hairline. */}
          {/* In a bar under 20rem wide (a 320 px phone at the default text
              size) RESEARCH would push MENU off the bar, so the mark stands
              alone there; the link's name is the full one. A container
              query in rem, so a larger text size drops it sooner, whichever
              way the reader enlarged the text. Where even the mark and the buttons can't
              share a row (a phone at 200% text: 185 + 227 px in 256), the bar
              wraps, the buttons right-aligned on a second row, and
              HeaderClearance moves the pages' clearance down with it: one
              row clipped every page there (WCAG 1.4.10). */}
          {/* Under 56rem the records band is this square, left of the mark. */}
          <span className="flex items-center gap-1">
            <RunStatusSquare />
            <Link
              ref={logoRef}
              href="/"
              aria-label="Civitas Research"
              className="group inline-flex min-h-6 items-center gap-2.5 text-ink-hi"
            >
              <span className="font-pixel text-xs tracking-widest transition-colors group-hover:text-phos sm:text-sm min-[56rem]:text-xs xl:text-sm">
                CIVITAS
              </span>
              <span
                aria-hidden="true"
                className="hidden h-3.5 border-l border-ink-min/60 @min-[20rem]:inline sm:h-4"
              />
              <span className="hidden font-mono text-xs tracking-[0.18em] text-ink-lo @min-[20rem]:inline sm:tracking-[0.3em]">
                RESEARCH
              </span>
            </Link>
          </span>

          {/*
          One row from 56rem (896 px at the default text size), compact
          below xl (tighter gap, normal tracking, no [BSKY], which the footer
          carries anyway) and full from xl. It was two rows, one per width,
          which each section's pages would have given two copies of every
          id. The widths and margins are in the comment above useRowFits.

          It started at 768 px with 4 px to spare, so one longer label or a
          larger text size would have overflowed it. Now the breakpoints are
          in rem (they move with the text size) with ~150 px to spare, and
          useRowFits switches to MENU at any width where the measured row
          still doesn't fit. `flex-nowrap` stays: a wrapped row would push
          the header past `--header-clearance` and clip every page.
        */}
          <div
            ref={rowRef}
            data-testid="nav-row"
            // Not fitting, it stays laid out but invisible, inert and out of
            // the flow, so useRowFits can still measure it and bring it back
            // when it fits again (the window widens, the text shrinks).
            inert={!rowFits}
            className={`hidden min-[56rem]:flex items-center gap-2 xl:gap-6 flex-nowrap ${
              rowFits ? "" : "invisible pointer-events-none absolute right-0"
            }`}
          >
            {NAV.map((section) => (
              <SectionItem
                key={section}
                section={section}
                active={inSection(section, pathname)}
                open={openSection === section}
                onToggle={() =>
                  setOpened(openSection === section ? null : { section, path: pathname })
                }
                onClose={closeSection}
                pathname={pathname}
              />
            ))}
            <a
              href={BSKY_PROFILE_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Civitas on Bluesky (opens in new tab)"
              title="Follow Civitas on Bluesky"
              className="hidden xl:inline text-ink-lo hover:text-phos font-mono text-xs tracking-widest transition-colors"
            >
              [BSKY]
            </a>
            <SiteSearch className="min-h-6 text-xs" />
          </div>

          {/* Mobile hamburger. A 44x44 target (WCAG 2.5.8 asks 24, 2.5.5 44)
              around the same 20 px word: the negative margins give the
              padding back, so the bar's height and the word's place don't
              move. It was the 36x20 word alone, in the hardest thumb zone. */}
          {/* The MENU layout's search, beside MENU: the same dialog. */}
          <div className={`${menuOnly} -my-3 flex items-center`}>
            <SiteSearch className="min-h-11 min-w-11 justify-center px-2 text-sm" />
            <button
              ref={toggleRef}
              className={`-mr-2 inline-flex min-h-11 min-w-11 items-center justify-center px-2 text-ink-lo hover:text-ink-hi font-mono text-sm tracking-widest transition-colors`}
              onClick={() => {
                if (menuOpen) closeMenu();
                else setMenuOpen(true);
              }}
              aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
            >
              {menuOpen ? "CLOSE" : "MENU"}
            </button>
          </div>
        </div>

        {/* Mobile menu: two levels, every section's pages listed under its
            hub, so any page is two taps from MENU (MENU, then the page).
            Every target is 44 px tall; the list scrolls inside the header
            when it is taller than the screen. */}
        {menuOpen && (
          <div
            ref={menuRef}
            id="mobile-menu"
            role="region"
            aria-label="Site menu"
            tabIndex={-1}
            className={`${menuOnly} outline-none max-h-[calc(100dvh-3.5rem)] min-[56rem]:max-h-[calc(100dvh-5.5rem)] overflow-y-auto overscroll-contain bg-surface-base/[0.98] border-t border-white/[0.07] px-6 pt-2 pb-6`}
          >
            {/* Phone order (mobile spec P8): the sections first, as rows a
                thumb can hit, each section's pages behind its own ▸ (the
                current section's open); then the reader's state, which
                says what it set and where to go next; then Bluesky. It was
                25 caps links at one weight, the pages beside their
                sections. */}
            <ul>
              {NAV.map((section) => {
                const { label, href } = SECTIONS[section];
                const active = inSection(section, pathname);
                const subs = sectionSubpages(section);
                const expanded = (shownPages ?? (active ? section : null)) === section;
                const listId = `menu-pages-${section}`;
                return (
                  <li key={section} className="border-b border-white/[0.07]">
                    <div className="flex items-stretch">
                      <Link
                        href={href}
                        aria-current={active ? "page" : undefined}
                        onClick={closeMenu}
                        className={`flex min-h-13 flex-1 items-center font-sans text-lg ${
                          active
                            ? "text-ink-hi underline decoration-2 underline-offset-[6px]"
                            : section === ACCENT
                              ? "text-ink-hi hover:text-phos"
                              : "text-ink hover:text-ink-hi"
                        }`}
                      >
                        {label}
                      </Link>
                      {subs.length > 0 && (
                        <button
                          type="button"
                          aria-expanded={expanded}
                          aria-controls={listId}
                          aria-label={`${label} pages`}
                          onClick={() => setShownPages(expanded ? "" : section)}
                          className="flex min-h-13 min-w-13 items-center justify-center text-ink-lo hover:text-ink-hi"
                        >
                          <span
                            aria-hidden="true"
                            className={`inline-block transition-transform motion-reduce:transition-none ${expanded ? "rotate-90" : ""}`}
                          >
                            ▸
                          </span>
                        </button>
                      )}
                    </div>
                    {subs.length > 0 && (
                      <ul
                        id={listId}
                        hidden={!expanded}
                        aria-label={`${label} pages`}
                        className="mb-2 border-l border-white/15 pl-3"
                      >
                        {subs.map((t) => {
                          const current = currentSubpage(section, t.href, pathname);
                          return (
                            <li key={t.key}>
                              <Link
                                href={t.href}
                                aria-current={current ? "page" : undefined}
                                onClick={closeMenu}
                                className={`${SUBLINK} leading-snug ${current ? "text-ink-hi" : "text-ink-lo hover:text-ink-hi"}`}
                              >
                                {t.label}
                              </Link>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="border-b border-white/[0.07] py-3">
              <YourStatePicker size="lg" onNavigate={closeMenu} />
            </div>
            <a
              href={BSKY_PROFILE_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Civitas on Bluesky (opens in new tab)"
              onClick={closeMenu}
              className="mt-2 inline-flex min-h-11 items-center text-ink-lo hover:text-phos font-mono text-sm tracking-widest transition-colors"
            >
              [BSKY]
            </a>
          </div>
        )}
      </nav>
    </header>
  );
}
