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
    <nav
      aria-label="Main navigation"
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled || menuOpen ? "bg-[#0a0a0a] backdrop-blur-sm" : "bg-transparent"
      } border-b border-matrix-green/20`}
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
          <Link href="/" className="text-matrix-green/70 hover:text-matrix-green transition-colors">
            {"> HOME"}
          </Link>
          <Link
            href="/scorecard"
            className="text-neon-pink/70 hover:text-neon-pink transition-colors"
          >
            {"> SCORECARD"}
          </Link>
          <Link
            href="/leaderboard"
            className="text-neon-yellow/70 hover:text-neon-yellow transition-colors"
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
            className="text-neon-cyan/70 hover:text-neon-cyan transition-colors"
          >
            {"> ABOUT"}
          </Link>
        </div>

        {/* Mobile hamburger */}
        <button
          className="sm:hidden text-matrix-green text-2xl"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Navigation menu"
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
          role="menu"
          className="sm:hidden bg-[#0a0a0a] border-t border-matrix-green/20 px-4 py-6 flex flex-col gap-4 text-xl"
        >
          <Link
            href="/"
            role="menuitem"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> HOME"}
          </Link>
          <Link
            href="/scorecard"
            role="menuitem"
            className="text-neon-pink/70 hover:text-neon-pink transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> SCORECARD"}
          </Link>
          <Link
            href="/leaderboard"
            role="menuitem"
            className="text-neon-yellow/70 hover:text-neon-yellow transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> LEADERBOARD"}
          </Link>
          <Link
            href="/explore"
            role="menuitem"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> EXPLORE"}
          </Link>
          <Link
            href="/about"
            role="menuitem"
            className="text-neon-cyan/70 hover:text-neon-cyan transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> ABOUT"}
          </Link>
        </div>
      )}
    </nav>
  );
}
