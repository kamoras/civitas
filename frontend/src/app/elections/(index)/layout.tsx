import type { Metadata } from "next";
import { connection } from "next/server";
import type { LiveResults, PviMap, RaceSummary } from "@/types/election";
import type { ElectionCalendar, SpecialsBoard } from "@/types/specials";
import type { ArchiveIndex } from "@/types/electionArchive";
import type {
  ElectionAdministration,
  FecDeadlineEvent,
  NewCongress,
  NextField,
  OddYearStates,
  PresidentialContest,
} from "@/types/nextCycle";
import type { RedistrictingIndex } from "@/types/redistricting";
import { TOPIC_FEEDS, pageMetadata } from "@/lib/site";
import { describeElections } from "@/lib/seo";
import { everyLiveStateVoting, showsResults } from "@/lib/results";
import SectionCards from "@/components/sections/SectionCards";
import ClosingSoon from "@/components/elections/ClosingSoon";
import type { ThisWeek } from "@/types/thisWeek";
import { ElectionsInitialProvider, type ElectionsInitial } from "./ElectionsInitial";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Cycle year for the title/description comes from the backend (same
// current_election_cycle() the /elections/races endpoint filters on) —
// not recomputed here — so this metadata rolls to the next cycle with
// zero code changes once the backend does.
async function fetchCycleYear(): Promise<number | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/races`, { next: { revalidate: 3600 } });
    if (!res.ok) return null;
    const races: RaceSummary[] = await res.json();
    return races[0]?.cycleYear ?? null;
  } catch {
    return null;
  }
}

// What /elections is showing (backend election_phase), read the way the
// page itself reads it: results once some covered state's last polls have
// closed; on election day before that, no count yet (everyLiveStateVoting)
// — and a link shared that morning must read neither "Election Results"
// nor the campaign's "partisan lean", which the page stops drawing on
// election day. Revalidated every five minutes, so the description turns
// within minutes of the phase or the first close; an unreachable backend
// (as under `next build`) is null: the metadata uses the campaign wording,
// as the page's own default is, and the page reads the phase itself.
// `now` is this server's clock, which in production runs beside the
// backend's.
async function fetchPhase(now: number): Promise<ElectionsInitial["phase"]> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/results`, { next: { revalidate: 300 } });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<LiveResults>;
    if (!body?.phase) return null;
    const read = { phase: body.phase, cycleYear: body.cycleYear ?? null };
    if (!showsResults(body.phase)) return { ...read, mode: "campaign" };
    const voting = everyLiveStateVoting(
      {
        phase: body.phase,
        pollsClose: body.pollsClose,
        feeds: body.feeds,
        races: Array.isArray(body.races) ? body.races : [],
        liveStates: Array.isArray(body.liveStates) ? body.liveStates : [],
      },
      now
    );
    return { ...read, mode: voting ? "election_day" : "results" };
  } catch {
    return null;
  }
}

// Canonical /elections, for the index page only: this layout sits in the
// (index) route group, so the state, redistricting and race pages beneath
// (and their 404s) never inherit it. See lib/site.ts.
export async function generateMetadata(): Promise<Metadata> {
  const [cycleYear, phase] = await Promise.all([fetchCycleYear(), fetchPhase(Date.now())]);
  return pageMetadata({
    ...describeElections(cycleYear, phase?.mode ?? "campaign"),
    path: "/elections",
    feed: TOPIC_FEEDS.elections,
  });
}

// The lean map, as the page reads it (lib/api fetchPviMap): its maps as
// objects. Null when the backend can't answer; the page then reads it itself.
async function fetchPvi(): Promise<PviMap | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/pvi`, { next: { revalidate: 300 } });
    if (!res.ok) return null;
    const raw = (await res.json()) as Partial<PviMap> | null;
    if (!raw || typeof raw !== "object") return null;
    return { ...raw, states: raw.states ?? {}, districts: raw.districts ?? {} } as PviMap;
  } catch {
    return null;
  }
}

// The specials board (with the season), and the calendar or archive the
// season leads with. Null when the backend can't answer: the page then
// stays on the campaign frame it would otherwise show.
async function fetchJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BACKEND}${path}`, { next: { revalidate: 120 } });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// Rendered per request, never at build time: `next build` has no backend,
// so a prerendered page would always be the no-data frame.
async function readInitial(): Promise<ElectionsInitial> {
  await connection();
  const renderedAt = Date.now();
  const [pvi, phase, specials] = await Promise.all([
    fetchPvi(),
    fetchPhase(renderedAt),
    fetchJson<SpecialsBoard>("/api/elections/specials"),
  ]);
  // The season's own lead (election_phase.season): the calendar between
  // cycles and through the primaries, the archive once the count settles.
  const season = specials?.season?.season;
  const between = !!specials?.season?.betweenCycles;
  const [calendar, archive, field, newCongress, fec, redistricting, oddYear, presidential, admin] =
    await Promise.all([
      between || season === "ramp" ? fetchJson<ElectionCalendar>("/api/elections/calendar") : null,
      season === "post_certification" ? fetchJson<ArchiveIndex>("/api/elections/archive") : null,
      between || season === "ramp" ? fetchJson<NextField>("/api/elections/field") : null,
      season === "off_season" ? fetchJson<NewCongress>("/api/elections/new-congress") : null,
      fetchJson<{ deadline: FecDeadlineEvent | null }>("/api/elections/fec-deadline"),
      between ? fetchJson<RedistrictingIndex>("/api/elections/redistricting") : null,
      between ? fetchJson<OddYearStates>("/api/elections/odd-year") : null,
      fetchJson<PresidentialContest>("/api/elections/presidential"),
      between ? fetchJson<ElectionAdministration>("/api/elections/administration") : null,
    ]);
  return {
    pvi,
    phase,
    specials,
    calendar,
    archive,
    field,
    newCongress,
    fecDeadline: fec?.deadline ?? null,
    redistrictingWatch: redistricting?.watch ?? null,
    oddYear,
    presidential,
    administration: admin,
    renderedAt,
  };
}

export default async function ElectionsLayout({ children }: { children: React.ReactNode }) {
  const [initial, week] = await Promise.all([readInitial(), fetchJson<ThisWeek>("/api/this-week")]);
  const closing = week?.election.registrationClosing;
  return (
    <ElectionsInitialProvider
      value={initial}
      hub={
        <>
          {/* Registration closing soon, with where to register: the
              deadline a reader can still act on comes before the map. */}
          {closing && (
            <ClosingSoon
              rows={closing}
              days={week?.election.closingSoonDays}
              className="mt-6 border border-phos/20 bg-surface px-4 py-3"
            />
          )}
          <SectionCards section="elections" current="ballots" />
        </>
      }
    >
      {children}
    </ElectionsInitialProvider>
  );
}
