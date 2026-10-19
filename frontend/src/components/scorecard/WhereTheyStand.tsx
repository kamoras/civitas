"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchPeerComparison } from "@/lib/api";
import type { PeerComparison, PeerGroupKey, PeerMeasure } from "@/types/peers";

/** How each row's figure is printed on its axis and in its table: the
 *  backend's values, formatted only. */
function fmt(m: PeerMeasure, v: number): string {
  if (m.unit === "count") return String(Math.round(v));
  const pct = v * 100;
  return `${m.unit === "rate" && pct > 0 && pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
}

/** The axis's top: the largest figure on the row, up to a round step, so
 *  the strip reads on the measure's own scale, never 0-100 score units. */
function axisMax(m: PeerMeasure): number {
  const top = Math.max(...m.distribution, m.value ?? 0, m.expected ?? 0);
  if (m.unit === "count") return Math.max(5, Math.ceil(top / 5) * 5);
  const step = top <= 0.1 ? 0.02 : 0.1;
  return Math.max(step, Math.ceil(top / step) * step);
}

/** A round mark. Drawn as SVG, not a rounded box: the site's boxes keep
 *  hard corners, and a dot is a data mark, not a box. */
function Dot({
  size,
  fill,
  ring,
  className = "",
  style,
  title,
}: {
  size: number;
  fill: string;
  ring?: string;
  className?: string;
  style?: React.CSSProperties;
  title?: string;
}) {
  const r = size / 2;
  return (
    <svg width={size} height={size} className={className} style={style}>
      {title && <title>{title}</title>}
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

function Strip({ m, name }: { m: PeerMeasure; name: string }) {
  const max = axisMax(m);
  const at = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  const muted = m.thin;
  return (
    <div className="min-w-0">
      {/* The table below is the accessible form of this strip. */}
      <div aria-hidden="true" className="relative h-10">
        <div className="absolute inset-x-0 top-[22px] h-px bg-white/40" />
        {m.distribution.map((v, i) => (
          <Dot
            key={m.distributionIds[i]}
            size={6}
            fill={
              muted ? "color-mix(in srgb, var(--overlay) 25%, transparent)" : "var(--chart-muted)"
            }
            title={`${m.distributionNames[i]}: ${fmt(m, v)}`}
            className="absolute -translate-x-1/2"
            style={{ left: at(v), top: `${12 + JITTER[i % JITTER.length] / 2}px` }}
          />
        ))}
        {m.expected != null && (
          <span
            className="absolute top-1 h-8 -translate-x-1/2 border-l-2 border-dashed border-ink-hi"
            style={{ left: at(m.expected) }}
          />
        )}
        {m.value != null && (
          <Dot
            size={14}
            fill="var(--ink-hi)"
            ring="var(--phos)"
            title={`${name}: ${m.shown}`}
            className="absolute top-[15px] -translate-x-1/2"
            style={{ left: at(m.value) }}
          />
        )}
      </div>
      <div className="relative flex h-5 justify-between text-xs text-ink-min" aria-hidden="true">
        <span>{fmt(m, 0)}</span>
        {m.expected != null && (
          <span
            className="absolute -translate-x-1/2 font-semibold whitespace-nowrap text-ink-lo"
            style={{ left: at(m.expected) }}
          >
            {m.expectedBasis === "seat"
              ? `${fmt(m, m.expected)} expected`
              : `median ${fmt(m, m.expected)}`}
          </span>
        )}
        <span>{fmt(m, max)}</span>
      </div>
    </div>
  );
}

function Row({ m, name, showTable }: { m: PeerMeasure; name: string; showTable: boolean }) {
  const strong = (m.word ?? "").startsWith("well");
  const captionId = useId();
  return (
    <figure
      aria-labelledby={captionId}
      className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-white/25 px-4 py-4 first:border-t-0 sm:px-6 md:grid-cols-[10rem_minmax(0,1fr)_9rem]"
    >
      <div className="flex items-baseline justify-between gap-3 md:block">
        <div>
          <p className="text-[13px] font-semibold uppercase tracking-[0.06em] text-ink-lo">
            {m.label}
          </p>
          <p className="text-[28px] font-semibold leading-tight text-ink-hi sm:text-[32px]">
            {m.shown ?? "No record"}
          </p>
        </div>
        <p
          className={`text-right text-[13px] font-semibold uppercase tracking-[0.06em] md:hidden ${strong ? "text-signal-orange" : "text-ink-lo"}`}
        >
          {m.thin ? "" : m.word}
        </p>
      </div>
      <div className="min-w-0">
        <p className="mb-1 text-sm text-ink-lo">{m.direction}</p>
        <Strip m={m} name={name} />
      </div>
      <p
        className={`hidden self-center text-[13px] font-semibold uppercase tracking-[0.06em] md:block ${strong ? "text-signal-orange" : "text-ink-lo"}`}
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
      <div className={showTable ? "md:col-span-3" : "sr-only"}>
        <table className="w-full max-w-md text-sm">
          <caption className="text-left text-xs text-ink-min">
            {m.label}: every {m.peerGroup.label.toLowerCase()} figure, lowest first
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
            {m.distribution.map((v, i) => (
              <tr key={m.distributionIds[i]} className="border-t border-white/10">
                <td className="py-1">
                  <Link
                    href={`/politicians/${m.distributionIds[i]}`}
                    className="underline-offset-2 hover:underline"
                  >
                    {m.distributionNames[i]}
                  </Link>
                </td>
                <td className="py-1 text-right">{fmt(m, v)}</td>
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
          className="font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]"
        >
          Where {name} stands
        </h2>
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="sr-only">Compare with</legend>
          <span aria-hidden="true" className="text-sm text-ink-lo">
            Compared with:
          </span>
          <span className="inline-flex">
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
            <p className="text-[13px] font-semibold uppercase tracking-[0.06em] text-signal-orange">
              Stands out
            </p>
            <p className="font-display text-lg font-semibold leading-snug text-ink-hi">
              {data.standsOut}
            </p>
          </div>
          <div className="mt-3 border border-white/25 bg-surface">
            {data.measures.map((m) => (
              <Row key={m.key} m={m} name={name} showTable={showTable} />
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
            <span>┆ what the seat predicts, or the peer median</span>
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
