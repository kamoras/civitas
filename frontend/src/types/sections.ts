/** GET /api/sections (backend/app/api/sections.py): one live figure per
 *  hub card on /congress, /elections and /agencies.
 *  Every count and rate is the backend's; the frontend only words it. A
 *  null is a figure the site can't give yet, never a zero. */

import type { AccountabilitySummary } from "@/types/mapAccountability";
import type { Season } from "@/types/specials";
import type { FecDeadlineEvent } from "@/types/nextCycle";

export interface SectionFigures {
  congress: {
    week: { start: string; recordVotes: number } | null;
    month: { month: string; recordVotes: number } | null;
    /** When the latest day Congress met had no business: the last day of
     *  business, which the week and month cards show instead of "None". */
    recess: { lastBusinessDay: string | null } | null;
    committees: { count: number; metThisWeek: number | null };
    bills: { inMotion: number };
    president: Record<"house" | "senate", { rate: number; rollCalls: number } | null>;
  };
  agencies: {
    budget: { total: number; fiscalYear: number | null; reportedCount: number } | null;
    comments: { open: number };
  };
  elections: {
    /** The coming election's date, which the archive card names. */
    ballots: { date: string };
    map:
      | { available: true; seats: number; withPolls: number; pollsDiffer: number }
      | { available: false; reason: string };
    redistricting: { states: number; cycle: string | null; upcoming: boolean };
    /** The newest archived election and how its map's ranges held against
     *  the official count; null before the first archive. Optional: an
     *  older backend's answer has none. */
    archive?: {
      cycle: number;
      stillCounting: boolean;
      accountability: AccountabilitySummary | null;
    } | null;
    /** Which stretch of the calendar the section is in (optional: an older
     *  backend's answer has none). */
    season?: Season;
    /** The FEC's reporting deadline the calendar leads with (optional: an
     *  older backend's answer has none). */
    calendar?: { deadline: FecDeadlineEvent | null };
  };
}
