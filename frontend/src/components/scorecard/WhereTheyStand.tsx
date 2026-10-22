"use client";

import HowToRead from "@/components/shared/HowToRead";
import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchPeerComparison } from "@/lib/api";
import ChartReadout from "@/components/shared/ChartReadout";
import type { PeerComparison, PeerGroupKey, PeerMeasure } from "@/types/peers";
import { onThisPage } from "@/lib/onThisPage";
import StripPoints, { StripAxis, stripTop, type StripPoint } from "@/components/shared/Strip";
import CountHistogram, { wholeCounts } from "@/components/shared/CountHistogram";
import { TYPE } from "@/lib/typeStyles";
import { More } from "@/components/shared/RecordBox";

/** The page's own member's mark, on a strip and a histogram alike. */
const MEMBER_MARK = { fill: "var(--ink-hi)", ring: "var(--phos)" };

/** A round mark. Drawn as SVG, not a rounded box: the site's boxes keep
 *  hard corners, and a dot is a data mark, not a box. */
export function Dot({
  size,
  fill,
  ring,
  className = "",
  style,
}: {
  size: number;
  fill: string;
  ring?: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const r = size / 2;
  return (
    <svg width={size} height={size} className={className} style={style}>
      <circle
        cx={r}
        cy={r}
        r={ring ? r - 1 : r}
        fill={fill}
        stroke={ring}
        strokeWidth={ring ? 2 : 0}
      />
    </svg>
  );
}

/** A dot's height in the band, the same every render, so ties stay
 *  countable without moving (a beeswarm pressed into 20px). */
const JITTER = [10, 4, 16, 7, 13, 1, 19, 10];

/** A small mark's centre in the band: its jittered top plus its radius. */
const jitterY = (i: number, size: number) => 12 + JITTER[i % JITTER.length] / 2 + size / 2;

function Strip({ m, memberId }: { m: PeerMeasure; memberId: string }) {
  if (wholeCounts(m.unit, m.distribution)) {
    // A whole number: a dot histogram, so ties stand side by side.
    return (
      <CountHistogram
        label={`${m.label}, ${m.peerGroup.label}`}
        points={m.distribution.map((v, i) => ({
          id: m.distributionIds[i],
          name: m.distributionNames[i],
          detail: m.distributionSeats[i],
          shown: m.distributionShown[i],
          rank: m.distributionRanks[i],
          href: `/politicians/${m.distributionIds[i]}`,
          value: v,
        }))}
        marked={{ [memberId]: MEMBER_MARK }}
        startId={memberId}
        tick={
          m.expected == null || m.expectedShown == null
            ? null
            : { value: m.expected, text: m.expectedShown }
        }
      />
    );
  }
  const max = stripTop(m.axisMax, [...m.distribution, m.value, m.expected]);
  const at = (v: number) => Math.min(100, (v / max) * 100);
  const muted = m.thin;
  const points: StripPoint[] = m.distribution.map((v, i) => {
    const id = m.distributionIds[i];
    return {
      id,
      name: m.distributionNames[i],
      detail: m.distributionSeats[i],
      shown: m.distributionShown[i],
      rank: m.distributionRanks[i],
      href: `/politicians/${id}`,
      x: at(v),
      y: id === memberId ? 22 : jitterY(i, 6),
    };
  });
  return (
    <div className="min-w-0">
      {/* The table below is the full text form of this strip. */}
      <StripPoints
        label={`${m.label}, ${m.peerGroup.label}`}
        points={points}
        startId={memberId}
        className="h-10"
      >
        <div className="absolute inset-x-0 top-[22px] h-px bg-white/40" />
        {m.distribution.map((v, i) => (
          <Dot
            key={m.distributionIds[i]}
            size={6}
            fill={
              muted ? "color-mix(in srgb, var(--overlay) 25%, transparent)" : "var(--chart-muted)"
            }
            className="absolute -translate-x-1/2"
            style={{ left: `${at(v)}%`, top: `${jitterY(i, 0)}px` }}
          />
        ))}
        {m.expected != null && (
          <span
            className="absolute top-1 h-8 -translate-x-1/2 border-l-2 border-dashed border-ink-hi"
            style={{ left: `${at(m.expected)}%` }}
          />
        )}
        {m.value != null && (
          <Dot
            size={14}
            fill="var(--ink-hi)"
            ring="var(--phos)"
            className="absolute top-[15px] -translate-x-1/2"
            style={{ left: `${at(m.value)}%` }}
          />
        )}
      </StripPoints>
      <StripAxis
        start={m.axisStartShown}
        end={m.axisEndShown}
        tick={
          m.expected == null || m.expectedShown == null
            ? null
            : { at: at(m.expected), text: m.expectedShown }
        }
      />
    </div>
  );
}

/** This measure over time: a line of the member's own figure, night by
 *  night this Congress, once it has changed (the API's points and words,
 *  peer_comparison.over_time). Each night reads on hover, tap and the
 *  keyboard; the sentence after it gives the first and latest. */
function OverTime({ m }: { m: PeerMeasure }) {
  const t = m.overTime;
  if (!t) return null;
  const marks = t.marks.map((mk, i) => ({ ...mk, x: t.points[i].x, yPct: t.points[i].y }));
  return (
    <div className="flex flex-wrap items-center gap-2 text-small text-ink-lo">
      <ChartReadout
        label={`${m.label} over time`}
        marks={marks}
        startId={marks[marks.length - 1]?.id}
        className="h-6 w-28"
      >
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="block h-6 w-28">
          <polyline
            points={t.points.map((pt) => `${pt.x},${pt.y}`).join(" ")}
            fill="none"
            stroke="var(--ink-hi)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </ChartReadout>
      <p>{t.sentence}</p>
    </div>
  );
}

/** The member's figure, its unit on the same line and smaller ("0 of 493"),
 *  so the cell spends one line on it. */
function InlineFigure({ shown }: { shown: string | null }) {
  const parts = shown == null ? null : /^([+\-−]?[\d.,]+%?)\s+(.+)$/.exec(shown);
  return (
    <p className={`${TYPE.figure} text-ink-hi`}>
      {parts ? (
        <>
          {parts[1]} <span className="text-small font-semibold text-ink-lo">{parts[2]}</span>
        </>
      ) : (
        (shown ?? "No record")
      )}
    </p>
  );
}

/** One measure as a cell of the small multiple: its label and word, the
 *  member's figure, the strip, and the backend's sentence; why it matters,
 *  how it is measured and over time sit behind "How this is measured". */
function Cell({
  m,
  memberId,
  showTable,
}: {
  m: PeerMeasure;
  memberId: string;
  showTable: boolean;
}) {
  const strong = (m.word ?? "").startsWith("well");
  const captionId = useId();
  return (
    <figure
      aria-labelledby={captionId}
      className="relative flex min-w-0 flex-col gap-2 border-b border-white/25 pt-3 pb-4 before:absolute before:inset-y-0 before:-left-5 before:w-px before:bg-white/25"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h3 className={TYPE.kicker}>{m.label}</h3>
        <p className={`text-small font-semibold ${strong ? "text-signal-orange" : "text-ink-lo"}`}>
          {m.thin ? "" : m.word}
        </p>
      </div>
      <InlineFigure shown={m.shown} />
      <Strip m={m} memberId={memberId} />
      <figcaption id={captionId} className="text-small text-ink">
        <span className="sr-only">{m.label}: </span>
        {m.thin
          ? `Not enough record yet to compare (${m.record != null && m.key === "breaks" ? `${m.record} party-line votes` : `${m.peerGroup.n} with a figure`}).`
          : m.sentence}
        {!m.thin && m.word && <span className="sr-only"> {m.word}.</span>}
      </figcaption>
      <More label="How this is measured" bare className="mt-0">
        <div className="flex flex-col gap-2 text-small text-ink-lo">
          <p>
            <span className="font-semibold text-ink-hi">{m.question}</span> {m.why}
          </p>
          <p className="max-w-prose">{m.measured}</p>
          <OverTime m={m} />
          <p>
            {m.peerGroup.label}, n = {m.peerGroup.n}
            {m.percentile != null && ` · percentile ${m.percentile}`} ·{" "}
            <a
              href={m.source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-phos underline underline-offset-2"
            >
              {m.source.name} ↗
            </a>{" "}
            ·{" "}
            <Link href="/about/scores#peers" className="text-phos underline underline-offset-2">
              Methods and sources
            </Link>
          </p>
        </div>
      </More>
      <div className={showTable ? "" : "sr-only"}>
        <table className="w-full text-small">
          <caption className="text-left text-small text-ink-min">
            {m.label}:{" "}
            {m.peerGroup.labelOne
              ? `every ${m.peerGroup.labelOne}'s figure`
              : `every figure among the ${m.peerGroup.label}`}
            , lowest first
          </caption>
          <thead>
            <tr className="text-left text-small text-ink-min">
              <th scope="col" className="py-1 font-semibold">
                Member
              </th>
              <th scope="col" className="py-1 text-right font-semibold">
                {m.label}
              </th>
            </tr>
          </thead>
          <tbody>
            {m.distributionIds.map((id, i) => (
              <tr key={id} className="border-t border-white/10">
                <td className="py-1">
                  <Link
                    href={`/politicians/${m.distributionIds[i]}`}
                    // Out of the Tab order while the table is only for screen
                    // readers: a sighted keyboard user otherwise tabbed
                    // through every peer's link with nothing on screen.
                    tabIndex={showTable ? undefined : -1}
                    className="underline-offset-2 hover:underline"
                  >
                    {m.distributionNames[i]}
                  </Link>
                </td>
                <td className="py-1 text-right">{m.distributionShown[i]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

const SEGMENT_ON = "bg-ink-hi text-surface-base";
const SEGMENT_OFF = "bg-surface text-ink-hi";
const SEGMENT =
  "inline-flex min-h-8 cursor-pointer items-center border border-ink-hi px-2.5 text-small font-semibold has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2";

/**
 * "Where they stand": one row per measure, the member among peers on the
 * measure's own scale, the figure the seat predicts as a tick, and a word
 * by the backend's fixed rule. Every figure, percentile and word comes from
 * the API (backend app/peer_comparison.py); this only places them.
 */
export default function WhereTheyStand({
  memberId,
  name,
  chamber,
  initial,
}: {
  memberId: string;
  name: string;
  chamber: "senate" | "house";
  /** The caucus comparison, read by the server page. */
  initial?: PeerComparison | null;
}) {
  const params = useSearchParams();
  // Latched at mount: the URL describes how the page was opened, and the
  // toggle writes it back with replaceState (AGENTS.md, URL state traps).
  const [group, setGroup] = useState<PeerGroupKey>(() =>
    params?.get("peers") === "chamber" ? "chamber" : "caucus"
  );
  const [showTable, setShowTable] = useState(false);
  const { data, error } = useAsyncData(
    `${chamber}/${memberId}/${group}`,
    () => fetchPeerComparison(chamber, memberId, group),
    initial ? { key: `${chamber}/${memberId}/caucus`, data: initial } : undefined
  );
  const choose = (next: PeerGroupKey) => {
    setGroup(next);
    const url = new URL(window.location.href);
    if (next === "chamber") url.searchParams.set("peers", "chamber");
    else url.searchParams.delete("peers");
    window.history.replaceState(null, "", url);
  };
  // The caucus group's own name ("Senate Democrats and independents"),
  // from whichever caucus comparison is at hand.
  const caucusText =
    (group === "caucus" ? data : initial)?.measures[0]?.peerGroup.label ?? "Same party";
  const whole = chamber === "senate" ? "Whole Senate" : "Whole House";

  // The peer group's choice: beside the heading from sm up; on a phone
  // under the strips, beside the table and compare buttons, so the first
  // strip stays on the first screen. One is shown at a time (display), each
  // its own radio group.
  // min-w-0: a fieldset is min-content wide by default, and at 200% text the
  // two segments side by side were wider than a phone.
  const toggle = (where: "head" | "foot") => (
    <fieldset
      className={`${where === "head" ? "hidden sm:flex" : "flex sm:hidden"} min-w-0 max-w-full flex-wrap items-center gap-2`}
    >
      <legend className="sr-only">Compare with</legend>
      <span aria-hidden="true" className="text-small text-ink-lo">
        Compared with:
      </span>
      <span className="inline-flex max-w-full flex-wrap">
        {(
          [
            ["caucus", caucusText],
            ["chamber", whole],
          ] as const
        ).map(([key, label]) => (
          <label
            key={key}
            className={`${SEGMENT} ${group === key ? SEGMENT_ON : SEGMENT_OFF} -ml-px first:ml-0`}
          >
            <input
              type="radio"
              name={`peers-${memberId}-${where}`}
              value={key}
              checked={group === key}
              onChange={() => choose(key)}
              className="sr-only"
            />
            {key === "chamber" ? (
              // One word on a phone, so both choices share a line; its
              // name is the full label everywhere.
              <>
                <span aria-hidden="true" className="sm:hidden">
                  {chamber === "senate" ? "Senate" : "House"}
                </span>
                <span className="sr-only sm:not-sr-only">{label}</span>
              </>
            ) : (
              label
            )}
          </label>
        ))}
      </span>
    </fieldset>
  );

  return (
    <section id="where-they-stand" aria-labelledby="where-heading" className="font-sans">
      <div className="flex flex-wrap items-end justify-between gap-3 border-t-[3px] border-ink-hi pt-2 sm:pt-3">
        <h2 id="where-heading" {...onThisPage("Where they stand")} className={TYPE.section}>
          Where {name} stands
        </h2>
        {toggle("head")}
      </div>

      {error && !data && (
        <p className="mt-3 text-small text-ink-lo">
          The comparison with peers could not be loaded.
        </p>
      )}
      {data && (
        <>
          {/* Only a measure that is genuinely unusual earns the line
              (standsOutKey: the backend's rule); otherwise nothing. */}
          {/* Under the strips on a phone, so the first strip stays on the
              first screen; above them from sm up. */}
          <div className="flex flex-col">
            {/* One column on a phone, two from 40rem, four from 62rem of the
                section's own width, ruled like the record's boxes. */}
            <div className="@container mt-3 -mx-2 overflow-x-clip px-2">
              <div className="grid grid-cols-[minmax(0,1fr)] gap-x-10 border-t-2 border-ink-hi @min-[40rem]:grid-cols-2 @min-[62rem]:grid-cols-4">
                {data.measures.map((m) => (
                  <Cell key={m.key} m={m} memberId={memberId} showTable={showTable} />
                ))}
              </div>
            </div>
            {data.standsOutKey && (
              <p className={`${TYPE.body} mt-3 sm:order-first`}>
                <span className="font-semibold text-signal-orange">Stands out:</span>{" "}
                {data.standsOut}
              </p>
            )}
          </div>
          {/* The key, and what a row means, for a reader new to the strips;
              in words, so it is read aloud too (it was an aria-hidden key). */}
          <div className="mt-3 sm:hidden">{toggle("foot")}</div>
          <HowToRead className="mt-2">
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span>
                <Dot size={6} fill="var(--chart-muted)" className="mr-1 inline-block" />
                one peer
              </span>
              <span>
                <Dot
                  size={12}
                  fill="var(--ink-hi)"
                  ring="var(--phos)"
                  className="mr-1 inline-block align-middle"
                />
                {name}
              </span>
              <span>┆ the average for seats like this one, or the peer median</span>
            </p>
            <p>
              Each strip is one measure, with {name} among the members compared. Further right is a
              larger figure. The word beside the measure (above, near or below peers) comes from
              where {name} falls among them, by a fixed rule; it says how far, not whether that is
              good.
            </p>
          </HowToRead>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              aria-pressed={showTable}
              onClick={() => setShowTable((v) => !v)}
              className="min-h-11 border border-ink-hi bg-surface px-3 text-small font-semibold text-ink-hi hover:bg-white/5"
            >
              {showTable ? "Hide the tables" : "Show as a table"}
            </button>
            <Link
              href={`/compare?leftId=${memberId}&leftChamber=${chamber}`}
              className="inline-flex min-h-11 items-center border border-ink-hi bg-surface px-3 text-small font-semibold text-ink-hi hover:bg-white/5"
            >
              Compare with a peer ▸
            </Link>
          </div>
          <More label="How peers are chosen and what “stands out” means" bare>
            <p className="max-w-prose text-small text-ink-lo">
              {data.standsOutKey ? data.standsOutWhy : `${data.standsOut} ${data.standsOutWhy}`}{" "}
              <Link href="/about/scores#peers" className="text-phos underline underline-offset-2">
                How peers are chosen
              </Link>
            </p>
          </More>
        </>
      )}
    </section>
  );
}
