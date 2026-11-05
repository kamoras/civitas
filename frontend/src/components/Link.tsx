"use client";

import NextLink from "next/link";
import { useSyncExternalStore, type ComponentProps } from "react";

function subscribe(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

const shownToAPerson = () => !navigator.webdriver && document.visibilityState === "visible";

/**
 * Whether the page may prefetch: only once it is in view, in a browser no
 * automation drives (navigator.webdriver). The server render and hydration
 * say no (nothing is known there), and the page says yes as soon as it can.
 *
 * Why it matters: a prefetch is what confirms a visit (src/proxy.ts,
 * lib/visitSignal.ts, backend app/api/visits.py `_confirmed_paths`). From
 * 2026-10-23 a crawler running full Chrome from rotating cloud addresses ran
 * every page and prefetched its links, and was ~760 of a day's ~790
 * confirmed visitors while sending none of the load timings a page in view
 * outside automation sends (AGENTS.md §8). A tab opened in the background
 * prefetches when the reader turns to it.
 */
export function usePrefetchAllowed(): boolean {
  return useSyncExternalStore(subscribe, shownToAPerson, () => false);
}

// next/link's own props, which mean nothing on a plain <a>.
const NEXT_LINK_ONLY = new Set([
  "href",
  "as",
  "replace",
  "scroll",
  "shallow",
  "passHref",
  "prefetch",
  "locale",
  "legacyBehavior",
  "onNavigate",
]);

const automated = () => Boolean(navigator.webdriver);

/** Whether automation drives this browser (navigator.webdriver). False on
 *  the server and while hydrating. */
export function useAutomated(): boolean {
  return useSyncExternalStore(subscribe, automated, () => false);
}

/** next/link, prefetching only on a page a person is shown (see
 *  usePrefetchAllowed). Every in-app link goes through this; importing
 *  next/link directly anywhere else fails Link.test.tsx.
 *
 *  Under automation it is a plain link: a click on next/link fetches the
 *  next page's data from the running page, a same-origin script request the
 *  visit counter reads as a browser running the page (lib/visitSignal.ts),
 *  so a driven browser clicking through links was counted although it
 *  never prefetched. A plain link loads the next page as a page request,
 *  which is held and never confirmed. */
export default function Link(props: ComponentProps<typeof NextLink>) {
  const allowed = usePrefetchAllowed();
  const isAutomated = useAutomated();
  if (isAutomated && typeof props.href === "string") {
    const rest = Object.fromEntries(
      Object.entries(props).filter(([name]) => !NEXT_LINK_ONLY.has(name))
    ) as ComponentProps<"a">;
    return <a {...rest} href={props.href} />;
  }
  return <NextLink {...props} prefetch={allowed ? props.prefetch : false} />;
}
