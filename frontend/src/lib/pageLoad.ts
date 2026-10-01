/** Whether a request reaching the middleware is someone opening a page, as
 *  opposed to the router fetching a page's data.
 *
 *  Next prefetches every <Link> that scrolls into view (only under `next
 *  build`, never `next dev`), and every one passes through the middleware:
 *  one homepage load was counted as 30 page views across 13 routes. The
 *  middleware cannot tell those from a click — Next strips `RSC`,
 *  `Next-Router-Prefetch` and `_rsc` before it runs (checked under `next
 *  build`), and a click on a prefetched link sends no request at all. So
 *  the middleware counts only document loads, and NavigationBeacon counts
 *  navigations inside the app.
 *
 *  A client that sends no Sec-Fetch-Dest is not a browser opening a page and
 *  is not counted. Every current browser sends `Sec-Fetch-Dest: document` on
 *  a page load (Chrome since 2020, Firefox 2021, Safari 16.4 in 2023); a
 *  crawler, a script or `curl` sends nothing. Counting those "as before" made
 *  crawlers two thirds of the unique visitors on 2026-10-01 (971 of 1,452,
 *  user agents naming no browser and no operating system). */
export function isPageLoad(headers: Headers): boolean {
  const purpose =
    `${headers.get("sec-purpose") ?? ""} ${headers.get("purpose") ?? ""}`.toLowerCase();
  if (purpose.includes("prefetch") || purpose.includes("prerender")) return false;
  const dest = headers.get("sec-fetch-dest");
  return dest === "document";
}
