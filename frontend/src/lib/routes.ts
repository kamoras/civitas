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

/** The Action Center's Archive tab (the year-in-review timeline). Same
 * reasoning as above. */
export const ACTION_CENTER_ARCHIVE_HREF = "/action?tab=timeline";

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

/** A federal agency's page, by its Federal Register slug. */
export function agencyHref(slug: string): string {
  return `/agencies/${encodeURIComponent(slug)}`;
}

/** Explore, filtered to an agency's documents (its sub-agencies' included). */
export function agencyExploreHref(slug: string): string {
  return `/explore?agency=${encodeURIComponent(slug)}`;
}

/**
 * The elections archive: every general election Civitas has kept a record
 * of (`/elections/archive`), and each one's page (`/elections/{cycle}`).
 *
 * The last tab of `SECTIONS.elections`, and the tab an archive page marks
 * as its own. The index never goes empty: before the first archive it says
 * when one starts.
 */
export const ELECTIONS_ARCHIVE_HREF = "/elections/archive";

/** One archived general election's page. */
export function electionArchiveHref(cycle: number): string {
  return `/elections/${cycle}`;
}

/** A race on its archived election's page (the anchor the page gives it,
 *  and the one the old `/elections/{raceId}` links resolve to). */
export function archivedRaceHref(cycle: number, raceId: string): string {
  return `${electionArchiveHref(cycle)}#race-${raceId}`;
}

/**
 * Each section's sub-pages, in the order its tab bar lists them: the one
 * table `SectionTabs` reads, so a page added here appears in every bar of its
 * section. Every tab is a page of its own (a link with `aria-current`, not an
 * in-page ARIA tab), and none is ever dropped for the election phase: a page
 * out of its phase (the seat map from election day on) says so itself, so
 * the tab still leads somewhere that explains. The hrefs repeat
 * `ELECTION_MAP_HREF`, `REDISTRICTING_HREF` and `presidentSupportHref("house")`
 * as literals because those modules import this one; `routes.test.ts` holds
 * them equal.
 */
export const SECTIONS = {
  congress: {
    label: "Congress",
    tabs: [
      { key: "reports", label: "Reports", href: "/congress" },
      { key: "bills", label: "Bills", href: "/congress/bills" },
      { key: "committees", label: "Committees", href: "/congress/committees" },
      // "Votes with the president", not "With the president": the page is
      // how often members voted with the president's stated positions.
      { key: "president", label: "Votes with the president", href: "/congress/president/house" },
    ],
  },
  elections: {
    label: "Elections",
    tabs: [
      { key: "ballots", label: "Ballots by state", href: "/elections" },
      { key: "map", label: "Seat map", href: "/elections/map?chamber=senate&view=usual" },
      { key: "redistricting", label: "New House maps", href: "/elections/redistricting" },
      { key: "archive", label: "Archive", href: ELECTIONS_ARCHIVE_HREF },
    ],
  },
} as const;

export type SectionKey = keyof typeof SECTIONS;
export type SectionTabKey<S extends SectionKey> = (typeof SECTIONS)[S]["tabs"][number]["key"];
