import { ABOUT_CHAPTERS, ABOUT_OVERVIEW_HREF } from "./aboutPages";
import { COURT_HREF } from "./court";
import type { PoliticianBranch } from "@/types/politicians";

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

/** Special elections, vacant seats and every federal election date and
 * deadline (app/elections/calendar). */
export const CALENDAR_HREF = "/elections/calendar";

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
 * Congress's members: the directory, where a reader finds their senators
 * and representative by state, town or county (`?branch=senate|house`,
 * `?state=`, `?county=`). The page a member's breadcrumb leads back to.
 * `/politicians` redirects here (next.config.mjs). It never reads
 * `?chamber=`: that was the measures page's parameter when it lived at
 * this address, and a `/congress/members?chamber=` link redirects to
 * MEASURES_HREF.
 */
export const MEMBERS_HREF = "/congress/members";

/** The executive branch's hub: the president, agencies, actions, comment
 *  periods and nominations, each from stored records. */
export const EXECUTIVE_HREF = "/executive";

/** Every president, the sitting one first, with the historians' rank. */
export const PRESIDENTS_HREF = "/executive/presidents";

/** The sitting president's record. A server redirect to their
 *  `/politicians/[id]`, resolved from the API on each request
 *  (app/executive/president), so no name or id is written here. */
export const PRESIDENT_HREF = "/executive/president";

/** The sitting president's documents in Explore (executive orders,
 *  proclamations, memoranda), newest first: a redirect resolved like
 *  PRESIDENT_HREF (app/executive/orders). */
export const EXECUTIVE_ORDERS_HREF = "/executive/orders";

/** Every member of a chamber on four plain measures, side by side
 *  (`?chamber=senate|house`). */
export const MEASURES_HREF = "/congress/members/measures";

/** The directory filtered to a state (and a chamber). */
export function membersHref(state?: string | null, branch?: "senate" | "house"): string {
  const params = new URLSearchParams();
  if (branch) params.set("branch", branch);
  if (state) params.set("state", state);
  const q = params.toString();
  return q ? `${MEMBERS_HREF}?${q}` : MEMBERS_HREF;
}

/**
 * The site's sections and each one's pages, in the order the main
 * navigation, the homepage's index and each section's
 * tab bar list them: one table, so a page added here appears in all of
 * them. `href` is the section's hub; `match` is the paths that count as
 * being in the section (the main navigation's current-section mark).
 *
 * Every tab is a page of its own (a link with `aria-current`, not an
 * in-page ARIA tab), and none is ever dropped for the election phase: a page
 * out of its phase (the seat map from election day on) says so itself, so
 * the tab still leads somewhere that explains. The hrefs repeat
 * `ELECTION_MAP_HREF`, `REDISTRICTING_HREF` and `presidentSupportHref("house")`
 * as literals because those modules import this one; `routes.test.ts` holds
 * them equal. "Latest week" and "Latest month" lead to the report of the
 * last week and month Congress met (app/congress/week, app/congress/month),
 * not the calendar's, so a recess never opens on an empty report.
 */
export const SECTIONS = {
  action: {
    label: "Action Center",
    href: ACTION_CENTER_HREF,
    match: ["/action", "/issue"],
    tabs: [
      { key: "today", label: "Today", href: ACTION_CENTER_HREF },
      { key: "ongoing", label: "Ongoing", href: ACTION_CENTER_MONITORS_HREF },
      { key: "archive", label: "Archive", href: ACTION_CENTER_ARCHIVE_HREF },
    ],
  },
  congress: {
    label: "Congress",
    href: "/congress",
    // `/politicians` is every member's, president's and justice's record:
    // most are members, and a president's or justice's page passes its own
    // section to the Navbar (recordTrail).
    match: ["/congress", "/compare", "/politicians"],
    tabs: [
      { key: "reports", label: "Reports", href: "/congress" },
      { key: "week", label: "Latest week", href: "/congress/week" },
      { key: "month", label: "Latest month", href: "/congress/month" },
      // Members first of the people pages: it is where a reader finds their
      // own (the directory), and the measures and the pair compare it.
      { key: "members", label: "Members", href: MEMBERS_HREF },
      { key: "measures", label: "Measure by measure", href: MEASURES_HREF },
      { key: "compare", label: "Compare members", href: "/compare" },
      { key: "bills", label: "Bills", href: "/congress/bills" },
      { key: "committees", label: "Committees", href: "/congress/committees" },
      { key: "nominations", label: "Nominations", href: "/congress/nominations" },
      // "Votes with the president", not "With the president": the page is
      // how often members voted with the president's stated positions.
      { key: "president", label: "Votes with the president", href: "/congress/president/house" },
    ],
  },
  // Article II: the president, every president, and the agencies that
  // carry out the law. Nominations are also Congress's page (the Senate
  // confirms them, and the URL and section mark stay there), listed here
  // because "who did the president nominate" is looked for here.
  executive: {
    label: "Executive",
    href: EXECUTIVE_HREF,
    match: [EXECUTIVE_HREF, "/agencies", "/compare/presidents"],
    tabs: [
      { key: "overview", label: "Overview", href: EXECUTIVE_HREF },
      { key: "president", label: "The President", href: PRESIDENT_HREF },
      { key: "presidents", label: "Every president", href: PRESIDENTS_HREF },
      { key: "compare", label: "Compare presidents", href: "/compare/presidents" },
      { key: "agencies", label: "Every agency", href: "/agencies" },
      { key: "budget", label: "Budgets", href: "/agencies#budget" },
      { key: "comments", label: "Open comment periods", href: "/explore?comment=open" },
      { key: "orders", label: "Executive orders", href: EXECUTIVE_ORDERS_HREF },
      { key: "nominations", label: "Nominations", href: "/congress/nominations" },
    ],
  },
  // Article III. Only the Supreme Court is covered, so the label says so.
  court: {
    label: "Supreme Court",
    href: COURT_HREF,
    match: [COURT_HREF],
    tabs: [{ key: "court", label: "Justices and decisions", href: COURT_HREF }],
  },
  elections: {
    label: "Elections",
    href: "/elections",
    match: ["/elections"],
    tabs: [
      { key: "ballots", label: "Ballots by state", href: "/elections" },
      { key: "map", label: "Seat map", href: "/elections/map?chamber=senate&view=usual" },
      { key: "redistricting", label: "New House maps", href: "/elections/redistricting" },
      { key: "calendar", label: "Calendar", href: "/elections/calendar" },
      { key: "archive", label: "Archive", href: ELECTIONS_ARCHIVE_HREF },
    ],
  },
  explore: {
    label: "Explore",
    href: "/explore",
    match: ["/explore"],
    tabs: [{ key: "search", label: "Search the record", href: "/explore" }],
  },
  about: {
    label: "About",
    href: ABOUT_OVERVIEW_HREF,
    match: ["/about"],
    tabs: [
      { key: "overview", label: "Overview", href: ABOUT_OVERVIEW_HREF },
      ...ABOUT_CHAPTERS.map((c) => ({ key: c.href, label: c.title, href: c.href })),
    ],
  },
} as const;

export type SectionKey = keyof typeof SECTIONS;
export type SectionTabKey<S extends SectionKey> = (typeof SECTIONS)[S]["tabs"][number]["key"];

/** A section's pages other than its hub: what its menu lists under the
 * hub's own link, so no menu names one page twice. */
export function sectionSubpages(section: SectionKey) {
  const { href, tabs } = SECTIONS[section];
  return tabs.filter((t) => t.href !== href);
}

/**
 * The section `pathname` (no query string, as usePathname gives it) is in:
 * the one whose `match` names the longest prefix of it, by whole path
 * segments, so `/compare/presidents` belongs to the section that lists it
 * even when another lists `/compare`. Null for a page that is no section's
 * (the footer's pages, the homepage). A record shared by several sections
 * (`/politicians/[id]`) passes its own to the Navbar instead: see
 * recordTrail.
 */
export function currentSection(pathname: string): SectionKey | null {
  let best: SectionKey | null = null;
  let bestLength = -1;
  for (const key of Object.keys(SECTIONS) as SectionKey[]) {
    for (const p of SECTIONS[key].match) {
      if ((pathname === p || pathname.startsWith(`${p}/`)) && p.length > bestLength) {
        best = key;
        bestLength = p.length;
      }
    }
  }
  return best;
}

/** One step of a breadcrumb: a page's name and address. */
export interface Crumb {
  label: string;
  href: string;
}

/**
 * Where a member's, president's or justice's record sits in the site: the
 * section of the branch they serve in (the Navbar's current-section mark)
 * and the breadcrumb down to the record. The record keeps one URL,
 * `/politicians/[id]`, whatever the branch (a member who moves from the
 * House to the Senate keeps it, and the public API and feeds link it), so
 * its path can't say which section it is in; the branch does.
 */
export function recordTrail(branch: PoliticianBranch): { section: SectionKey; crumbs: Crumb[] } {
  switch (branch) {
    case "senate":
    case "house":
      return {
        section: "congress",
        crumbs: [
          { label: SECTIONS.congress.label, href: SECTIONS.congress.href },
          { label: "Members", href: MEMBERS_HREF },
        ],
      };
    case "president":
      return {
        section: "executive",
        crumbs: [
          { label: SECTIONS.executive.label, href: SECTIONS.executive.href },
          { label: "Every president", href: PRESIDENTS_HREF },
        ],
      };
    case "scotus":
      return {
        section: "court",
        crumbs: [{ label: SECTIONS.court.label, href: SECTIONS.court.href }],
      };
  }
}
