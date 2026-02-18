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
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled ? "bg-crt-black/95 backdrop-blur-sm" : "bg-transparent"
      } border-b border-matrix-green/20`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
        <Link
          href="/"
          className="font-pixel text-[10px] sm:text-xs text-matrix-green hover:text-neon-cyan transition-colors neon-green"
        >
          [M/P]
        </Link>

        {/* Desktop nav */}
        <div className="hidden sm:flex items-center gap-6 text-lg">
          <Link href="/" className="text-matrix-green/70 hover:text-matrix-green transition-colors">
            {"> HOME"}
          </Link>
          <Link
            href="/senator-scorecard"
            className="text-neon-pink/70 hover:text-neon-pink transition-colors"
          >
            {"> SENATOR_SCORECARD"}
          </Link>
          <Link
            href="/leaderboard"
            className="text-neon-yellow/70 hover:text-neon-yellow transition-colors"
          >
            {"> LEADERBOARD"}
          </Link>
        </div>

        {/* Mobile hamburger */}
        <button
          className="sm:hidden text-matrix-green text-2xl"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Toggle menu"
        >
          {menuOpen ? "[X]" : "[=]"}
        </button>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <div className="sm:hidden bg-crt-black/98 border-t border-matrix-green/20 px-4 py-6 flex flex-col gap-4 text-xl">
          <Link
            href="/"
            className="text-matrix-green/70 hover:text-matrix-green transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> HOME"}
          </Link>
          <Link
            href="/senator-scorecard"
            className="text-neon-pink/70 hover:text-neon-pink transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> SENATOR_SCORECARD"}
          </Link>
          <Link
            href="/leaderboard"
            className="text-neon-yellow/70 hover:text-neon-yellow transition-colors"
            onClick={() => setMenuOpen(false)}
          >
            {"> LEADERBOARD"}
          </Link>
        </div>
      )}
    </nav>
  );
}
