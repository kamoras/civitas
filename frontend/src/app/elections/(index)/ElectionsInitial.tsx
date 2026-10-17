"use client";

import { createContext, useContext } from "react";
import type { ElectionPhaseInfo, PviMap } from "@/types/election";
import type { ElectionCalendar, SpecialsBoard } from "@/types/specials";
import type { ArchiveIndex } from "@/types/electionArchive";

/** What the server already read for /elections (elections/layout.tsx): the
 * lean map and which mode the page is in, so the masthead, map and state
 * directory are in the HTML rather than painted after hydration (the
 * masthead's line painted at 2.1 s on a throttled phone, 2026-10-14). The
 * live count itself is still read by the page, on its own clock. */
export interface ElectionsInitial {
  pvi: PviMap | null;
  /** The phase as the server read it, and which frame that means: the
   * campaign's lean map, election day before any count, or the count.
   * Null when the server couldn't read it. */
  phase: {
    mode: "campaign" | "election_day" | "results";
    phase: ElectionPhaseInfo;
    cycleYear: number | null;
  } | null;
  /** The specials board, with the season /elections is in; between
   * general elections it leads the page, with the calendar. Null when the
   * server couldn't read it. */
  specials: SpecialsBoard | null;
  /** The calendar, read between general elections and in primary season. */
  calendar: ElectionCalendar | null;
  /** The archive's index, read after the count settles (post-certification). */
  archive?: ArchiveIndex | null;
  /** The server's clock when it rendered, so the countdown reads the same
   * on the server and in the browser. */
  renderedAt: number;
}

const ElectionsInitialContext = createContext<ElectionsInitial | null>(null);
// The section hub's cards, rendered on the server by the layout and handed
// to the client page to place (components/sections/SectionCards.tsx).
const ElectionsHubContext = createContext<React.ReactNode>(null);

export function ElectionsInitialProvider({
  value,
  hub,
  children,
}: {
  value: ElectionsInitial;
  hub?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <ElectionsInitialContext.Provider value={value}>
      <ElectionsHubContext.Provider value={hub}>{children}</ElectionsHubContext.Provider>
    </ElectionsInitialContext.Provider>
  );
}

export function useElectionsHub(): React.ReactNode {
  return useContext(ElectionsHubContext);
}

export function useElectionsInitial(): ElectionsInitial | null {
  return useContext(ElectionsInitialContext);
}
