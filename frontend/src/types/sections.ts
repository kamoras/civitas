/** GET /api/sections (backend/app/api/sections.py): one live figure per
 *  section page, for the hub cards and the homepage's "Sections" band.
 *  Every count and rate is the backend's; the frontend only words it. A
 *  null is a figure the site can't give yet, never a zero. */

import type { AccountabilitySummary } from "@/types/mapAccountability";

export interface SectionFigures {
  action: {
    today: { issues: number; date: string | null };
    ongoing: { monitors: number };
    archive: { days: number; since: string | null };
  };
  congress: {
    reports: { date: string; recordVotes: number } | null;
    week: { start: string; recordVotes: number } | null;
    month: { month: string; recordVotes: number } | null;
    committees: { count: number; metThisWeek: number | null };
    bills: { inMotion: number };
    president: Record<"house" | "senate", { rate: number; rollCalls: number } | null>;
  };
  agencies: {
    directory: { count: number };
    budget: { total: number; fiscalYear: number | null; reportedCount: number } | null;
    comments: { open: number };
  };
  politicians: { directory: { senators: number; representatives: number } };
  elections: {
    ballots: { states: number; date: string; phase: string; daysUntil: number };
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
  };
  explore: { search: { documents: number } };
}
