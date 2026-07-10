"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useRef, useCallback } from "react";


const BSKY_PROFILE_URL = "https://bsky.app/profile/civitas-research.bsky.social";

const NAV_LINKS: readonly { href: string; label: string; accent?: boolean }[] = [
  { href: "/action", label: "ACTION CENTER", accent: true },
  { href: "/politicians", label: "POLITICIANS" },
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

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + "/");

  return (
    <header
      role="banner"
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled || menuOpen ? "bg-crt-black/95 backdrop-blur-md" : "bg-transparent"
      } border-b border-matrix-green/10`}
    >
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[9999]
                   focus:bg-crt-black focus:text-neon-cyan focus:border focus:border-neon-cyan/60
                   focus:px-4 focus:py-2 focus:font-mono focus:text-xs focus:tracking-widest
                   focus:outline-none"
      >
        SKIP TO MAIN CONTENT
      </a>
    <nav
      aria-label="Main navigation"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
        <Link
          href="/"
          className="font-pixel text-[10px] sm:text-xs text-matrix-green hover:text-neon-cyan transition-colors tracking-widest"
        >
          CIVITAS
        </Link>

        {/* Desktop nav */}
        <div className="hidden sm:flex items-center gap-7">
          {NAV_LINKS.map(({ href, label, accent }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={
                  active
                    ? "text-neon-cyan font-mono text-xs tracking-widest uppercase transition-colors border-b border-neon-cyan/50 pb-0.5"
                    : accent
                      ? "text-neon-cyan/60 hover:text-neon-cyan font-mono text-xs tracking-widest uppercase transition-colors"
                      : "text-matrix-green/50 hover:text-matrix-green/90 font-mono text-xs tracking-widest uppercase transition-colors"
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
            className="text-neon-cyan/50 hover:text-neon-cyan font-mono text-xs tracking-widest transition-colors"
          >
            [🦋 BSKY]
          </a>
        </div>

        {/* Mobile hamburger */}
        <button
          ref={toggleRef}
          className="sm:hidden text-matrix-green/70 hover:text-matrix-green font-mono text-sm tracking-widest transition-colors"
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
          className="sm:hidden bg-crt-black/98 border-t border-matrix-green/10 px-6 py-8 flex flex-col gap-5"
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
                    ? "text-neon-cyan font-mono text-sm tracking-widest uppercase transition-colors"
                    : accent
                      ? "text-neon-cyan/60 hover:text-neon-cyan font-mono text-sm tracking-widest uppercase transition-colors"
                      : "text-matrix-green/50 hover:text-matrix-green font-mono text-sm tracking-widest uppercase transition-colors"
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
            className="text-neon-cyan/50 hover:text-neon-cyan font-mono text-sm tracking-widest transition-colors"
          >
            [🦋 BSKY]
          </a>
        </div>
      )}
    </nav>
    </header>
  );
}
