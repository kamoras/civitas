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
    const firstLink = menuRef.current.querySelector<HTMLElement>('a[href]');
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
        'a[href], button:not([disabled])'
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
        `pt-24` (96px). Keep it to one line on mobile — a second line would
        push past that margin.
      */}
      <RecordsBand />
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[9999]
                   focus:bg-surface-base focus:text-phos focus:border focus:border-phos/60
                   focus:px-4 focus:py-2 focus:font-mono focus:text-xs focus:tracking-widest
                   focus:outline-none"
      >
        SKIP TO MAIN CONTENT
      </a>
    <nav aria-label="Main navigation" className="border-b-3 border-white/15">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
        <Link
          href="/"
          className="font-pixel text-[10px] sm:text-xs text-ink-hi hover:text-phos transition-colors tracking-widest"
        >
          CIVITAS
        </Link>

        {/*
          `lg` (1024px), not `sm` (640px).

          Nine wide-tracked uppercase links do not fit on one row until about
          900px, so from 640px up they wrapped to two or three rows. On main
          that merely made a 41px header 57px, which nothing depended on. With
          the records band above it, the same wrap produced a 114px header
          between 640 and 740px — past the 96px (`pt-24`) all 19 pages reserve
          for the fixed header, clipping the top of their content. Measured
          across 320-1280px; a four-width spot check missed it entirely.

          Holding the menu button until the links genuinely fit is also the
          better call on its own: nine cramped links at 640px were never
          readable.
        */}
        {/* Desktop nav */}
        <div className="hidden lg:flex items-center gap-7">
          {NAV_LINKS.map(({ href, label, accent }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={
                  active
                    ? "bg-phos text-surface-base font-mono text-xs tracking-widest uppercase px-2 py-0.5"
                    : accent
                      ? "text-ink-hi hover:text-phos font-mono text-xs tracking-widest uppercase transition-colors"
                      : "text-ink-lo hover:text-ink-hi font-mono text-xs tracking-widest uppercase transition-colors"
                }
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
          className="lg:hidden text-ink-lo hover:text-ink-hi font-mono text-sm tracking-widest transition-colors"
          onClick={() => menuOpen ? closeMenu() : setMenuOpen(true)}
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
          className="lg:hidden bg-surface-base/[0.98] border-t border-white/[0.07] px-6 py-8 flex flex-col items-start gap-5"
        >
          {NAV_LINKS.map(({ href, label, accent }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                onClick={closeMenu}
                className={
                  active
                    ? "self-start bg-phos text-surface-base font-mono text-sm tracking-widest uppercase px-2 py-0.5"
                    : accent
                      ? "text-ink-hi hover:text-phos font-mono text-sm tracking-widest uppercase transition-colors"
                      : "text-ink-lo hover:text-ink-hi font-mono text-sm tracking-widest uppercase transition-colors"
                }
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
