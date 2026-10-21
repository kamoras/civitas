"use client";

import { createContext, useContext } from "react";

/** The pipeline's overview, as the server read it (congress/bills/layout.tsx):
 * the masthead's count and the stage funnel, so they are in the HTML rather
 * than painted after hydration (the masthead's lines painted at 1.8 s on a
 * throttled phone, 2026-10-14). */
export interface BillsInitial {
  stageCounts: Record<string, number>;
  stageFlow: Record<string, { shareText: string; barPercent: number }>;
  inMotion: number;
}

const BillsInitialContext = createContext<BillsInitial | null>(null);

export function BillsInitialProvider({
  value,
  children,
}: {
  value: BillsInitial | null;
  children: React.ReactNode;
}) {
  return <BillsInitialContext.Provider value={value}>{children}</BillsInitialContext.Provider>;
}

export function useBillsInitial(): BillsInitial | null {
  return useContext(BillsInitialContext);
}
