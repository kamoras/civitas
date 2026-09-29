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
 *  navigations inside the app. A client that sends no Sec-Fetch-Dest (not a
 *  browser) is counted as before. */
export function isPageLoad(headers: Headers): boolean {
  const purpose =
    `${headers.get("sec-purpose") ?? ""} ${headers.get("purpose") ?? ""}`.toLowerCase();
  if (purpose.includes("prefetch") || purpose.includes("prerender")) return false;
  const dest = headers.get("sec-fetch-dest");
  return dest === null || dest === "document";
}
