import Link from "next/link";
import type { ReactNode } from "react";
import { getScoreColor } from "@/lib/representation";
import { displayScore } from "@/lib/formatting";
import {
  COMPUTED_LEAN_HREF,
  candidateName,
  formatPvi,
  houseSeatLabel,
  incumbencyLabel,
  isActiveCandidate,
  isRedrawnSeat,
} from "@/lib/elections";
import { STATE_NAMES } from "@/lib/stateCodes";
import { mapHref } from "@/lib/electionMap";
import type { LeanContext, RaceWithCandidates } from "@/types/election";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import { INLINE_LINK } from "@/lib/controlStyles";

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

/** Every seat of its kind on one left-to-right axis of lean against the
 * nation, most Democratic at the left: a column per figure, one mark per
 * seat stacked in it, this seat's column outlined and labelled. Marks are
 * tinted by the sign of each seat's OWN lean (blue D, red R, grey EVEN),
 * and the EVEN line and the D+/R+ end labels say the same without colour.
 * One SVG scaled to the width, so it is one row at any width: it was a
 * grid of squares that wrapped into rows (58 a row, 25 on a phone), which
 * broke the axis the labels under it described, and tinted seats by which
 * side of THIS seat they fell, so a D+19 seat showed a sea of red.
 * Every figure is the backend's (leanContext.distribution). */
const AXIS_W = 1000;
const AXIS_H = 60;

function LeanAxis({ context, seatLabel }: { context: LeanContext; seatLabel: string }) {
  const dist = context.distribution;
  if (!dist?.length) return null;
  const lo = dist[0].lean;
  const hi = dist[dist.length - 1].lean;
  const colW = AXIS_W / (hi - lo + 1);
  const tallest = Math.max(...dist.map((d) => d.count));
  const markH = AXIS_H / tallest;
  const at = (lean: number) => ((lean - lo + 0.5) * colW) / AXIS_W;
  const seat = at(context.seatLean);
  const even = lo <= 0 && hi >= 0 ? at(0) : null;
  const tint = (lean: number) =>
    lean < 0 ? "fill-dem-blue" : lean > 0 ? "fill-rep-red" : "fill-ink-lo";
  const summary = `Every ${context.unit === "states" ? "state" : "district"}'s lean, from ${dist[0].label} to ${dist[dist.length - 1].label}; this seat, ${seatLabel}, marked. ${leanCountsSentence(context)}`;
  return (
    <div className="mt-3">
      <div role="img" aria-label={summary} data-testid="lean-axis">
        <div
          className="relative h-5 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-hi"
          aria-hidden="true"
        >
          <span
            className="absolute bottom-0 whitespace-nowrap"
            // The ▼ over the seat's column: half its width back past the edge.
            style={
              seat <= 0.5
                ? { left: `${seat * 100}%`, transform: "translateX(-0.3em)" }
                : { right: `${(1 - seat) * 100}%`, transform: "translateX(0.3em)" }
            }
          >
            {seat <= 0.5 ? `▼ this seat · ${seatLabel}` : `this seat · ${seatLabel} ▼`}
          </span>
        </div>
        <svg
          viewBox={`0 0 ${AXIS_W} ${AXIS_H}`}
          preserveAspectRatio="none"
          className="block h-12 w-full"
          aria-hidden="true"
        >
          {dist.map((d) =>
            Array.from({ length: d.count }, (_, k) => (
              <rect
                key={`${d.lean}-${k}`}
                data-lean={d.lean}
                className={tint(d.lean)}
                x={(d.lean - lo) * colW + colW * 0.1}
                width={colW * 0.8}
                y={AXIS_H - (k + 1) * markH + markH * 0.1}
                height={markH * 0.8}
              />
            ))
          )}
          {even != null && (
            <line
              x1={even * AXIS_W}
              x2={even * AXIS_W}
              y1={0}
              y2={AXIS_H}
              className="stroke-ink-min"
              strokeDasharray="3 3"
              vectorEffect="non-scaling-stroke"
            />
          )}
          <rect
            data-seat
            x={(context.seatLean - lo) * colW}
            width={colW}
            y={0}
            height={AXIS_H}
            className="fill-none stroke-ink-hi"
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <div
          className="relative mt-1 flex justify-between gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-min"
          aria-hidden="true"
        >
          <span>← {dist[0].label}</span>
          {even != null && even > 0.2 && even < 0.8 && (
            <span className="absolute -translate-x-1/2" style={{ left: `${even * 100}%` }}>
              EVEN
            </span>
          )}
          <span>{dist[dist.length - 1].label} →</span>
        </div>
      </div>
      <details className="mt-2" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
        <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos">
          Seats at each lean, as a table
        </summary>
        <div
          role="region"
          aria-label={`${context.unit === "states" ? "States" : "Districts"} at each lean`}
          tabIndex={0}
          className="mt-1 max-h-48 overflow-y-auto"
        >
          <table className="font-mono text-xs text-ink-lo">
            <thead>
              <tr>
                <th scope="col" className="pr-6 text-left font-normal text-ink-min">
                  Lean
                </th>
                <th scope="col" className="text-right font-normal text-ink-min">
                  {context.unit === "states" ? "States" : "Districts"}
                </th>
              </tr>
            </thead>
            <tbody>
              {dist.map((d) => (
                <tr
                  key={d.lean}
                  className={d.lean === context.seatLean ? "text-ink-hi" : undefined}
                >
                  <td className="pr-6">
                    {d.label}
                    {d.lean === context.seatLean ? " (this seat)" : ""}
                  </td>
                  <td className="text-right">{d.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/** The signed points of a lean label ("R+3" 3, "D+2" -2, "EVEN" 0), so
 * the sentence and the label are the same number. */
export function usualPoints(label: string): number {
  if (label === "EVEN") return 0;
  const n = Number(label.slice(2));
  return label.startsWith("D") ? -n : n;
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
 * publishes no odds and calls no race. The race links to its cycle's
 * map's poll range for it (lib/electionMap), which is kept off this
 * section on purpose: the section shows the incumbent's Representation
 * Score, and a poll range must never sit beside a score.
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
  const usual = !resultsMode ? (race.usualVote ?? null) : null;
  const lean = !resultsMode && (usual || race.pvi != null) ? race.leanContext : null;
  const incumbent = race.candidates.find(
    (c) => isActiveCandidate(c) && c.incumbentChallenge === "I" && c.incumbentRecord
  );
  const redrawn = isRedrawnSeat(race, newLines);
  const stateName = STATE_NAMES[race.state] ?? race.state;
  // Named in the heading so a shared picture of this section says which
  // race it is; the drawer's own header isn't in the picture.
  const seatName =
    race.district != null ? houseSeatLabel(race.state, race.district) : `${stateName} U.S. Senate`;

  return (
    <section
      aria-labelledby={`context-${race.id}`}
      className="mb-5 bg-surface-base"
      {...{ [SHARE_SECTION_ATTR]: `context-${race.id}` }}
      {...shareMetaAttrs({
        source:
          "Cook PVI; Civitas-computed lean from open precinct returns; Civitas representation scores",
        // Read when the page loads.
        asOf: null,
        scaleMax: incumbent?.incumbentRecord ? 100 : null,
      })}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-white/[0.09] pb-1">
        <h3
          id={`context-${race.id}`}
          className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min"
        >
          What shapes this race · {seatName}
        </h3>
        {/* The figures below as data, and how they are made: links, so
            left out of a picture of the section. */}
        <span className="flex items-center gap-x-2 font-mono text-[11px] uppercase tracking-[0.1em]">
          <ShareSectionButton label={`What shapes this race: ${seatName}`} anchor={null} />
          <span className="flex gap-x-2" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
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
        </span>
      </div>
      <p className="mt-2 text-[13px] text-ink-lo">
        {resultsMode
          ? "Civitas calls no race. A count on this page is the state's own, linked to its source."
          : "Descriptive only: Civitas publishes no odds for this race and calls no race."}
        {!resultsMode && (
          <>
            {" "}
            The {race.cycleYear} {race.district == null ? "Senate" : "House"} map shows a range for
            its margin, from polls where there are any, beside how the{" "}
            {race.district == null ? "state" : "district"} usually votes.{" "}
            <Link
              href={mapHref("polls", race.district == null ? "senate" : "house", race.id)}
              className="whitespace-nowrap font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            >
              [ {race.district == null ? "SENATE" : "HOUSE"} MAP → ]
            </Link>
          </>
        )}
      </p>
      <dl>
        {lean && usual && (
          <Row label={`The seat · ${usual.label}`}>
            {leanSentence(usualPoints(usual.label), lean.unit)} {leanCountsSentence(lean)}
            <LeanAxis context={lean} seatLabel={usual.label} />
            <p className="mt-2 text-[13px]">
              {usual.source === "computed"
                ? `Computed by Civitas from open precinct returns, on this election's district lines: the figure the ${race.cycleYear} map shows, used in no score.`
                : `Computed by Civitas from each state's official presidential returns: the figure the ${race.cycleYear} map shows.`}{" "}
              {race.pvi != null &&
                race.pviLevel === (race.district == null ? "state" : "district") && (
                  <>
                    Cook PVI, as a cross-check:{" "}
                    <span className="font-mono text-ink">{formatPvi(race.pvi)}</span>.{" "}
                  </>
                )}
              <Link href={COMPUTED_LEAN_HREF} className={INLINE_LINK}>
                How it is computed
              </Link>
            </p>
          </Row>
        )}
        {lean && !usual && race.pvi != null && (
          <Row label={`The seat · Cook PVI ${formatPvi(race.pvi)}`}>
            {leanSentence(race.pvi, lean.unit)} {leanCountsSentence(lean)}
            <LeanAxis context={lean} seatLabel={formatPvi(race.pvi)} />
            <p className="mt-2 text-[13px]">
              Cook&apos;s figure: Civitas has no computed lean for this seat on these lines.
            </p>
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
