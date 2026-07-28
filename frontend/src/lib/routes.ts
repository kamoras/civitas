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
