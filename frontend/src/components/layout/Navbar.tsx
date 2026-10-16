"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useRef, useCallback } from "react";
import { SECTIONS, inSection, sectionSubpages, type SectionKey } from "@/lib/routes";
import { BSKY_PROFILE_URL } from "@/lib/site";
import RecordsBand from "./RecordsBand";

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

const SUBLINK =
  "flex min-h-11 items-center font-mono text-xs uppercase tracking-[0.12em] transition-colors";

/**
 * A section in the desktop row: its hub's link and, beside it, a button that
 * discloses the section's other pages (the WAI-ARIA disclosure navigation
 * pattern, "with top-level links": a button with aria-expanded and a plain
 * list of links, no menu role, so Tab walks it like any other links).
 * Escape closes it and returns focus to the button; so does focus or a
 * pointer leaving it.
 */
function SectionItem({
  section,
  active,
  open,
  onToggle,
  onClose,
  alignRight,
  pathname,
}: {
  section: SectionKey;
  active: boolean;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  alignRight: boolean;
  pathname: string;
}) {
  const { label, href } = SECTIONS[section];
  const subs = sectionSubpages(section);
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

  const panelId = `nav-pages-${section}`;
  return (
    <div ref={ref} className="relative flex items-center gap-0.5">
      <Link
        href={href}
        aria-current={active ? "page" : undefined}
        className={`tracking-normal lg:tracking-widest ${navLinkClass(active, section === ACCENT, "xs")}`}
      >
        {label.toUpperCase()}
      </Link>
      {subs.length > 0 && (
        <button
          ref={buttonRef}
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`${label} pages`}
          onClick={onToggle}
          className="inline-flex h-6 w-6 items-center justify-center font-mono text-xs text-ink-min transition-colors hover:text-phos aria-expanded:text-phos"
        >
          <span
            aria-hidden="true"
            className={`inline-block transition-transform ${open ? "rotate-180" : ""}`}
          >
            &#9662;
          </span>
        </button>
      )}
      {subs.length > 0 && (
        <ul
          id={panelId}
          hidden={!open}
          className={`absolute top-full z-10 mt-3 w-max min-w-56 max-w-80 border border-white/15 border-t-phos/50 bg-surface-base py-1 ${
            alignRight ? "right-0" : "left-0"
          }`}
        >
          {subs.map((t) => {
            const current = currentSubpage(section, t.href, pathname);
            return (
              <li key={t.key}>
                <Link
                  href={t.href}
                  aria-current={current ? "page" : undefined}
                  onClick={() => onClose()}
                  className={`${SUBLINK} px-4 ${current ? "text-ink-hi" : "text-ink-lo hover:bg-white/[0.04] hover:text-ink-hi"}`}
                >
                  <span aria-hidden="true" className="mr-2 text-ink-min">
                    &rsaquo;
                  </span>
                  {t.label}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default function Navbar() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // The desktop section whose pages are showing, and the page it was opened
  // on: a navigation by any route (a link, Back) closes it without an effect.
  const [opened, setOpened] = useState<{ section: SectionKey; path: string } | null>(null);
  const openSection = opened && opened.path === pathname ? opened.section : null;
  const closeSection = useCallback(() => setOpened(null), []);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 50);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const closeMenu = useCallback(() => {
    setMenuOpen(false);
    toggleRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!menuOpen || !menuRef.current) return;
    const firstLink = menuRef.current.querySelector<HTMLElement>("a[href]");
    firstLink?.focus();
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
        "a[href], button:not([disabled])"
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
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
      role="banner"
      className={`fixed top-0 left-0 right-0 z-50 transition-colors duration-300 ${
        scrolled || menuOpen ? "bg-surface-base/95 backdrop-blur-md" : "bg-surface-base/80"
      }`}
    >
      {/*
        The band lives inside the fixed header rather than above it so every
        page picks it up without touching its own layout: the header is ~79px
        tall with the band, and all 19 pages already clear a fixed navbar with
        `--header-clearance` (6rem). Keep it to one line on mobile — a second line would
        push past that margin.
      */}
      <RecordsBand />
      {/* The skip link is the root layout's, ahead of everything: a second
          one here cost keyboard users an extra Tab on every page. */}
      <nav aria-label="Main navigation" className="border-b-3 border-white/15">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <Link
            href="/"
            className="font-pixel text-xs sm:text-sm md:text-xs lg:text-sm text-ink-hi hover:text-phos transition-colors tracking-widest"
          >
            CIVITAS
          </Link>

          {/*
          One row from 768 px, compact below lg (tighter gap, normal
          tracking, no [BSKY], which the footer carries anyway) and full from
          lg. It was two rows, one per width, which each section's pages
          would have given two copies of every id.

          Measured under `next build`: seven sections with a 24 px pages
          button each are 599 px compact (no tracking, 8 px gaps, the
          wordmark at text-xs) beside the wordmark at 768 px, the narrowest
          width the row renders at, ending at x=740 of a 744 px content
          edge; and 839 px full at 1024 px, 25 px clear of the wordmark.

          `flex-nowrap` is the guard, not the layout: if a fallback face ever
          measures wider than Share Tech Mono, the row overflows — which the
          route audit catches as horizontal scroll — rather than silently
          wrapping and clipping the top of every page again.
        */}
          <div className="hidden md:flex items-center gap-2 lg:gap-6 flex-nowrap">
            {NAV.map((section, i) => (
              <SectionItem
                key={section}
                section={section}
                active={inSection(section, pathname)}
                open={openSection === section}
                onToggle={() =>
                  setOpened(openSection === section ? null : { section, path: pathname })
                }
                onClose={closeSection}
                alignRight={i >= NAV.length - 2}
                pathname={pathname}
              />
            ))}
            <a
              href={BSKY_PROFILE_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Civitas on Bluesky (opens in new tab)"
              title="Follow Civitas on Bluesky"
              className="hidden lg:inline text-ink-lo hover:text-phos font-mono text-xs tracking-widest transition-colors"
            >
              [BSKY]
            </a>
          </div>

          {/* Mobile hamburger. A 44x44 target (WCAG 2.5.8 asks 24, 2.5.5 44)
              around the same 20 px word: the negative margins give the
              padding back, so the bar's height and the word's place don't
              move. It was the 36x20 word alone, in the hardest thumb zone. */}
          <button
            ref={toggleRef}
            className="md:hidden -my-3 -mr-2 inline-flex min-h-11 min-w-11 items-center justify-center px-2 text-ink-lo hover:text-ink-hi font-mono text-sm tracking-widest transition-colors"
            onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-expanded={menuOpen}
            aria-controls="mobile-menu"
          >
            {menuOpen ? "CLOSE" : "MENU"}
          </button>
        </div>

        {/* Mobile menu: two levels, every section's pages listed under its
            hub, so any page is two taps from MENU (MENU, then the page).
            Every target is 44 px tall; the list scrolls inside the header
            when it is taller than the screen. */}
        {menuOpen && (
          <div
            ref={menuRef}
            id="mobile-menu"
            className="md:hidden max-h-[calc(100dvh-5.5rem)] overflow-y-auto overscroll-contain bg-surface-base/[0.98] border-t border-white/[0.07] px-6 pt-2 pb-6"
          >
            <ul>
              {NAV.map((section) => {
                const { label, href } = SECTIONS[section];
                const active = inSection(section, pathname);
                const subs = sectionSubpages(section);
                return (
                  <li key={section} className="border-b border-white/[0.07] py-1">
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      onClick={closeMenu}
                      className="flex min-h-11 items-center"
                    >
                      <span
                        className={`tracking-widest ${navLinkClass(active, section === ACCENT, "sm")}`}
                      >
                        {label.toUpperCase()}
                      </span>
                    </Link>
                    {subs.length > 0 && (
                      <ul
                        aria-label={`${label} pages`}
                        className="mb-1 grid grid-cols-2 gap-x-4 border-l border-white/15 pl-3"
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
