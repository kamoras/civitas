import Link from "next/link";
import type { ArchiveIndex } from "@/types/electionArchive";
import type { Season } from "@/types/specials";
import { electionArchiveHref } from "@/lib/routes";
import { longDate } from "@/lib/specials";

const LINK =
  "inline-flex min-h-11 items-center font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos";

/** The day after the season's last, as an ISO date: post-certification ends
 * the day before the new Congress is seated. */
function dayAfter(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

/** /elections after the count has settled, until the new Congress is seated
 * (the backend's post_certification season): the election just held, as the
 * archive kept it, leads. How much of the count is official is the
 * archive's own figure, never assumed from the calendar. */
export default function ArchiveLead({
  season,
  archive,
}: {
  season: Season;
  archive: ArchiveIndex | null;
}) {
  const held = season.heldCycle;
  if (held == null) return null;
  const kept = archive?.cycles.find((c) => c.cycle === held) ?? null;
  const a = kept?.accountability;
  return (
    <section
      aria-labelledby="season-archive"
      className="mt-6 border border-phos/20 bg-surface px-4 pb-4 font-sans"
    >
      <h2
        id="season-archive"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        The {held} election, on the record
      </h2>
      {kept ? (
        <>
          <p className="mt-3 font-display text-2xl font-semibold leading-tight text-ink-hi">
            {kept.counted > 0
              ? `${kept.official} of ${kept.counted} counted races listed official by their states`
              : `${kept.races} federal races on the record; no state's count was read`}
          </p>
          <p className="mt-2 text-sm text-ink-lo">
            {kept.races} federal races, {kept.counted} with a count read from the state&apos;s own
            feed{kept.measures ? `, and ${kept.measures} statewide measures, quoted` : ""}. A count
            &ldquo;leads&rdquo; until its state lists it as official; Civitas calls no race.
          </p>
          {a && (
            <p className="mt-2 text-sm text-ink-lo">
              {a.graded > 0
                ? `The seat map's ranges, checked against the official count so far: ${a.held80} of ${a.graded} margins inside the 80% range, ${a.held95} inside the 95%.`
                : `The seat map's ranges are checked seat by seat once each count is official: none of ${a.ranged} is yet.`}
            </p>
          )}
        </>
      ) : (
        <p className="mt-3 text-sm text-ink-lo">
          {archive
            ? `No record of the ${held} election was kept here.`
            : `The record of the ${held} election couldn't be read just now.`}
        </p>
      )}
      {season.until && (
        <p className="mt-2 text-sm text-ink-lo">
          The new Congress is seated on {longDate(dayAfter(season.until))} (20th Amendment).
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-x-6">
        {kept && (
          <Link href={electionArchiveHref(held)} className={LINK}>
            [ THE {held} RECORD → ]
          </Link>
        )}
      </div>
    </section>
  );
}
