"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchPeerComparison } from "@/lib/api";
import ChartReadout from "@/components/shared/ChartReadout";
import type { PeerComparison, PeerGroupKey, PeerMeasure } from "@/types/peers";
import { onThisPage } from "@/lib/onThisPage";
import StripPoints, { BigFigure, StripAxis, type StripPoint } from "@/components/shared/Strip";

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
  const at = (v: number) => Math.min(100, (v / m.axisMax) * 100);
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
    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-lo md:col-span-3">
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

function Row({ m, memberId, showTable }: { m: PeerMeasure; memberId: string; showTable: boolean }) {
  const strong = (m.word ?? "").startsWith("well");
  const captionId = useId();
  return (
    <figure
      aria-labelledby={captionId}
      className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-white/25 px-4 py-4 [h3+&]:border-t-0 sm:px-6 md:grid-cols-[10rem_minmax(0,1fr)_9rem]"
    >
      <div className="flex items-baseline justify-between gap-3 md:block">
        <div>
          <div className="text-[13px] font-semibold text-ink-lo">{m.label}</div>
          <BigFigure
            shown={m.shown}
            className="text-[28px] font-semibold leading-tight text-ink-hi sm:text-[32px]"
          />
        </div>
        <p
          className={`text-right text-[13px] font-semibold md:hidden ${strong ? "text-signal-orange" : "text-ink-lo"}`}
        >
          {m.thin ? "" : m.word}
        </p>
      </div>
      <div className="min-w-0">
        <p className="mb-1 text-sm text-ink">{m.why}</p>
        <Strip m={m} memberId={memberId} />
      </div>
      <p
        className={`hidden self-center text-[13px] font-semibold md:block ${strong ? "text-signal-orange" : "text-ink-lo"}`}
      >
        {m.thin ? "" : m.word}
      </p>
      <figcaption id={captionId} className="text-sm text-ink md:col-span-3">
        <span className="sr-only">{m.label}: </span>
        {m.thin
          ? `Not enough record yet to compare (${m.record != null && m.key === "breaks" ? `${m.record} party-line votes` : `${m.peerGroup.n} with a figure`}).`
          : m.sentence}
        {!m.thin && m.word && <span className="sr-only"> {m.word}.</span>}{" "}
        <a
          href={m.source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-phos underline underline-offset-2"
        >
          {m.source.name} ↗
        </a>
      </figcaption>
      <OverTime m={m} />
      <details className="group text-sm text-ink-lo md:col-span-3">
        <summary className="inline-flex min-h-6 cursor-pointer items-center gap-1 font-semibold text-ink-hi underline-offset-2 hover:underline">
          How this is measured
          <span aria-hidden="true" className="inline-block group-open:rotate-90">
            ▸
          </span>
        </summary>
        <p className="mt-1 max-w-prose">
          {m.measured}{" "}
          <Link href="/about/scores#peers" className="text-phos underline underline-offset-2">
            Methods and sources
          </Link>
        </p>
      </details>
      <div className={showTable ? "md:col-span-3" : "sr-only"}>
        <table className="w-full max-w-md text-sm">
          <caption className="text-left text-xs text-ink-min">
            {m.label}:{" "}
            {m.peerGroup.labelOne
              ? `every ${m.peerGroup.labelOne}'s figure`
              : `every figure among the ${m.peerGroup.label}`}
            , lowest first
          </caption>
          <thead>
            <tr className="text-left text-xs text-ink-min">
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
  "inline-flex min-h-11 cursor-pointer items-center border border-ink-hi px-3 text-sm font-semibold has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2";

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

  return (
    <section id="where-they-stand" aria-labelledby="where-heading" className="font-sans">
      <div className="flex flex-wrap items-end justify-between gap-3 border-t-[3px] border-ink-hi pt-3">
        <h2
          id="where-heading"
          {...onThisPage("Where they stand")}
          className="font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]"
        >
          Where {name} stands
        </h2>
        {/* min-w-0: a fieldset is min-content wide by default, and at 200% text
            the two segments side by side were wider than a phone. */}
        <fieldset className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
          <legend className="sr-only">Compare with</legend>
          <span aria-hidden="true" className="text-sm text-ink-lo">
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
                  name={`peers-${memberId}`}
                  value={key}
                  checked={group === key}
                  onChange={() => choose(key)}
                  className="sr-only"
                />
                {label}
              </label>
            ))}
          </span>
        </fieldset>
      </div>

      {error && !data && (
        <p className="mt-3 text-sm text-ink-lo">The comparison with peers could not be loaded.</p>
      )}
      {data && (
        <>
          <div className="mt-4 border border-white/25 border-l-4 border-l-signal-orange bg-surface px-4 py-3 sm:px-6">
            <p className="text-[13px] font-semibold text-signal-orange">Stands out</p>
            <p className="font-display text-lg font-semibold leading-snug text-ink-hi">
              {data.standsOut}
            </p>
            <p className="mt-1 text-sm text-ink-lo">{data.standsOutWhy}</p>
          </div>
          <div className="mt-3 border border-white/25 bg-surface">
            {/* Rows that answer the same question sit under it, once. */}
            {data.measures.map((m, i) => (
              <div key={m.key}>
                {m.question !== data.measures[i - 1]?.question && (
                  <h3
                    className={`px-4 pt-4 font-display text-lg font-semibold text-ink-hi sm:px-6 ${i ? "border-t-2 border-ink-hi/60" : ""}`}
                  >
                    {m.question}
                  </h3>
                )}
                <Row m={m} memberId={memberId} showTable={showTable} />
              </div>
            ))}
          </div>
          <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo" aria-hidden="true">
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
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              aria-pressed={showTable}
              onClick={() => setShowTable((v) => !v)}
              className="min-h-11 border border-ink-hi bg-surface px-3 text-sm font-semibold text-ink-hi hover:bg-white/5"
            >
              {showTable ? "Hide the tables" : "Show as a table"}
            </button>
            <Link
              href={`/compare?leftId=${memberId}&leftChamber=${chamber}`}
              className="inline-flex min-h-11 items-center border border-ink-hi bg-surface px-3 text-sm font-semibold text-ink-hi hover:bg-white/5"
            >
              Compare with a peer ▸
            </Link>
            <Link
              href="/about/scores#peers"
              className="inline-flex min-h-11 items-center border border-ink-hi bg-surface px-3 text-sm font-semibold text-ink-hi hover:bg-white/5"
            >
              How peers are chosen ▸
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
