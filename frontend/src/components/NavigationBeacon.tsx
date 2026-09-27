"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { sendNavigation } from "@/lib/api";

// Module-level, not a ref: a remount (or React's dev-mode double effect)
// must not count the same page twice.
let lastPath: string | null = null;

/**
 * Counts a navigation inside the app as a page view. The first path is the
 * document load, which the middleware already counted; every change of
 * pathname after it is a click the middleware never sees (lib/pageLoad.ts).
 * A query-string change (a tab) is not a new page. The admin dashboard is
 * not reader traffic, as in the middleware's matcher.
 */
export default function NavigationBeacon() {
  const pathname = usePathname();
  useEffect(() => {
    if (lastPath === null) {
      lastPath = pathname;
      return;
    }
    if (pathname === lastPath) return;
    lastPath = pathname;
    if (!pathname.startsWith("/admin")) sendNavigation(pathname);
  }, [pathname]);
  return null;
}

