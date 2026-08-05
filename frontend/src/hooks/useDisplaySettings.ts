"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Reader-controlled display settings (typography, palette, effects).
 *
 * Why this exists: the terminal aesthetic is deliberate, but it makes a set of
 * choices — a pixel-grid display font, fully-saturated hues on near-black, a
 * glow on the glyph edges, a scanline overlay — whose legibility cost depends
 * heavily on the reader's *hardware and room*, which no single default can get
 * right. A fractional display scale (Windows at 125%) renders the pixel font
 * off its 8-cell grid; a bright room makes a dark UI the worst-case polarity
 * (Piepenbrock et al., Ergonomics 2013); astigmatism (~47% of people) turns a
 * high-luminance saturated foreground into halation.
 *
 * WCAG 2.2 SC 1.4.8 Visual Presentation asks for exactly this remedy — let the
 * reader select foreground/background and spacing — rather than a fixed guess.
 *
 * Settings are applied as attributes on <html> (not React context) so the CSS
 * in globals.css can key off them globally without touching ~500 existing
 * Tailwind call sites.
 */

const KEY = "civitas_display_settings";
const EVENT = "civitas:displaysettings";

export type Theme = "dark" | "light";
export type TextScale = 100 | 112 | 125 | 150;

export interface DisplaySettings {
  /** "dark" = terminal palette; "light" = positive-polarity reading theme. */
  theme: Theme;
  /**
   * Swap the pixel/bitmap display fonts for the legible mono everywhere, and
   * drop the fully-saturated foreground for a low-chroma reading layer.
   */
  legible: boolean;
  /** Disable glow text-shadows, the CRT scanline overlay, and Matrix rain. */
  effects: boolean;
  /** Percentage applied to the root font size. */
  textScale: TextScale;
}

export const DEFAULT_SETTINGS: DisplaySettings = {
  theme: "dark",
  legible: false,
  effects: true,
  textScale: 100,
};

const VALID_SCALES: readonly number[] = [100, 112, 125, 150];

export function parseSettings(raw: string | null): DisplaySettings {
  if (!raw) return DEFAULT_SETTINGS;
  try {
    const p = JSON.parse(raw) as Partial<DisplaySettings>;
    return {
      theme: p.theme === "light" ? "light" : "dark",
      legible: p.legible === true,
      // Absent/!== false means "leave effects on" — matches the default.
      effects: p.effects !== false,
      textScale: (VALID_SCALES.includes(p.textScale as number) ? p.textScale : 100) as TextScale,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

/**
 * Push settings onto <html>. Kept as a standalone export so the inline
 * bootstrap script in layout.tsx applies the identical mapping before first
 * paint (no flash of the un-themed terminal palette).
 */
export function applySettings(s: DisplaySettings): void {
  const el = document.documentElement;
  el.dataset.theme = s.theme;
  el.dataset.legible = s.legible ? "on" : "off";
  el.dataset.effects = s.effects ? "on" : "off";
  el.style.setProperty("--civitas-text-scale", String(s.textScale / 100));
}

function subscribe(callback: () => void): () => void {
  window.addEventListener(EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

// useSyncExternalStore compares snapshots by identity, so parsing on every read
// would return a fresh object each time and loop forever. Cache by raw string.
let cachedRaw: string | null = null;
let cachedValue: DisplaySettings = DEFAULT_SETTINGS;

function getSnapshot(): DisplaySettings {
  const raw = localStorage.getItem(KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedValue = parseSettings(raw);
  }
  return cachedValue;
}

export function useDisplaySettings(): [DisplaySettings, (patch: Partial<DisplaySettings>) => void] {
  const settings = useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT_SETTINGS);

  const update = useCallback(
    (patch: Partial<DisplaySettings>) => {
      const next = { ...settings, ...patch };
      localStorage.setItem(KEY, JSON.stringify(next));
      applySettings(next);
      window.dispatchEvent(new Event(EVENT));
    },
    [settings]
  );

  return [settings, update];
}

export const DISPLAY_SETTINGS_KEY = KEY;
