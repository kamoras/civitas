"use client";

import { useEffect } from "react";
import { sendLoadTiming } from "@/lib/api";

// Module-level, not a ref: React's dev-mode double effect would otherwise
// report every load twice, and a remount must not report a load that
// already happened.
let reported = false;

/**
 * Reports this hard page load's timing once, after the load event, for the
 * admin dashboard's load-time charts. Soft (client-side) navigations have no
 * Navigation Timing entry and are not measured — the number is "how long a
 * cold visit takes", which is the one this host's hardware decides.
 *
 * A page that was hidden at any point before it finished loading is skipped:
 * a background tab's timers and rendering are throttled, so its "load time"
 * measures the browser's scheduling rather than the site, and a few of them
 * are enough to drag p95 somewhere nobody actually waited.
 */
export default function LoadTimingBeacon() {
  useEffect(() => {
    if (reported) return;
    const path = window.location.pathname;
    // The admin dashboard is not reader traffic.
    if (path.startsWith("/admin")) return;

    let hidden = document.visibilityState !== "visible";
    const onVisibility = () => {
      if (document.visibilityState !== "visible") hidden = true;
    };
    document.addEventListener("visibilitychange", onVisibility);

    let timer: ReturnType<typeof setTimeout> | undefined;
    const send = () => {
      // loadEventEnd is only filled in after every load handler returns, so
      // read it on the next task rather than inside the load event itself.
      timer = setTimeout(() => {
        document.removeEventListener("visibilitychange", onVisibility);
        if (hidden || reported) return;
        const nav = performance.getEntriesByType("navigation")[0] as
          PerformanceNavigationTiming | undefined;
        if (!nav || nav.loadEventEnd <= 0) return;
        const fcp = performance.getEntriesByName("first-contentful-paint")[0];
        reported = true;
        sendLoadTiming({
          path,
          ttfb: nav.responseStart,
          fcp: fcp ? fcp.startTime : null,
          load: nav.loadEventEnd,
        });
      }, 0);
    };

    if (document.readyState === "complete") send();
    else window.addEventListener("load", send, { once: true });

    return () => {
      window.removeEventListener("load", send);
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer) clearTimeout(timer);
    };
  }, []);

  return null;
}
