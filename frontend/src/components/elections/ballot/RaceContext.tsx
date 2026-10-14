import Link from "next/link";
import type { ReactNode } from "react";
import { getScoreColor } from "@/lib/representation";
import { displayScore } from "@/lib/formatting";
import {
  candidateName,
  formatPvi,
  incumbencyLabel,
  isActiveCandidate,
  isRedrawnSeat,
} from "@/lib/elections";
import { STATE_NAMES } from "@/lib/stateCodes";
import type { LeanContext, RaceWithCandidates } from "@/types/election";

/** The seat's lean in words: what R+5 means, for a reader who has never
 * seen a PVI. "The last two presidential elections" is the PVI's own
 * definition, so the sentence stays true whichever two they are. */
export function leanSentence(pvi: number, unit: LeanContext["unit"]): string {
  const seat = unit === "states" ? "state" : "district";
  if (pvi === 0) {
    return `In the last two presidential elections, this ${seat} voted about the same as the nation.`;
  }
  const side = pvi > 0 ? "Republican" : "Democratic";
  const points = Math.abs(pvi);
  return `In the last two presidential elections, this ${seat} voted about ${points} ${points === 1 ? "point" : "points"} more ${side} than the nation.`;
}

/** "236 of 435 districts lean more Democratic, 187 more Republican, 11 the
 * same." Every count is the backend's (leanContext). */
export function leanCountsSentence(c: LeanContext): string {
  const same = c.same ? `, ${c.same} the same` : "";
  return `${c.moreDemocratic} of ${c.of} ${c.unit} lean more Democratic, ${c.moreRepublican} more Republican${same}.`;
}

/** One square per seat of its kind, most Democratic-leaning first: those
 * leaning further Democratic, those level with this seat, this seat, then
 * those leaning further Republican. Tinted by which side of THIS seat they
 * lean, not by party, and this seat in ink. */
function LeanSquares({ context }: { context: LeanContext }) {
  const cells = [
    ...Array<string>(context.moreDemocratic).fill("bg-dem-blue/45"),
    ...Array<string>(context.same).fill("bg-white/35"),
    "bg-ink-hi outline outline-1 outline-offset-1 outline-ink-hi",
    ...Array<string>(context.moreRepublican).fill("bg-rep-red/45"),
  ];
  const cols =
    context.unit === "states"
      ? "grid-cols-[repeat(25,minmax(0,1fr))] sm:grid-cols-[repeat(50,minmax(0,1fr))]"
      : "grid-cols-[repeat(58,minmax(0,1fr))]";
  return (
    <div role="img" aria-label={leanCountsSentence(context)} className="mt-2">
      <div className={`grid gap-[2px] ${cols}`} aria-hidden="true">
        {cells.map((cls, i) => (
          <span key={i} className={`aspect-square ${cls}`} />
        ))}
      </div>
      <div
        className="mt-1 flex justify-between font-mono text-[10px] uppercase tracking-[0.12em] text-ink-min"
        aria-hidden="true"
      >
        <span>← more Democratic</span>
        <span>more Republican →</span>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-b border-white/[0.07] py-3 last:border-b-0">
      <dt className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-min">{label}</dt>
      <dd className="mt-1 text-[14px] leading-relaxed text-ink-lo">{children}</dd>
    </div>
  );
}

/** What shapes this race, before its money: where the seat leans and how
 * that compares with every other seat, whether the member who holds it is
 * running, and that none of it is a forecast. Descriptive only — Civitas
 * publishes no prediction and calls no race.
 *
 * From election day (`resultsMode`) the lean is left out, as it is from the
 * page header: beside a live count it reads as a prediction of it. */
export default function RaceContext({
  race,
  newLines = false,
  resultsMode = false,
}: {
  race: RaceWithCandidates;
  newLines?: boolean;
  resultsMode?: boolean;
}) {
  const lean = !resultsMode && race.pvi != null ? race.leanContext : null;
  const incumbent = race.candidates.find(
    (c) => isActiveCandidate(c) && c.incumbentChallenge === "I" && c.incumbentRecord
  );
  const redrawn = isRedrawnSeat(race, newLines);
  const stateName = STATE_NAMES[race.state] ?? race.state;

  return (
    <section aria-labelledby={`context-${race.id}`} className="mb-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-white/[0.09] pb-1">
        <h3
          id={`context-${race.id}`}
          className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min"
        >
          What shapes this race
        </h3>
        {/* The figures below as data, and how they are made. */}
        <span className="flex gap-x-2 font-mono text-[11px] uppercase tracking-[0.1em]">
          <a
            href={`/api/elections/states/${race.state}/races.csv`}
            download
            aria-label={`CSV: every federal candidate in ${stateName}`}
            className="inline-flex min-h-[24px] items-center text-signal-cyan hover:text-phos"
          >
            [ CSV ]
          </a>
          <a
            href={`/api/elections/races/${race.id}`}
            aria-label="JSON: this race"
            className="inline-flex min-h-[24px] items-center text-signal-cyan hover:text-phos"
          >
            [ JSON ]
          </a>
          <Link
            href="/about/elections#race-context"
            aria-label="Method: how these figures are made"
            className="inline-flex min-h-[24px] items-center text-signal-cyan hover:text-phos"
          >
            [ METHOD ]
          </Link>
        </span>
      </div>
      <p className="mt-2 text-[13px] text-ink-lo">
        {resultsMode
          ? "Civitas calls no race. A count on this page is the state's own, linked to its source."
          : "Descriptive only: Civitas publishes no prediction or odds for this race."}
      </p>
      <dl>
        {lean && race.pvi != null && (
          <Row label={`The seat · ${formatPvi(race.pvi)}`}>
            {leanSentence(race.pvi, lean.unit)} {leanCountsSentence(lean)}
            <LeanSquares context={lean} />
          </Row>
        )}
        {incumbent?.incumbentRecord && (
          <Row
            label={
              incumbencyLabel("I", redrawn, incumbent.incumbentRecord.seat, resultsMode) ??
              "Incumbent"
            }
          >
            {candidateName(incumbent)} is on the ballot. Civitas score for this member&apos;s
            record:{" "}
            <span
              className={`font-mono ${getScoreColor(displayScore(incumbent.incumbentRecord.score))}`}
            >
              {displayScore(incumbent.incumbentRecord.score)}
            </span>{" "}
            of 100.{" "}
            <Link
              href={`/politicians/${incumbent.incumbentRecord.id}`}
              className="whitespace-nowrap font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
            >
              [ SCORECARD → ]
            </Link>
          </Row>
        )}
      </dl>
    </section>
  );
}
