/** What a request reaching the proxy tells the visit counter
 *  (backend/app/api/visits.py, `_confirmed_paths`), or null for nothing.
 *
 *  - "page": a browser asking for a page. Held, not counted: a crawler
 *    sends the same headers (October 2026, ~7,000 of a day's 7,357 counted
 *    visitors never ran a page).
 *  - "router": a same-origin request the page's own script makes once it
 *    runs in a browser — in practice Next's router prefetching the links in
 *    view. It counts the page that client opened.
 *
 *  Both are requests the site needs in order to work, so a reader whose
 *  blocker refuses tracking requests is counted without anything more being
 *  sent. Next strips `RSC`, `Next-Router-Prefetch` and `_rsc` before the
 *  proxy runs (checked under `next build`), so a router request is known by
 *  its fetch metadata: the browser sets `Sec-Fetch-*` itself, and only a
 *  script fetch() is `empty`. The proxy's matcher keeps out the other
 *  fetches a page makes (/api, /data). */
export type VisitSignal = "page" | "router";

export function visitSignal(headers: Headers): VisitSignal | null {
  const dest = headers.get("sec-fetch-dest");
  if (dest === "document") {
    // The browser fetching a page it may never show (a speculative
    // prefetch or prerender) is no one opening it.
    const purpose =
      `${headers.get("sec-purpose") ?? ""} ${headers.get("purpose") ?? ""}`.toLowerCase();
    return purpose.includes("prefetch") || purpose.includes("prerender") ? null : "page";
  }
  return dest === "empty" && headers.get("sec-fetch-site") === "same-origin" ? "router" : null;
}
