import type { SectionFigures } from "@/types/sections";
import { calendarDate, monthLabel, shortDate } from "@/lib/congress";
import { formatCurrency, formatUtcDate } from "@/lib/formatting";

/** One section page's live figure, worded: the figure and what it counts. */
export interface Preview {
  figure: string;
  caption: string;
}

/** "Jan 15, 2028": a date that may be a year or more off says its year. */
const dated = (iso: string) =>
  formatUtcDate(iso, { month: "short", day: "numeric", year: "numeric" });
const n = (x: number) => x.toLocaleString("en-US");
const plural = (x: number, one: string, many = `${one}s`) => (x === 1 ? one : many);

/** A record-vote figure for a report: the count, or, on a day or period
 * with none, what that means (the report still lists what each chamber
 * did), never a bare 0. */
function votes(count: number, when: string): Preview {
  return count > 0
    ? { figure: n(count), caption: `${plural(count, "record vote")} ${when}` }
    : {
        figure: "None",
        caption: `no record votes ${when}; the report lists what each chamber did`,
      };
}

function president(f: SectionFigures["congress"]["president"]): Preview {
  const h = f.house;
  if (!h) {
    return {
      figure: "Not yet",
      caption: "no House vote on a position the president stated has been counted yet",
    };
  }
  return {
    figure: `${Math.round(h.rate * 100)}%`,
    caption: `of House members' votes went the president's way, on ${n(h.rollCalls)} ${plural(h.rollCalls, "roll call")} with a stated position`,
  };
}

/**
 * Each section page's figure from GET /api/sections, keyed by SECTIONS
 * (lib/routes.ts) section and tab. A page with no figure (the leaderboard,
 * the About chapters) has no entry. A figure the site can't give yet says
 * what it is waiting on: an empty default is content.
 */
const PREVIEWS: Record<string, Record<string, (f: SectionFigures) => Preview | null>> = {
  action: {
    today: ({ action: { today } }) =>
      today.date
        ? today.issues > 0
          ? {
              figure: n(today.issues),
              caption: `${plural(today.issues, "issue")} on ${shortDate(today.date)}`,
            }
          : { figure: "None", caption: `issues on ${shortDate(today.date)}; read hourly` }
        : { figure: "Not yet", caption: "no issues published yet; the news is read hourly" },
    ongoing: ({ action: { ongoing } }) =>
      ongoing.monitors > 0
        ? {
            figure: n(ongoing.monitors),
            caption: `ongoing national ${plural(ongoing.monitors, "story", "stories")} tracked`,
          }
        : {
            figure: "None",
            caption: "no story has run on enough days to track; closed ones listed",
          },
    archive: ({ action: { archive } }) =>
      archive.days > 0 && archive.since
        ? {
            figure: n(archive.days),
            caption: `${plural(archive.days, "day")} of top issues since ${calendarDate(archive.since)}`,
          }
        : { figure: "Not yet", caption: "each day's top issue is kept here once one is published" },
  },
  congress: {
    reports: ({ congress: { reports } }) =>
      reports
        ? votes(
            reports.recordVotes,
            `on ${shortDate(reports.date)}, the last day either chamber met`
          )
        : { figure: "Not yet", caption: "no day of Congress recorded yet" },
    week: ({ congress: { week } }) =>
      week
        ? votes(week.recordVotes, `in the week of ${shortDate(week.start)}`)
        : { figure: "Not yet", caption: "no week of Congress recorded yet" },
    month: ({ congress: { month } }) =>
      month
        ? votes(month.recordVotes, `in ${monthLabel(month.month)}`)
        : { figure: "Not yet", caption: "no month of Congress recorded yet" },
    bills: ({ congress: { bills } }) =>
      bills.inMotion > 0
        ? {
            figure: n(bills.inMotion),
            caption: `${plural(bills.inMotion, "bill")} past introduction and referral this Congress`,
          }
        : {
            figure: "None",
            caption: "no bill has moved past committee referral yet this Congress",
          },
    committees: ({ congress: { committees: c } }) =>
      c.count > 0
        ? {
            figure: n(c.count),
            caption: `${plural(c.count, "committee")} with meetings on record${
              c.metThisWeek ? `, ${n(c.metThisWeek)} in the latest week` : ""
            }`,
          }
        : { figure: "Not yet", caption: "no committee meeting recorded yet" },
    president: ({ congress }) => president(congress.president),
  },
  agencies: {
    directory: ({ agencies: { directory } }) =>
      directory.count > 0
        ? {
            figure: n(directory.count),
            caption: "departments, agencies and sub-agencies in the Federal Register",
          }
        : { figure: "Not yet", caption: "the Federal Register's agency list hasn't been read yet" },
    budget: ({ agencies: { budget } }) =>
      budget
        ? {
            figure: formatCurrency(budget.total),
            caption: `budgetary resources, FY${budget.fiscalYear}, across ${n(budget.reportedCount)} reporting ${plural(budget.reportedCount, "agency", "agencies")}`,
          }
        : { figure: "Not yet", caption: "USAspending.gov's budgets haven't been read yet" },
    comments: ({ agencies: { comments } }) =>
      comments.open > 0
        ? {
            figure: n(comments.open),
            caption: `federal ${plural(comments.open, "document")} open for public comment today`,
          }
        : { figure: "None", caption: "no Federal Register document takes comments today" },
  },
  politicians: {
    directory: ({ politicians: { directory: d } }) =>
      d.senators + d.representatives > 0
        ? {
            figure: n(d.senators + d.representatives),
            caption: `serving members: ${n(d.senators)} senators, ${n(d.representatives)} representatives`,
          }
        : { figure: "Not yet", caption: "the member rosters haven't been read yet" },
  },
  elections: {
    ballots: ({ elections: { ballots: b, season } }) => {
      // Between cycles a general a year or two off is no headline: the
      // archive's election, or the next primaries, lead instead.
      if (season?.season === "post_certification" && season.heldCycle)
        return {
          figure: String(season.heldCycle),
          caption: `the ${season.heldCycle} election as recorded · next general ${dated(b.date)}`,
        };
      if (season?.season === "off_season")
        return season.firstPrimary
          ? {
              figure: dated(season.firstPrimary),
              caption: `the ${season.cycle} primaries begin, by the FEC's calendar · ${n(b.states)} ballots`,
            }
          : {
              figure: String(season.cycle),
              caption: `next general ${dated(b.date)}; its primaries aren't on the FEC's calendar yet`,
            };
      return b.phase === "campaign" && b.daysUntil > 0
        ? {
            figure: n(b.daysUntil),
            caption: `${plural(b.daysUntil, "day")} to Election Day, ${shortDate(b.date)} · ${n(b.states)} ballots${season?.season === "ramp" ? " · primaries under way" : ""}`,
          }
        : b.daysUntil === 0
          ? { figure: "Today", caption: `is Election Day · ${n(b.states)} ballots and counts` }
          : { figure: "Results", caption: `the ${shortDate(b.date)} election, state by state` };
    },
    map: ({ elections: { map } }) =>
      !map.available
        ? { figure: "Off", caption: map.reason }
        : map.withPolls > 0
          ? {
              figure: n(map.withPolls),
              caption: `of ${n(map.seats)} Senate seats have polls · ${n(map.pollsDiffer)} where they differ from the usual vote`,
            }
          : {
              figure: n(map.seats),
              caption: "Senate seats on the map by their usual vote; no poll in the window yet",
            },
    redistricting: ({ elections: { redistricting: r } }) =>
      r.states > 0
        ? {
            figure: n(r.states),
            caption: `${plural(r.states, "state")} ${r.upcoming ? "vote" : "voted"} on new House lines${r.cycle ? ` in ${r.cycle}` : ""}`,
          }
        : { figure: "None", caption: "no state has new House lines this cycle" },
    // The archive's card leads with the public check of the map: how many
    // official margins fell inside its ranges. Before the first archive, or
    // before any count is official, it says so, never a bare 0.
    // The FEC's next reporting deadline: a recurring event, worded around
    // its day (backend next_cycle.deadline_event).
    calendar: ({ elections: { calendar } }) => {
      if (calendar === undefined) throw new Error("older backend");
      const d = calendar.deadline;
      if (!d) return null;
      const what = d.label ?? d.reportType;
      if (d.status === "due_today") return { figure: "Today", caption: `FEC ${what} reports due` };
      if (d.status === "passed")
        return {
          figure: dated(d.date),
          caption: `FEC ${what} reports were due · every date and deadline ahead`,
        };
      return {
        figure: dated(d.date),
        caption: `next FEC filing deadline: ${what} reports · every date and deadline ahead`,
      };
    },
    archive: ({ elections: { archive, ballots } }) => {
      if (archive === undefined) throw new Error("older backend");
      if (!archive)
        return {
          figure: "Not yet",
          caption: `the ${ballots.date.slice(0, 4)} election is kept here from its day, its map's ranges checked against the count`,
        };
      const a = archive.accountability;
      if (!a)
        return {
          figure: String(archive.cycle),
          caption: "the election as recorded; no pre-election map was kept to check",
        };
      if (a.graded === 0)
        return {
          figure: "Counting",
          caption: `${archive.cycle}: 0 of ${n(a.ranged)} seats with a range official yet; each is checked against the map once official`,
        };
      return {
        figure: n(a.held80),
        caption: `of ${n(a.graded)} official ${archive.cycle} margins inside the map's 80% ranges · ${n(a.held95)} inside the 95% · swing map${archive.stillCounting ? " · still counting" : ""}`,
      };
    },
  },
  court: {
    // Destructuring an older backend's answer (no `court`) throws: no figure.
    court: ({ court: { court } }) =>
      court
        ? {
            figure: n(court.cases),
            caption: `${plural(court.cases, "case")} decided in the ${court.term} term, ${n(court.unanimous)} unanimous`,
          }
        : { figure: "Not yet", caption: "no Supreme Court decision has been read yet" },
  },
  explore: {
    search: ({ explore: { search } }) =>
      search.documents > 0
        ? { figure: n(search.documents), caption: "federal documents on file to search" }
        : { figure: "Not yet", caption: "no federal document has been read yet" },
  },
};

/** The figure for one section page, or null when the page has none or
 * the figures couldn't be read. */
export function sectionPreview(
  figures: SectionFigures | null,
  section: string,
  tab: string
): Preview | null {
  const word = PREVIEWS[section]?.[tab];
  if (!figures || !word) return null;
  try {
    return word(figures);
  } catch {
    // A figure missing from an older backend's answer: no figure, not a crash.
    return null;
  }
}

/** A section's pages in the order the season leads with (backend
 * election_phase.SEASON_LEAD): nothing is dropped, a page the order doesn't
 * name keeps its place after the ones it does. */
export function leadOrder<T extends { key: string }>(tabs: T[], lead: string[] | undefined): T[] {
  if (!lead?.length) return tabs;
  const rank = (k: string) => (lead.includes(k) ? lead.indexOf(k) : lead.length);
  return [...tabs].sort((a, b) => rank(a.key) - rank(b.key));
}

/** The order the elections section's season leads with; undefined for any
 * other section, or an answer without it. */
export function seasonLead(figures: SectionFigures | null, section: string): string[] | undefined {
  return section === "elections" ? figures?.elections?.season?.lead : undefined;
}
