"use client";

import Link from "next/link";
import { useState, useEffect } from "react";

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 50);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      role="banner"
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled || menuOpen ? "bg-[#0a0a0a] backdrop-blur-sm" : "bg-transparent"
      } border-b border-matrix-green/20`}
    >
    <nav
      aria-label="Main navigation"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
        <Link
          href="/"
          className="font-pixel text-[10px] sm:text-xs text-matrix-green hover:text-neon-cyan transition-colors neon-green"
        >
          [CIVITAS]
        </Link>

        {/* Desktop nav */}
        <div className="hidden sm:flex items-center gap-6 text-lg">
          <Link
            href="/action"
            className="text-neon-cyan/70 hover:text-neon-cyan transition-colors font-pixel text-sm"
          >
            {"> ACTION CENTER"}
          </Link>
          <Link
            href="/scorecard"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
          >
            {"> SCORECARD"}
          </Link>
          <Link
            href="/leaderboard"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
          >
            {"> LEADERBOARD"}
          </Link>
          <Link
            href="/explore"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
          >
            {"> EXPLORE"}
          </Link>
          <Link
            href="/about"
            className="text-matrix-green/50 hover:text-matrix-green transition-colors"
          >
            {"> ABOUT"}
          </Link>
        </div>

        {/* Mobile hamburger */}
        <button
          className="sm:hidden text-matrix-green text-2xl"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
          aria-expanded={menuOpen}
          aria-controls="mobile-menu"
        >
          {menuOpen ? "[X]" : "[=]"}
        </button>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <div
          id="mobile-menu"
          className="sm:hidden bg-[#0a0a0a] border-t border-matrix-green/20 px-4 py-6 flex flex-col gap-4 text-xl"
        >
          <Link
            href="/action"
            className="text-neon-cyan/70 hover:text-neon-cyan transition-colors font-pixel text-base"
            onClick={() => setMenuOpen(false)}
          >
            {"> ACTION CENTER"}
          </Link>
          <Link
            href="/scorecard"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> SCORECARD"}
          </Link>
          <Link
            href="/leaderboard"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> LEADERBOARD"}
          </Link>
          <Link
            href="/explore"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> EXPLORE"}
          </Link>
          <Link
            href="/about"
            className="text-matrix-green/50 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> ABOUT"}
          </Link>
        </div>
      )}
    </nav>
    </header>
  );
}
