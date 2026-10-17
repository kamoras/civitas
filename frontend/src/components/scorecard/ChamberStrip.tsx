import Link from "next/link";
import type { ChamberField, ChamberNeighbour } from "@/types/politicians";
import { houseSeatLabel } from "@/lib/elections";
import { PARTY_COLORS } from "@/lib/partyStyles";

// Each member is one square in their score's column, stacked by party in
// this order from the axis up, so a party reads as one band across the strip.
const PARTY_ORDER = ["D", "I", "R"];
const FILL: Record<string, string> = {
  D: "fill-dem-blue",
  R: "fill-signal-red",
  I: "fill-ind-purple",
};
const PARTY_NOUN: Record<string, string> = { D: "Democrats", R: "Republicans", I: "Independents" };
// Square and gap, in the SVG's own units (it scales to the column's width).
const CELL = 4;
const GAP = 1;
// Room above the stacks for the member's marker.
const HEAD = 9;
// The stacks' height, at most, in the same units: a House column holds
// dozens of members, and at full squares the strip took a phone's screen.
// Past it a member's row is flattened, still one row per member.
const STACK_MAX = 44;

function seat(n: ChamberNeighbour, chamber: "senate" | "house"): string {
  return chamber === "house" && n.district != null
    ? `${n.party}-${houseSeatLabel(n.state, n.district)}`
    : `${n.party}-${n.state}`;
}

/**
 * Where the member sits in the chamber: every serving member as a square in
 * the column of their Representation Score, coloured by party, with this
 * member's column marked and the party and chamber medians under the axis.
 * Then the two members ranked next to them, as links, so a reader can walk
 * the leaderboard one profile at a time, or put a neighbour beside this
 * member on the compare page.
 *
 * Every figure is the profile API's (`chamberField`, computed over the same
 * field the rank is): this only places them. A unit chart rather than a
 * histogram's bars, so the reader counts people, not a curve.
 */
export default function ChamberStrip({
  field,
  memberId,
  name,
  score,
  rank,
  chamber,
}: {
  field: ChamberField;
  memberId: string;
  name: string;
  /** The member's score as displayed (the header's big number). */
  score: number;
  rank: { rank: number; of: number };
  chamber: "senate" | "house";
}) {
  const scores = field.scores.map((s) => s.score);
  // Whole tens around the field: the strip spends its width where members are.
  const lo = Math.floor(Math.min(...scores, score) / 10) * 10;
  const hi = Math.ceil(Math.max(...scores, score) / 10) * 10;
  const columns: Record<number, string[]> = {};
  for (const s of field.scores) (columns[s.score] ??= []).push(s.party);
  for (const parties of Object.values(columns)) {
    parties.sort((a, b) => PARTY_ORDER.indexOf(a) - PARTY_ORDER.indexOf(b));
  }
  const tallest = Math.max(...Object.values(columns).map((c) => c.length));
  const step = CELL + GAP;
  const row = Math.min(step, STACK_MAX / tallest);
  const width = (hi - lo + 1) * step;
  const stackTop = HEAD;
  const height = HEAD + tallest * row;
  const x = (v: number) => (v - lo) * step;
  const members = chamber === "senate" ? "senators" : "representatives";
  const partyNoun = PARTY_NOUN[field.party] ?? "the party";
  const summary =
    `Representation Scores of all ${rank.of} serving ${members}, one square each, ` +
    `from ${lo} to ${hi}. ${name} scores ${score}, ranked ${rank.rank} of ${rank.of}.` +
    (field.partyMedian != null ? ` Median among ${partyNoun}: ${field.partyMedian}.` : "") +
    ` Median of the chamber: ${field.chamberMedian}.`;
  const compare = (n: ChamberNeighbour) =>
    `/compare?leftId=${encodeURIComponent(memberId)}&leftChamber=${chamber}&rightId=${encodeURIComponent(n.id)}&rightChamber=${chamber}`;

  return (
    <div className="flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full"
        role="img"
        aria-label={summary}
      >
        {Object.entries(columns).map(([s, parties]) =>
          parties.map((party, i) => (
            <rect
              key={`${s}-${i}`}
              x={x(Number(s))}
              y={height - (i + 1) * row}
              width={CELL}
              height={row * (CELL / step)}
              className={FILL[party] ?? "fill-ink-min"}
            />
          ))
        )}
        {/* The member's column: a rule from the marker down through the stack. */}
        <rect
          x={x(score) + CELL / 2 - 0.5}
          y={2}
          width={1}
          height={height - 2}
          className="fill-ink-hi"
        />
        <rect
          x={x(score) - 1}
          y={stackTop - 5}
          width={CELL + 2}
          height={4}
          className="fill-ink-hi"
        />
      </svg>
      {/* A row, not three absolutely placed labels: at a large text size the
          middle one wraps instead of running past the strip (WCAG 1.4.10). */}
      <div
        className="flex items-start justify-between gap-2 font-mono text-xs tabular-nums text-ink-min"
        aria-hidden="true"
      >
        <span>{lo}</span>
        <span className="min-w-0 text-center uppercase tracking-[0.12em]">
          All {rank.of} {members}
        </span>
        <span>{hi}</span>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-ink-lo">
        {field.partyMedian != null && (
          <span>
            <span className={PARTY_COLORS[field.party] ?? "text-ink"}>{partyNoun}</span> median{" "}
            <span className="tabular-nums text-ink-hi">{field.partyMedian}</span>
          </span>
        )}
        <span>
          Chamber median <span className="tabular-nums text-ink-hi">{field.chamberMedian}</span>
        </span>
      </p>
      {(field.above || field.below) && (
        <nav aria-label="Ranked next to this member" className="flex flex-col">
          {[
            { label: "Ranked above", n: field.above },
            { label: "Ranked below", n: field.below },
          ].map(({ label, n }) =>
            n ? (
              <p
                key={label}
                className="flex flex-wrap items-baseline justify-between gap-x-3 border-t border-white/[0.07] py-1.5 text-sm"
              >
                <span className="min-w-0">
                  <span className="mr-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                    {label}
                  </span>
                  <Link
                    href={`/politicians/${encodeURIComponent(n.id)}`}
                    className="text-ink underline decoration-white/30 underline-offset-4 hover:text-phos"
                  >
                    {n.name}
                  </Link>{" "}
                  <span className={`font-mono text-xs ${PARTY_COLORS[n.party] ?? "text-ink-lo"}`}>
                    {seat(n, chamber)}
                  </span>{" "}
                  <span className="font-mono text-xs tabular-nums text-ink-lo">
                    #{n.rank} · {n.score}
                  </span>
                </span>
                <Link
                  href={compare(n)}
                  className="inline-flex min-h-6 items-center font-mono text-xs text-ink-lo underline underline-offset-2 hover:text-phos"
                  aria-label={`Compare ${name} with ${n.name}`}
                >
                  Compare
                </Link>
              </p>
            ) : null
          )}
        </nav>
      )}
    </div>
  );
}
