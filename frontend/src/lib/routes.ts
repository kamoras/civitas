/**
 * Shared hrefs for in-app navigation targets that need a specific URL shape.
 */

/**
 * Where in-app links should point for the Action Center's default (issues) view.
 *
 * Note the explicit `?tab=issues` — it is load-bearing, not decoration. `/action`
 * is a statically prerendered route, and Next's client router reuses the cached
 * entry's search string when a soft navigation targets the same route with an
 * *empty* search. So once a session has loaded `/action?tab=timeline` (a shared
 * link, a bookmark, a refresh), every later `<Link href="/action">` in the app
 * lands back on the timeline tab with `?tab=timeline` still in the address bar —
 * the navbar's own Action Center link could not return you to the default view.
 * A link that names its tab is never reused this way, which is what makes this
 * immune.
 *
 * Bare `/action` stays valid as a public entry point: a cold page load has no
 * client router cache to restore from and renders the issues tab as normal. This
 * constant is only about links followed *inside* an already-running session.
 */
export const ACTION_CENTER_HREF = "/action?tab=issues";

/** The Action Center's national-monitors tab. Same reasoning as above. */
export const ACTION_CENTER_MONITORS_HREF = "/action?tab=monitors";

/**
 * One national monitor, opened in place on the Action Center's monitors tab.
 * The tab is named for the same reason as above; `monitor` is read once, when
 * the tab mounts, to expand and scroll to that row.
 */
export function monitorHref(slug: string): string {
  return `${ACTION_CENTER_MONITORS_HREF}&monitor=${encodeURIComponent(slug)}`;
}

/** The Today tab's address: the day being shown (when it isn't the live
 *  view) and the issue expanded on it, so a reload or a shared link opens
 *  the same thing. Writing one without the other opened a different day. */
export function issuesUrl(date: string | null, issue: string | null): string {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (issue) params.set("issue", issue);
  const q = params.toString();
  return q ? `/action?${q}` : "/action";
}
