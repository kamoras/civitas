import type { DayStatus, ElectionCalendar } from "@/types/specials";
import type { RegisterLink } from "@/types/election";
import { formatUtcDate, safeHref } from "@/lib/formatting";

/** How an election day's status reads on the page (the status itself is
 * the backend's, counted on the state's clock). */
export function dayText(status: DayStatus, daysLeft: number | null): string {
  if (status === "voting") return "Voting today";
  if (status === "polls_closed") return "Polls closed";
  if (status === "held") return "Held";
  if (daysLeft === 1) return "Tomorrow";
  return daysLeft != null ? `In ${daysLeft} days` : "Upcoming";
}

export function longDate(iso: string): string {
  return formatUtcDate(iso, { month: "long", day: "numeric", year: "numeric" });
}

/** One line of the calendar: an election, a voter deadline or a reporting
 * deadline, worded by the backend. */
export interface CalendarEntry {
  key: string;
  date: string;
  state: string | null;
  tag: "Election" | "Special" | "Deadline" | "FEC report";
  text: string;
  status: string;
  passed: boolean;
  href?: string;
  /** Where to register, on an open registration deadline. */
  register?: RegisterLink;
  /** What a run of same-day entries is called when every state is shown
   * ("General election", "Voter deadlines"); a special stands alone. */
  kind?: string;
}

export function calendarEntries(cal: ElectionCalendar): CalendarEntry[] {
  const out: CalendarEntry[] = [
    ...cal.elections.map((e) => ({
      key: e.id,
      date: e.date,
      state: e.state,
      tag: (e.special ? "Special" : "Election") as CalendarEntry["tag"],
      text: `${e.seat}: ${e.label ?? e.typeCode}${e.note ? ` (${e.note})` : ""}`,
      status: dayText(e.status, e.daysLeft),
      passed: e.status === "held",
      href: e.special ? `#special-${e.id}` : undefined,
      kind: e.special ? undefined : (e.label ?? e.typeCode),
    })),
    ...cal.voterDeadlines.map((d) => ({
      key: `${d.state}-${d.kind}-${d.date}-${d.label}`,
      date: d.date,
      state: d.state,
      tag: "Deadline" as const,
      text: `${d.stateName}: ${d.sentence}${d.republishedBy ? ` (as published by the ${d.republishedBy})` : ""}`,
      status: d.status === "closed" ? "Passed" : d.status === "open" ? "Open" : "Upcoming",
      passed: d.status === "closed",
      href: safeHref(d.source.url),
      register: d.register ?? undefined,
      kind: "Voter deadlines",
    })),
    ...cal.fecDeadlines.map((f) => ({
      key: `${f.date}-${f.reportType}`,
      date: f.date,
      state: null,
      tag: "FEC report" as const,
      text: `Campaign finance reports due: ${f.label.toLowerCase()}${f.reportYear ? ` (${f.reportYear})` : ""}`,
      status: f.status === "held" ? "Passed" : f.status === "due_today" ? "Due today" : "Upcoming",
      passed: f.status === "held",
    })),
  ];
  return out.sort((a, b) => a.date.localeCompare(b.date) || a.tag.localeCompare(b.tag));
}
