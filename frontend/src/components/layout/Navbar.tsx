"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useRef, useCallback } from "react";
import { ACTION_CENTER_HREF } from "@/lib/routes";
import RecordsBand from "./RecordsBand";

const BSKY_PROFILE_URL = "https://bsky.app/profile/civitas-research.org";

const NAV_LINKS: readonly { href: string; label: string; accent?: boolean }[] = [
  { href: ACTION_CENTER_HREF, label: "ACTION CENTER", accent: true },
  { href: "/bills", label: "BILLS" },
  { href: "/politicians", label: "POLITICIANS" },
  { href: "/elections", label: "ELECTIONS" },
  { href: "/leaderboard", label: "LEADERBOARD" },
  { href: "/compare", label: "COMPARE" },
  { href: "/explore", label: "EXPLORE" },
  { href: "/about", label: "ABOUT" },
];

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
  if (active) return `${base} border-phos text-phos`;
  return `${base} border-transparent ${accent ? "text-ink-hi hover:text-phos" : "text-ink-lo hover:text-ink-hi"}`;
}

export default function Navbar() {
  const pathname = usePathname();
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
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

  // usePathname() never includes a query string, so compare against the href's
  // path only — ACTION_CENTER_HREF carries a ?tab=, and a raw === would leave
  // the Action Center link permanently un-highlighted.
  const isActive = (href: string) => {
    const path = href.split("?")[0];
    return pathname === path || pathname.startsWith(path + "/");
  };

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
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[9999] focus:bg-surface-base focus:text-phos focus:border focus:border-phos/60
                   focus:px-4 focus:py-2 focus:font-mono focus:text-xs focus:tracking-widest
                   focus:outline-none"
      >
        SKIP TO MAIN CONTENT
      </a>
      <nav aria-label="Main navigation" className="border-b-3 border-white/15">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
          <Link
            href="/"
            className="font-pixel text-xs sm:text-sm text-ink-hi hover:text-phos transition-colors tracking-widest"
          >
            CIVITAS
          </Link>

          {/*
          Two link rows, not one, because one row cannot serve 640px and
          1440px at the same size.

          Nine wide-tracked uppercase links measure 793px at the full
          treatment, so they need ~975px of viewport before they fit beside
          the wordmark — which is why the full row starts at `lg`. Below that
          they used to wrap, and with the records band above them a wrapped
          row made a 114px header between 640 and 740px, past the clearance
          every page reserves. Content got clipped site-wide.

          The earlier fix held the menu button all the way to `lg`, which
          handed a hamburger to every tablet. Instead `md` (768px) now gets a
          compact row: tighter gap, normal tracking, and no [BSKY] (which the
          footer carries anyway). That measures 548px against a 588px budget
          at 768px — the narrowest width it renders at.

          `flex-nowrap` is the guard, not the layout: if a fallback face ever
          measures wider than Share Tech Mono, the row overflows — which the
          route audit catches as horizontal scroll — rather than silently
          wrapping and clipping the top of every page again.

          Measured across 320-1536px in all five records-band states.
        */}
          {/* Compact row: md up to lg */}
          <div className="hidden md:flex lg:hidden items-center gap-3 flex-nowrap">
            {NAV_LINKS.map(({ href, label, accent }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`tracking-[0.025em] ${navLinkClass(active, accent, "xs")}`}
                >
                  {label}
                </Link>
              );
            })}
          </div>

          {/* Full row: lg and up */}
          <div className="hidden lg:flex items-center gap-7 flex-nowrap">
            {NAV_LINKS.map(({ href, label, accent }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`tracking-widest ${navLinkClass(active, accent, "xs")}`}
                >
                  {label}
                </Link>
              );
            })}
            <a
              href={BSKY_PROFILE_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Civitas on Bluesky (opens in new tab)"
              title="Follow Civitas on Bluesky"
              className="text-ink-lo hover:text-phos font-mono text-xs tracking-widest transition-colors"
            >
              [BSKY]
            </a>
          </div>

          {/* Mobile hamburger */}
          <button
            ref={toggleRef}
            className="md:hidden text-ink-lo hover:text-ink-hi font-mono text-sm tracking-widest transition-colors"
            onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-expanded={menuOpen}
            aria-controls="mobile-menu"
          >
            {menuOpen ? "CLOSE" : "MENU"}
          </button>
        </div>

        {/* Mobile menu */}
        {menuOpen && (
          <div
            ref={menuRef}
            id="mobile-menu"
            className="md:hidden bg-surface-base/[0.98] border-t border-white/[0.07] px-6 py-8 flex flex-col items-start gap-5"
          >
            {NAV_LINKS.map(({ href, label, accent }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  onClick={closeMenu}
                  className={`self-start tracking-widest ${navLinkClass(active, accent, "sm")}`}
                >
                  {label}
                </Link>
              );
            })}
            <a
              href={BSKY_PROFILE_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Civitas on Bluesky (opens in new tab)"
              onClick={closeMenu}
              className="text-ink-lo hover:text-phos font-mono text-sm tracking-widest transition-colors"
            >
              [BSKY]
            </a>
          </div>
        )}
      </nav>
    </header>
  );
}
