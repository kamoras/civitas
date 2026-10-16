import Link from "next/link";
import { connection } from "next/server";
import type { ArchiveIndex } from "@/types/electionArchive";
import { electionArchiveHref, ELECTIONS_ARCHIVE_HREF } from "@/lib/routes";
import { pageMetadata } from "@/lib/site";
import { describeElectionArchiveIndex } from "@/lib/seo";
import { formatEasternTime } from "@/lib/results";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import { longDate } from "@/components/elections/archive/CycleArchive";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

export const metadata = pageMetadata({
  ...describeElectionArchiveIndex(),
  path: ELECTIONS_ARCHIVE_HREF,
});

async function fetchIndex(): Promise<ArchiveIndex> {
  const res = await fetch(`${BACKEND}/api/elections/archive`, { next: { revalidate: 300 } });
  if (!res.ok) throw new Error(`election archive: HTTP ${res.status}`);
  return (await res.json()) as ArchiveIndex;
}

/** What the archive keeps, said once whether or not anything is in it yet. */
const KEEPS =
  "Each general election is kept here from its election day: the seat map as it stood the day before, each Senate and House race's count as its state reports it, and the whole ballot the site showed (federal, statewide, legislative and judicial contests, and statewide measures), quoted from their sources. The record stays after the site moves on to the next election.";

/** When the next archive starts, from the site's current election. */
function nextLine(index: ArchiveIndex): string | null {
  const { current, cycles } = index;
  if (cycles.some((c) => c.cycle === current.cycle)) return null;
  if (current.phase !== "campaign")
    return `The ${current.cycle} count is being read now; its record appears here within the hour and is copied again as the count moves.`;
  const when = `the ${current.cycle} general election, on ${longDate(current.electionDay)}`;
  return cycles.length
    ? `Next: ${when}, which joins this list from that day.`
    : `The first will be ${when}: it appears here from that day.`;
}

/**
 * /elections/archive: every general election Civitas has kept a record of
 * (GET /api/elections/archive), newest first. Never an empty page: before
 * the first archive it says what will appear and when, from the backend's
 * current election.
 */
export default async function ElectionArchiveIndexPage() {
  // Per request: the backend isn't reachable under `next build`.
  await connection();
  const index = await fetchIndex();
  const next = nextLine(index);
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-4xl space-y-8">
          <SectionTabs section="elections" active="archive" className="" />
          <PageMasthead eyebrow="Elections · Archive" title="Past elections">
            {KEEPS}
          </PageMasthead>
          {index.cycles.length === 0 ? (
            <section className="border border-phos/20 bg-surface p-5">
              <h2 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                Nothing archived yet
              </h2>
              <p className="mt-2 text-ink-lo">{next}</p>
              <p className="mt-3 text-sm">
                <Link
                  href="/elections"
                  className="inline-flex min-h-11 items-center text-ink-hi underline"
                >
                  The ballot for the {index.current.cycle} election →
                </Link>
              </p>
            </section>
          ) : (
            <>
              <ol className="space-y-3">
                {index.cycles.map((c) => (
                  <li key={c.cycle}>
                    <Link
                      href={electionArchiveHref(c.cycle)}
                      className="block border border-white/[0.09] bg-surface p-4 hover:border-phos/60"
                    >
                      <span className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-display text-xl font-extrabold uppercase text-ink-hi">
                          The {c.cycle} general election →
                        </span>
                        {c.stillCounting && (
                          <span className="border border-signal-amber/40 px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] text-ink-hi">
                            COUNT STILL MOVING
                          </span>
                        )}
                      </span>
                      <span className="mt-1 block font-mono text-xs text-ink-lo">
                        {longDate(c.electionDay)} · {c.races} federal races · {c.counted} with a
                        count read, {c.official} listed official
                        {c.stateContests ? ` · ${c.stateContests} state contests` : ""} ·{" "}
                        {c.measures} statewide measures
                        {c.hasMap ? " · the map before election day" : ""}
                      </span>
                      {c.accountability && (
                        <span className="mt-1 block font-mono text-xs text-ink-hi">
                          {c.accountability.graded > 0
                            ? `The map's ranges: ${c.accountability.held80} of ${c.accountability.graded} official margins inside the 80%, ${c.accountability.held95} inside the 95%`
                            : `The map's ranges: none graded yet, 0 of ${c.accountability.ranged} seats with a range official`}
                        </span>
                      )}
                      {c.recordAsOf && (
                        <span className="mt-1 block font-mono text-[11px] text-ink-min">
                          Recorded {formatEasternTime(c.recordAsOf)}
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ol>
              {next && <p className="text-sm text-ink-lo">{next}</p>}
            </>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
