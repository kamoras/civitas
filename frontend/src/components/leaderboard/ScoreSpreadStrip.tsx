import type { ScoreSpread } from "@/types/senator";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { SERIES } from "@/components/admin/charts/palette";

/**
 * Every serving member of a chamber as one tally mark, stacked on the whole-
 * number score the leaderboard shows: a tally sheet of the chamber, not a
 * chart template.
 *
 * It is there to stop a rank being read as more than it is. Members who
 * show the same score share a rank, and most of a chamber sits in a narrow
 * band, so ten places can be one point. The column for the row under the
 * pointer or keyboard focus lights up, so the table and the field read
 * together.
 *
 * Every figure is the backend's (GET /api/leaderboard/spread); this only
 * places marks.
 */

// The highlight is a mark colour from the admin charts' validated palette,
// not party blue or red and not phosphor, which the site keeps for status.
const HIGHLIGHT = SERIES[1];

// One member: a mark 5 units wide and 2 tall in a 6 x 3 cell. Flat marks,
// not squares, keep the House's tallest stack (28 members on 2026-10-13)
// short enough to leave the table in the first screen.
const CELL_W = 6;
const CELL_H = 3;
// Never more than this many pixels per unit, so the strip stops growing on
// a wide screen.
const MAX_SCALE = 2;

function chamberNoun(chamber: ScoreSpread["chamber"], n: number) {
  if (chamber === "senate") return n === 1 ? "senator" : "senators";
  return n === 1 ? "representative" : "representatives";
}

export default function ScoreSpreadStrip({
  spread,
  highlight,
}: {
  spread: ScoreSpread;
  /** The displayed score of the row being pointed at or focused, if any. */
  highlight: number | null;
}) {
  if (spread.counts.length === 0 || spread.p25 == null || spread.p75 == null) return null;
  const lo = Math.floor(spread.counts[0].score / 5) * 5;
  const hi = Math.ceil((spread.counts[spread.counts.length - 1].score + 1) / 5) * 5;
  const tallest = Math.max(...spread.counts.map((c) => c.members));
  const width = (hi - lo) * CELL_W;
  const plotH = tallest * CELL_H;
  const height = plotH + 4;
  const x = (score: number) => (score - lo) * CELL_W;
  const ticks = Array.from({ length: Math.floor((hi - lo) / 10) + 1 }, (_, i) => lo + i * 10);
  const shared = highlight == null ? null : spread.counts.find((c) => c.score === highlight);
  const noun = chamberNoun(spread.chamber, spread.total);

  const chamberName = spread.chamber === "senate" ? "Senate" : "House";
  return (
    <ShareSubjectProvider
      subject={{
        title: `The ${chamberName}, by representation score`,
        subtitle: `${spread.total} serving ${noun}`,
        url: absoluteUrl(`/leaderboard?branch=${spread.chamber}`),
        source: `Civitas representation scores: FEC filings; roll calls as ${spread.chamber === "senate" ? "Senate.gov" : "the House Clerk"} records them; Voteview; Cook PVI; Congress.gov bill status`,
        asOf: spread.asOf ?? null,
        scaleMax: 100,
      }}
    >
      {/* The share section is this div, not the figure: a capture's clone
          kept the figure's user-agent margin (40px each side) and cut off
          its right edge. */}
      <div {...{ [SHARE_SECTION_ATTR]: `${spread.chamber}-field` }} className="bg-surface-base">
        <figure className="m-0 border border-white/[0.09] px-4 pb-3 pt-2">
          <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 font-mono text-xs tracking-[0.08em]">
            <span className="flex items-baseline gap-3">
              <span className="uppercase tracking-[0.16em] text-ink-min">The field, by score</span>
              <ShareSectionButton label="The field, by score" anchor={null} />
            </span>
            <span className="text-ink-lo" aria-live="polite">
              {shared ? (
                <>
                  <span style={{ color: HIGHLIGHT }}>&#9632;</span> {shared.members}{" "}
                  {chamberNoun(spread.chamber, shared.members)} show {shared.score}
                  {shared.members > 1 ? " and share one rank" : ""}
                </>
              ) : (
                <>
                  Middle half: {spread.p25}–{spread.p75} · median {spread.median}
                </>
              )}
            </span>
          </figcaption>
          <div className="mt-2" style={{ maxWidth: width * MAX_SCALE }}>
            <svg
              viewBox={`0 0 ${width} ${height}`}
              className="block h-auto w-full"
              role="img"
              aria-label={`${spread.total} ${noun} on ${spread.counts.length} different scores, from ${spread.counts[0].score} to ${spread.counts[spread.counts.length - 1].score}; half score between ${spread.p25} and ${spread.p75}.`}
            >
              {/* The middle half, as a bracket under the marks. */}
              <rect
                x={x(spread.p25)}
                y={plotH + 2}
                width={x(spread.p75) - x(spread.p25) + CELL_W - 1}
                height={1.5}
                className="fill-ink-min"
              />
              {spread.counts.map((c) => {
                const on = c.score === highlight;
                return Array.from({ length: c.members }, (_, i) => (
                  <rect
                    key={`${c.score}-${i}`}
                    x={x(c.score)}
                    y={plotH - (i + 1) * CELL_H}
                    width={CELL_W - 1}
                    height={CELL_H - 1}
                    fill={on ? HIGHLIGHT : undefined}
                    className={on ? undefined : "fill-ink-min"}
                    fillOpacity={on ? 1 : 0.55}
                  />
                ));
              })}
            </svg>
            {/* Axis labels in HTML, placed by percentage, so they stay legible
            text at any width instead of scaling with the drawing. */}
            <div aria-hidden="true" className="relative h-4 font-mono text-xs text-ink-min">
              {ticks.map((t) => (
                <span
                  key={t}
                  className="absolute -translate-x-1/2"
                  style={{ left: `${((x(t) + (CELL_W - 1) / 2) / width) * 100}%` }}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
          <p className="mt-1 font-sans text-sm leading-snug text-ink-lo">
            Each mark is one {chamberNoun(spread.chamber, 1)}, stacked on the score the table shows.{" "}
            {spread.total} {noun} show {spread.counts.length} different scores, so members with the
            same score share a rank, and a gap of several places can be a single point.
          </p>
        </figure>
      </div>
    </ShareSubjectProvider>
  );
}
