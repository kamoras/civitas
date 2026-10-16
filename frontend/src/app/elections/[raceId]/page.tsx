import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { connection } from "next/server";
import { fetchRecord } from "@/lib/ssrPayload";
import { stateBallotHref } from "@/lib/elections";
import { parseChamber, parseView } from "@/lib/electionMap";
import { archivedRaceHref, electionArchiveHref, ELECTIONS_ARCHIVE_HREF } from "@/lib/routes";
import { absoluteUrl, notFoundMetadata, pageMetadata } from "@/lib/site";
import { describeElectionArchive } from "@/lib/seo";
import type { ArchiveRecord } from "@/types/electionArchive";
import type { ElectionMapResponse } from "@/types/electionMap";
import type { MapAccountability } from "@/types/mapAccountability";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import CycleArchive, { longDate } from "@/components/elections/archive/CycleArchive";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** One segment, two kinds of page: a four-digit year is an archived
 * general election (/elections/2026); anything else is a race id from an
 * old link (/elections/2026-SEN-GA, every Bluesky race post), which only
 * redirects. Next allows one dynamic segment per level, so they share it. */
function cycleOf(segment: string): number | null {
  return /^\d{4}$/.test(segment) ? Number(segment) : null;
}

/** Null for an election with no archive; an outage throws (fetchRecord). */
function fetchArchive(cycle: number): Promise<ArchiveRecord | null> {
  return fetchRecord<ArchiveRecord>(
    `${BACKEND}/api/elections/archive/${cycle}`,
    { next: { revalidate: 300 } },
    "races",
    "states",
    "totals"
  );
}

function fetchArchiveMap(cycle: number, chamber: string): Promise<ElectionMapResponse | null> {
  return fetchRecord<ElectionMapResponse>(
    `${BACKEND}/api/elections/archive/${cycle}/map?chamber=${chamber}`,
    { next: { revalidate: 300 } },
    "chambers"
  );
}

function fetchAccountability(cycle: number): Promise<MapAccountability | null> {
  return fetchRecord<MapAccountability>(
    `${BACKEND}/api/elections/archive/${cycle}/accountability`,
    { next: { revalidate: 300 } },
    "chambers"
  );
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ raceId: string }>;
}): Promise<Metadata> {
  const { raceId } = await params;
  const cycle = cycleOf(raceId);
  // A race link only redirects: its metadata is never rendered.
  if (cycle === null) return {};
  const record = await fetchArchive(cycle);
  if (!record) {
    return notFoundMetadata(
      `No archive for ${cycle}`,
      `Civitas kept no record of a ${cycle} general election.`
    );
  }
  return pageMetadata({
    ...describeElectionArchive(cycle, record.totals.races, record.totals.measures),
    path: electionArchiveHref(cycle),
    type: "article",
  });
}

export default async function ElectionsSegmentPage({
  params,
  searchParams,
}: {
  params: Promise<{ raceId: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { raceId } = await params;
  const cycle = cycleOf(raceId);
  if (cycle !== null) return <ArchivePage cycle={cycle} query={(await searchParams) ?? {}} />;

  // Null only for a race that doesn't exist; an outage throws (fetchRecord),
  // so a published link isn't answered with a 404 while the backend is down.
  const race = await fetchRecord<{ state: string; archiveCycle?: number | null }>(
    `${BACKEND}/api/elections/races/${encodeURIComponent(raceId)}`,
    { next: { revalidate: 120 } },
    "state"
  );
  const state = typeof race?.state === "string" ? race.state : null;

  if (!state) notFound();

  // A past election's race: the state page has moved on to the next
  // ballot, so the race is on its election's archive page.
  if (typeof race?.archiveCycle === "number") redirect(archivedRaceHref(race.archiveCycle, raceId));

  redirect(`${stateBallotHref(state)}#race-${raceId}`);
}

async function ArchivePage({
  cycle,
  query,
}: {
  cycle: number;
  query: Record<string, string | string[] | undefined>;
}) {
  // Rendered per request: the backend isn't reachable under `next build`,
  // and the map's view and chamber come from the query string.
  await connection();
  const record = await fetchArchive(cycle);
  if (!record) notFound();
  const chamber = parseChamber(query.chamber);
  const [map, accountability] = record.hasMap
    ? await Promise.all([fetchArchiveMap(cycle, chamber), fetchAccountability(cycle)])
    : [null, null];
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl space-y-8">
          <SectionTabs section="elections" active="archive" className="" />
          <PageMasthead
            eyebrow={`Elections · Archive · ${longDate(record.electionDay)}`}
            title={`The ${cycle} general election`}
          >
            What Civitas recorded: the seat map as it stood before election day and how its ranges
            held against the official count, each Senate and House race&apos;s count as its state
            reported it, and the whole ballot the site showed (federal, statewide, legislative and
            judicial contests, and statewide measures), quoted from their sources.{" "}
            <Link href={ELECTIONS_ARCHIVE_HREF} className="text-ink-hi underline">
              Every archived election →
            </Link>
          </PageMasthead>
          <CycleArchive
            record={record}
            map={map}
            accountability={accountability}
            initialView={parseView(query.view)}
            initialChamber={chamber}
            pageUrl={absoluteUrl(electionArchiveHref(cycle))}
          />
        </div>
      </main>
      <Footer />
    </div>
  );
}
