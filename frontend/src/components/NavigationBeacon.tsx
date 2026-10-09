"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { sendNavigation } from "@/lib/api";

// Module-level, not a ref: a remount (or React's dev-mode double effect)
// must not count the same page twice.
let lastPath: string | null = null;

/**
 * Counts a page view from the browser itself: the page it was opened on,
 * then every change of pathname after it (a click inside the app sends no
 * document request). A query-string change (a tab) is not a new page. The
 * admin dashboard is not reader traffic.
 *
 * Counted here, not when the server answers a page request: a request's
 * headers prove nothing. In October 2026 a crawler sending browser-like
 * headers (Sec-Fetch-Dest: document, a randomised Chrome User-Agent) from
 * thousands of rotating addresses was counted as ~7,000 of a day's 7,357
 * unique visitors; it never ran the page's script (one of its 1,269
 * addresses in an hour fetched a script file). A client that doesn't run
 * this page isn't a reader opening it. Next's link prefetches never
 * mount a page, so they aren't counted either.
 */
export default function NavigationBeacon() {
  const pathname = usePathname();
  useEffect(() => {
    if (pathname === lastPath) return;
    lastPath = pathname;
    if (!pathname.startsWith("/admin")) sendNavigation(pathname);
  }, [pathname]);
  return null;
}
