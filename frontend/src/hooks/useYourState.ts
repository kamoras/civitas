import { useCallback } from "react";
import { useSessionValue } from "@/hooks/useSessionToken";
import { STATE_CODES } from "@/lib/stateCodes";

/** The sessionStorage key for the reader's state, this tab only. */
export const YOUR_STATE_KEY = "civitas:your-state";

const KNOWN = new Set<string>(STATE_CODES);

/**
 * The state the reader chose in the header, for the open tab only
 * (AGENTS.md §8): sessionStorage, gone when the tab closes, never a cookie
 * or localStorage, never sent to the server except as the `?state=` a page
 * already puts in its own URL. Only a known postal code is kept or
 * returned, since it goes into hrefs.
 */
export function useYourState(): {
  state: string;
  ready: boolean;
  setState: (code: string) => void;
} {
  const { value, ready, set } = useSessionValue(YOUR_STATE_KEY);
  const setState = useCallback((code: string) => set(KNOWN.has(code) ? code : null), [set]);
  return { state: value && KNOWN.has(value) ? value : "", ready, setState };
}
