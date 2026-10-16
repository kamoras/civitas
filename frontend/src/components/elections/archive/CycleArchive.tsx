import Link from "next/link";
import ElectionMap from "@/components/elections/map/ElectionMap";
import MapAccountability from "@/components/elections/archive/MapAccountability";
import BallotMeasureCard from "@/components/elections/BallotMeasureCard";
import { statusTag } from "@/components/elections/results/RaceResult";
import { candidateName, PRIMARY_RESULTS_CAVEAT } from "@/lib/elections";
import { formatUtcDate, safeHref } from "@/lib/formatting";
import { formatAsOf } from "@/lib/electionMap";
import {
  formatEasternTime,
  partyTag,
  partyTextClass,
  raceLabel,
  reportingText,
} from "@/lib/results";
import type { ArchiveRecord, ArchivedRace, ArchivedStateContests } from "@/types/electionArchive";
import type { StatewideNominee } from "@/types/election";
import type { ElectionMapResponse } from "@/types/electionMap";
import type { MapAccountability as Accountability } from "@/types/mapAccountability";

/** Whose list a race's candidates are, in words (candidateSource, decided
 * by the backend when the record was copied). */
const BALLOT_SOURCE: Record<ArchivedRace["candidateSource"], string> = {
  confirmed: "the state's certified list",
  nominees:
    "nominees the state confirmed from its primary results; a candidate who ran in no primary may be missing",
  primary: "the state's primary ballot; no nominee was confirmed",
  filers: "FEC filings; no state list was read for this race",
};

export function longDate(day: string): string {
  return formatUtcDate(day, { year: "numeric", month: "long", day: "numeric" }, "en-US");
}

/** Why a race has no count: a ranked-choice state is never read live (its
 * first choices may not be who the rounds elect), a state with no feed
 * Civitas reads, or a contest the feed never matched. */
function noCountText(race: ArchivedRace, record: ArchiveRecord): string {
  if (record.rankedChoiceStates.includes(race.state))
    return "No count shown: this state decides federal races by ranked-choice rounds, and Civitas read no first-choice count that could be mistaken for the result.";
  if (!record.liveStates.includes(race.state))
    return "No count shown: Civitas does not read this state's results feed.";
  return "No count shown: the state's feed listed no contest Civitas could match to this race.";
}

/** A state nominee's party as the state's list printed it: its own label
 * where the shared codes can't name it, else the code's letter. */
function nomineeParty(n: StatewideNominee): string {
  return n.partyLabel || partyTag(n.party);
}

function NomineeList({ nominees }: { nominees: StatewideNominee[] }) {
  return (
    <span className="text-ink">
      {nominees.map((n, i) => (
        <span key={`${i}-${n.name}`}>
          {i > 0 && " · "}
          {n.name} <span className="font-mono text-xs text-ink-lo">({nomineeParty(n)})</span>
        </span>
      ))}
    </span>
  );
}

function sourceLine(src: { sourceName: string | null; ballotList: boolean }): string {
  const from = src.sourceName ? `Names as listed by ${src.sourceName}. ` : "";
  return src.ballotList
    ? `${from}The state's list for the November ballot.`
    : `${from}${PRIMARY_RESULTS_CAVEAT}`;
}

/** The state's own contests (statewide offices, the legislature, elected
 * judgeships) as its ballot page showed them: who was on the ballot, from
 * whose list. Civitas read no count for these. */
function StateContests({ contests }: { contests: ArchivedStateContests }) {
  const heading = "font-mono text-xs uppercase tracking-[0.14em] text-ink-min";
  const row =
    "grid gap-x-4 gap-y-0.5 border-b border-white/[0.07] px-4 py-2.5 text-sm last:border-b-0 sm:grid-cols-[14rem_minmax(0,1fr)]";
  return (
    <div className="space-y-4">
      {contests.statewideRaces.length > 0 && (
        <div className="space-y-2">
          <h4 className={heading}>Statewide offices</h4>
          <ul className="border border-white/[0.09] bg-surface">
            {contests.statewideRaces.map((r) => (
              <li key={r.office} className={row}>
                <span className="text-ink-lo">{r.label}</span>
                <NomineeList nominees={r.nominees} />
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-min">{sourceLine(contests.statewideSource)}</p>
        </div>
      )}
      {contests.stateLegRaces.map((chamber) => (
        <div key={chamber.chamber} className="space-y-2">
          <h4 className={heading}>{chamber.label}</h4>
          <ul className="border border-white/[0.09] bg-surface">
            {chamber.districts.map((d) => (
              <li key={d.district} className={row}>
                <span className="text-ink-lo">District {d.district}</span>
                <NomineeList nominees={d.nominees} />
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-min">{sourceLine(contests.statewideSource)}</p>
        </div>
      ))}
      {contests.judicialRaces.map((court) => (
        <div key={court.court} className="space-y-2">
          <h4 className={heading}>{court.label}</h4>
          <ul className="border border-white/[0.09] bg-surface">
            {court.seats.map((seat) => (
              <li key={seat.seat} className={row}>
                <span className="text-ink-lo">{seat.seat}</span>
                <NomineeList nominees={seat.nominees} />
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-min">{sourceLine(contests.judicialSource)}</p>
        </div>
      ))}
      <p className="text-xs text-ink-min">
        Civitas read no count for the state&apos;s own contests: the state&apos;s results pages have
        them.
      </p>
    </div>
  );
}

function RaceEntry({ race, record }: { race: ArchivedRace; record: ArchiveRecord }) {
  const r = race.result;
  const tag = r ? statusTag(r) : null;
  const source = r ? safeHref(r.sourceUrl) : undefined;
  return (
    <li
      id={`race-${race.id}`}
      tabIndex={-1}
      className="scroll-mt-[var(--header-clearance)] border-b border-white/[0.07] px-4 py-4 last:border-b-0 focus:outline-none focus-visible:ring-1 focus-visible:ring-phos/60"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="font-display text-base font-bold text-ink-hi">
          {race.office === "S"
            ? `U.S. Senate${race.isSpecial ? " (special)" : ""}`
            : `U.S. House · ${raceLabel(race)}`}
        </h4>
        <span
          className={`border px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] ${
            tag ? tag.className : "border-dashed border-white/25 text-ink-min"
          }`}
        >
          {tag ? tag.text : "NO COUNT"}
        </span>
      </div>
      {r ? (
        <>
          <ol className="mt-3 space-y-1.5" aria-label="The count, most votes first">
            {r.candidates.map((c, i) => (
              <li
                key={c.candidateId ?? `${i}-${c.name}`}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 text-sm sm:grid-cols-[minmax(0,1fr)_5rem_8rem]"
              >
                <span className="min-w-0 break-words text-ink">
                  {c.name}{" "}
                  <span className={`font-mono text-xs ${partyTextClass(c.party)}`}>
                    ({partyTag(c.party)})
                  </span>
                </span>
                <span className="text-right font-mono tabular-nums text-ink-hi">
                  {c.pct != null ? `${c.pct.toFixed(1)}%` : "—"}
                </span>
                <span className="hidden text-right font-mono text-xs tabular-nums text-ink-lo sm:block">
                  {c.votes.toLocaleString("en-US")}
                </span>
              </li>
            ))}
          </ol>
          <p className="mt-2 font-mono text-xs leading-relaxed text-ink-min">
            {[reportingText(r), `${r.votesCounted.toLocaleString("en-US")} votes`]
              .filter(Boolean)
              .join(" · ")}{" "}
            ·{" "}
            {source ? (
              <a
                href={source}
                target="_blank"
                rel="noopener noreferrer"
                className="text-ink-lo underline underline-offset-4 hover:text-ink-hi"
              >
                {r.sourceName} ↗
              </a>
            ) : (
              r.sourceName
            )}{" "}
            · last read {formatEasternTime(r.fetchedAt)}
          </p>
        </>
      ) : (
        <p className="mt-2 text-sm text-ink-min">{noCountText(race, record)}</p>
      )}
      {race.candidates.length > 0 ? (
        <p className="mt-2 text-sm text-ink-lo">
          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-min">
            On the ballot
          </span>{" "}
          {race.candidates.map((c, i) => (
            <span key={c.id}>
              {i > 0 && " · "}
              <span className="text-ink">{candidateName(c)}</span>{" "}
              <span className="font-mono text-xs">
                (
                {c.partyLabel && !["DEM", "REP"].includes(c.partyGroup ?? "")
                  ? c.partyLabel
                  : partyTag(c.partyGroup ?? c.party)}
                {c.incumbentChallenge === "I" ? ", incumbent" : ""})
              </span>
            </span>
          ))}
          <span className="block text-xs text-ink-min">
            From {BALLOT_SOURCE[race.candidateSource]}.
          </span>
        </p>
      ) : (
        <p className="mt-2 text-sm text-ink-min">No candidate list was on file for this race.</p>
      )}
    </li>
  );
}

/**
 * One held general election's page (/elections/{cycle}): the map as it
 * stood before election day, each race's count as its state reported it,
 * and the federal ballot and statewide measures as they stood. Everything
 * is the record the backend copied (GET /api/elections/archive/{cycle});
 * nothing is recomputed here, and no race is called.
 */
export default function CycleArchive({
  record,
  map,
  accountability = null,
  initialView,
  initialChamber,
  pageUrl,
}: {
  record: ArchiveRecord;
  /** The frozen map's first chamber; null when none was kept. */
  map: ElectionMapResponse | null;
  /** The frozen map's ranges against the official counts, and the swing
   * map; null when no map was kept. */
  accountability?: Accountability | null;
  initialView: Parameters<typeof ElectionMap>[0]["initialView"];
  initialChamber: Parameters<typeof ElectionMap>[0]["initialChamber"];
  pageUrl: string;
}) {
  const t = record.totals;
  return (
    <div className="space-y-12">
      {record.stillCounting && (
        <p className="border border-signal-amber/40 px-4 py-3 text-sm text-ink-hi">
          The count is still coming in. This record is copied from it as it moves, and is final once
          the results window closes.{" "}
          <Link href="/elections" className="text-phos underline underline-offset-4">
            Live results →
          </Link>
        </p>
      )}

      <dl className="grid grid-cols-2 gap-px border border-white/[0.09] bg-white/[0.07] font-mono sm:grid-cols-5 [&>div:last-child]:col-span-2 sm:[&>div:last-child]:col-span-1">
        {[
          ["Federal races", t.races],
          ["With a count read", t.counted],
          ["Count listed official", t.official],
          ["State contests", t.stateContests ?? 0],
          ["Statewide measures", t.measures],
        ].map(([label, n]) => (
          <div key={label} className="bg-surface-base px-4 py-3">
            <dt className="text-[11px] uppercase tracking-[0.12em] text-ink-min">{label}</dt>
            <dd className="mt-1 text-2xl font-bold tabular-nums text-ink-hi">{n}</dd>
          </div>
        ))}
      </dl>

      <section
        id="final-map"
        aria-labelledby="final-map-title"
        className="scroll-mt-[var(--header-clearance)] space-y-4"
      >
        <div>
          <h2
            id="final-map-title"
            className="font-mono text-sm uppercase tracking-[0.14em] text-ink-hi"
          >
            The map before election day
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-lo">
            {map
              ? `As the campaign's last refresh left it, ${formatAsOf(map.asOf)}: how each seat usually votes and the range the polls suggested. Ranges, not results; kept here as it stood, so it can be read beside the count below.`
              : "No pre-election map was kept for this election: none was on file when the campaign ended."}
          </p>
        </div>
        {map && (
          <ElectionMap
            response={map}
            initialView={initialView}
            initialChamber={initialChamber}
            pageUrl={pageUrl}
            archiveCycle={record.cycle}
          />
        )}
      </section>

      {accountability && (
        <MapAccountability data={accountability} pageUrl={`${pageUrl}#map-check`} />
      )}

      <section
        id="by-state"
        aria-labelledby="by-state-title"
        className="scroll-mt-[var(--header-clearance)] space-y-6"
      >
        <div>
          <h2
            id="by-state-title"
            className="font-mono text-sm uppercase tracking-[0.14em] text-ink-hi"
          >
            Count and ballot, by state
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-ink-lo">
            Each federal race&apos;s count as its state&apos;s own results system last reported it,
            and the whole ballot the site showed: federal candidates, statewide offices, the
            legislature, elected judgeships and statewide measures, quoted from their sources. A
            count the state lists as official still only leads: Civitas calls no race.
            {record.recordAsOf ? ` Recorded ${formatEasternTime(record.recordAsOf)}.` : ""}
          </p>
        </div>
        <nav aria-label="States">
          <ul className="flex flex-wrap gap-1.5 font-mono text-xs">
            {record.states.map((s) => (
              <li key={s.state}>
                <a
                  href={`#state-${s.state}`}
                  className="inline-flex min-h-11 min-w-11 items-center justify-center border border-white/20 px-2 text-ink-lo hover:border-phos hover:text-ink-hi"
                  aria-label={s.stateName}
                >
                  {s.state}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        {record.states.map((s) => {
          const races = record.races.filter((r) => r.state === s.state);
          const measures = record.measures.filter((m) => m.state === s.state);
          return (
            <section
              key={s.state}
              id={`state-${s.state}`}
              aria-labelledby={`state-${s.state}-title`}
              className="scroll-mt-[var(--header-clearance)] space-y-3"
            >
              <h3
                id={`state-${s.state}-title`}
                className="border-b border-phos/20 pb-1 font-display text-xl font-extrabold uppercase text-ink-hi"
              >
                {s.stateName}
              </h3>
              {s.candidateListSource && (
                <p className="text-xs text-ink-min">
                  Candidate names as printed by {s.candidateListSource}.
                </p>
              )}
              {races.length > 0 && (
                <ol className="border border-white/[0.09] bg-surface">
                  {races.map((r) => (
                    <RaceEntry key={r.id} race={r} record={record} />
                  ))}
                </ol>
              )}
              {s.stateContests && <StateContests contests={s.stateContests} />}
              {measures.length > 0 && (
                <div className="space-y-3">
                  <h4 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                    Statewide ballot measures
                  </h4>
                  {measures.map((m) => (
                    <BallotMeasureCard key={m.id} measure={m} />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </section>
    </div>
  );
}
