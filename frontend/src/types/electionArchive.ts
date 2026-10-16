/** GET /api/elections/archive and /api/elections/archive/{cycle} (backend
 * app/api/election_archive.py): each held general election as Civitas
 * recorded it. Every field is copied from the live pages' own shapes. */
import type { BallotMeasure, LiveRaceResult } from "@/types/election";

export interface ArchiveTotals {
  races: number;
  /** Races with a count read from the state's feed. */
  counted: number;
  /** Of those, counts the state itself lists as official. */
  official: number;
  measures: number;
  states: number;
}

export interface ArchivedCycle extends ArchiveTotals {
  cycle: number;
  electionDay: string;
  recordAsOf: string | null;
  mapAsOf: string | null;
  hasMap: boolean;
  /** The site is still on this election's count: the record is re-copied
   * from it until the results window closes. */
  stillCounting: boolean;
}

export interface ArchiveIndex {
  cycles: ArchivedCycle[];
  /** The site's election now, so an empty list can say when the next
   * archive starts. */
  current: { cycle: number; electionDay: string; phase: string };
}

export interface ArchivedCandidate {
  id: string;
  name: string;
  /** As the state printed it; the FEC's `name` otherwise. */
  ballotName: string | null;
  party: string;
  partyGroup: string | null;
  partyLabel: string | null;
  confirmed: boolean;
  incumbentChallenge: string | null;
}

export interface ArchivedRace {
  id: string;
  state: string;
  office: "S" | "H";
  district: number | null;
  isSpecial: boolean;
  candidateSource: "confirmed" | "nominees" | "primary" | "filers";
  candidates: ArchivedCandidate[];
  /** The count as the state last reported it; null where none was read. */
  result: LiveRaceResult | null;
}

export interface ArchiveRecord {
  cycle: number;
  electionDay: string;
  states: { state: string; stateName: string; candidateListSource: string | null }[];
  liveStates: string[];
  rankedChoiceStates: string[];
  redrawnStates: string[];
  races: ArchivedRace[];
  measures: BallotMeasure[];
  recordAsOf: string | null;
  mapAsOf: string | null;
  hasMap: boolean;
  stillCounting: boolean;
  totals: ArchiveTotals;
}
