"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { sendNavigation } from "@/lib/api";

// Module-level, not a ref: a remount (or React's dev-mode double effect)
// must not count the same page twice.
let lastPath: string | null = null;

/**
 * Counts the pages a reader opens inside the app after the first: a click
 * on a link Next has already prefetched sends the server no request, so
 * nothing but the page itself can tell. A query-string change (a tab) is
 * not a new page. The admin dashboard is not reader traffic.
 *
 * The first page is counted by the server, from requests the site needs
 * anyway (src/proxy.ts), so it is never sent from here. A reader whose
 * blocker refuses this request has the visit and that first page counted,
 * and the later pages not — their choice, and nothing works around it.
 */
export default function NavigationBeacon() {
  const pathname = usePathname();
  useEffect(() => {
    if (pathname === lastPath) return;
    const first = lastPath === null;
    lastPath = pathname;
    if (!first && !pathname.startsWith("/admin")) sendNavigation(pathname);
  }, [pathname]);
  return null;
}
