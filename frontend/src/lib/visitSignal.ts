/** What a request reaching the proxy tells the visit counter
 *  (backend/app/api/visits.py, `_confirmed_paths`), or null for nothing.
 *
 *  - "page": a browser asking for a page. Held, not counted: a crawler
 *    sends the same headers (October 2026, ~7,000 of a day's 7,357 counted
 *    visitors never ran a page).
 *  - "router": a same-origin request the page's own script makes once it
 *    runs in a browser — in practice Next's router prefetching the links in
 *    view. It counts the page that client opened. Links prefetch only on
 *    a page in view in a browser no automation drives
 *    (components/Link.tsx): a crawler running full Chrome was confirmed
 *    by its own prefetches from 2026-10-23.
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
    // A prefetched document runs no script until it is opened, and opening
    // it sends nothing, so it is no one opening the page. A prerendered one
    // runs, hidden, and is held like any page: its links prefetch only once
    // it is shown (components/Link.tsx), so a prerender never opened is
    // never confirmed, and one opened (Chrome's address bar prerenders a
    // site typed there) is counted, where skipping it lost the visit.
    const purpose =
      `${headers.get("sec-purpose") ?? ""} ${headers.get("purpose") ?? ""}`.toLowerCase();
    if (purpose.includes("prerender")) return "page";
    return purpose.includes("prefetch") ? null : "page";
  }
  return dest === "empty" && headers.get("sec-fetch-site") === "same-origin" ? "router" : null;
}
