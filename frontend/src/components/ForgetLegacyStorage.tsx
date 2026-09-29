"use client";

import { useEffect } from "react";

/**
 * Keys earlier versions of the site wrote to the visitor's browser, and which
 * nothing reads any more:
 *
 * - `civitas_user_state` — the Action Center's "Personalize" state, which was
 *   also read by the compare page's "compare my senators" shortcut.
 * - `civitas_actions` — the "Log my action" diary and its streak count.
 * - `civitas_pulse_votes` — which issues this browser had voted on with
 *   "This concerns me / Not a priority".
 *
 * All three are gone because the site does not ask who or where a visitor is,
 * and does not keep a record of them across visits (AGENTS.md §8). Removing
 * the features stops new writes; this removes what earlier visits left behind,
 * so the About page's "what we record about you" is also true of the
 * browser's own storage. Harmless when a key isn't there. It can be dropped
 * once enough time has passed that no returning visitor still has one.
 */
const LEGACY_KEYS = ["civitas_user_state", "civitas_actions", "civitas_pulse_votes"];

export default function ForgetLegacyStorage() {
  useEffect(() => {
    try {
      for (const key of LEGACY_KEYS) localStorage.removeItem(key);
    } catch {
      // Storage blocked (private mode, disabled site data): nothing to remove.
    }
  }, []);
  return null;
}
