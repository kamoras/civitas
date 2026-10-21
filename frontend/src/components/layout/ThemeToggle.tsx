"use client";

import { useSyncExternalStore } from "react";

/** The per-tab key the footer's choice is kept under. sessionStorage only:
 *  it lasts for this tab and is never a record of the visitor (AGENTS.md
 *  section 8). */
export const THEME_KEY = "civitas-theme";

/** Applies a choice made earlier in this tab before the first paint, so the
 *  page never flashes the other theme. First in <body>, so it runs before any
 *  of the page is drawn (app/layout.tsx says why not in <head>). */
export const THEME_BOOT = `try{var t=sessionStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

type Theme = "light" | "dark";

function current(): Theme {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const CHANGED = "civitas-theme-change";

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  media?.addEventListener("change", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    media?.removeEventListener("change", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

/**
 * Light (the default) or dark, overriding the system preference for this
 * tab. A button whose label names the theme it switches to.
 */
export default function ThemeToggle() {
  // Null on the server: the system preference is only known in the browser.
  const theme = useSyncExternalStore<Theme | null>(subscribe, current, () => null);
  if (!theme) return null;
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => {
        document.documentElement.dataset.theme = next;
        try {
          window.sessionStorage.setItem(THEME_KEY, next);
        } catch {
          // Storage refused (a private window): the choice holds for this page.
        }
        window.dispatchEvent(new Event(CHANGED));
      }}
      className="inline-flex min-h-11 items-center text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-ink-hi"
    >
      {next === "dark" ? "Dark theme" : "Light theme"}
    </button>
  );
}
