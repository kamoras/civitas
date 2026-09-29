import { useCallback, useSyncExternalStore } from "react";

/**
 * The URL's hash once the browser's location is `path`, or null until then.
 *
 * Reading `window.location.hash` during render is wrong on a soft (in-app)
 * navigation: the new page's first client render runs BEFORE Next commits
 * the new URL, so it still sees the page the reader came from — a
 * `<Link href="/elections/states/GA#race-…">` from /elections read "" and
 * a page that latched that never saw its #race- link. Null ("not here yet")
 * is what the server render and hydration get too, since there is no URL
 * to read there.
 *
 * Next commits the URL with history.pushState, which fires no event, so
 * besides popstate/hashchange the subscription re-checks for a few frames
 * after mount. React also re-reads the snapshot after every commit, which
 * is where a soft navigation's URL usually shows up.
 */
export function useHashAt(path: string): string | null {
  const getSnapshot = useCallback(
    () => (samePath(window.location.pathname, path) ? window.location.hash : null),
    [path]
  );
  return useSyncExternalStore(subscribe, getSnapshot, serverSnapshot);
}

/** Exported for tests that must outlast every re-check frame. */
export const RECHECK_FRAMES = 30;

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener("hashchange", onChange);
  let frames = 0;
  let raf =
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame(function tick() {
          onChange();
          raf = ++frames < RECHECK_FRAMES ? requestAnimationFrame(tick) : 0;
        })
      : 0;
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener("hashchange", onChange);
    if (raf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(raf);
  };
}

function serverSnapshot(): null {
  return null;
}

/** Case- and trailing-slash-insensitive: /elections/states/ga serves the
 * same page as /elections/states/GA. */
function samePath(a: string, b: string): boolean {
  const norm = (p: string) => {
    let s = p;
    try {
      s = decodeURIComponent(p);
    } catch {
      // keep it as given
    }
    return s.replace(/\/+$/, "").toLowerCase();
  };
  return norm(a) === norm(b);
}
