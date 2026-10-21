"use client";

import { useId, useState } from "react";
import Link from "next/link";
import type { PresidentPeerMeasure, PresidentPeers } from "@/types/presidentPeers";
import { onThisPage } from "@/lib/onThisPage";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { Dot } from "./WhereTheyStand";

const JITTER = [10, 4, 16, 7, 13, 1, 19, 10];

/** One figure among the other presidencies on its own scale: a dot per
 *  presidency, this one ringed, the expected figure as a dashed tick. The
 *  axis spans the figures on the row; every figure is the backend's. */
function Strip({ m, name }: { m: PresidentPeerMeasure; name: string }) {
  const all = [...m.distribution, m.value, ...(m.expected == null ? [] : [m.expected])];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const pad = (hi - lo || 1) * 0.06;
  const at = (v: number) => `${((v - lo + pad) / (hi - lo + 2 * pad)) * 100}%`;
  // End labels: the lowest and highest figures on the row, as the API printed them.
  const shown = [...m.distributionShown, m.shown];
  const values = [...m.distribution, m.value];
  const ends = [
    shown[values.indexOf(Math.min(...values))],
    shown[values.indexOf(Math.max(...values))],
  ];
  return (
    <div className="min-w-0">
      {/* The table below is the accessible form of this strip. */}
      <div aria-hidden="true" className="relative h-10">
        <div className="absolute inset-x-0 top-[22px] h-px bg-white/40" />
        {m.distribution.map((v, i) => (
          <Dot
            key={m.distributionIds[i]}
            size={6}
            fill="var(--chart-muted)"
            title={`${m.distributionNames[i]}: ${m.distributionShown[i]}`}
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
        <Dot
          size={14}
          fill="var(--ink-hi)"
          ring="var(--phos)"
          title={`${name}: ${m.shown}`}
          className="absolute top-[15px] -translate-x-1/2"
          style={{ left: at(m.value) }}
        />
      </div>
      <div
        className="flex flex-wrap justify-between gap-x-3 text-xs text-ink-min"
        aria-hidden="true"
      >
        <span>{ends[0]}</span>
        {m.expectedShown && <span className="font-semibold text-ink-lo">{m.expectedShown}</span>}
        <span>{ends[1]}</span>
      </div>
    </div>
  );
}

function Row({
  m,
  name,
  showTable,
}: {
  m: PresidentPeerMeasure;
  name: string;
  showTable: boolean;
}) {
  const strong = (m.word ?? "").startsWith("well");
  const captionId = useId();
  const word = m.thin ? null : m.word;
  return (
    <figure
      aria-labelledby={captionId}
      className="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-white/25 px-4 py-4 [h3+&]:border-t-0 sm:px-6 md:grid-cols-[11rem_minmax(0,1fr)_9rem]"
    >
      <div className="flex items-baseline justify-between gap-3 md:block">
        <div>
          <p className="text-[13px] font-semibold text-ink-lo">{m.label}</p>
          <p className="text-2xl font-semibold leading-tight text-ink-hi sm:text-[28px]">
            {m.shown}
          </p>
        </div>
        {word && (
          <p
            className={`text-right text-[13px] font-semibold md:hidden ${strong ? "text-signal-orange" : "text-ink-lo"}`}
          >
            {word}
          </p>
        )}
      </div>
      <div className="min-w-0">
        <p className="mb-1 text-sm text-ink">{m.why}</p>
        <Strip m={m} name={name} />
      </div>
      <p
        className={`hidden self-center text-[13px] font-semibold md:block ${strong ? "text-signal-orange" : "text-ink-lo"}`}
      >
        {word}
      </p>
      <figcaption id={captionId} className="text-sm text-ink md:col-span-3">
        <span className="sr-only">{m.label}: </span>
        {m.sentence} Compared with {m.peerGroup.n} {m.peerGroup.label}.
        {word && <span className="sr-only"> {word}.</span>}{" "}
        <a
          href={m.source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-phos underline underline-offset-2"
        >
          {m.source.name} ↗
        </a>
      </figcaption>
      <details
        className="group text-sm text-ink-lo md:col-span-3"
        {...{ [SHARE_EXCLUDE_ATTR]: "" }}
      >
        <summary className="inline-flex min-h-6 cursor-pointer items-center gap-1 font-semibold text-ink-hi underline-offset-2 hover:underline">
          How this is measured
          <span aria-hidden="true" className="inline-block group-open:rotate-90">
            ▸
          </span>
        </summary>
        <p className="mt-1 max-w-prose">{m.measured}</p>
      </details>
      <div className={showTable ? "md:col-span-3" : "sr-only"}>
        <table className="w-full max-w-md text-sm">
          <caption className="text-left text-xs text-ink-min">
            {m.label}: the other {m.peerGroup.label}, lowest first
          </caption>
          <thead>
            <tr className="text-left text-xs text-ink-min">
              <th scope="col" className="py-1 font-semibold">
                President
              </th>
              <th scope="col" className="py-1 text-right font-semibold">
                {m.label}
              </th>
            </tr>
          </thead>
          <tbody>
            {m.distribution.map((_, i) => (
              <tr key={m.distributionIds[i]} className="border-t border-white/10">
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

/**
 * "Where they stand" for a president: each figure the presidency has among
 * the other presidencies with the same figure, the way a member's page
 * shows peers. Every figure, word and sentence comes from the API (backend
 * app/president_peers.py); this only places them.
 */
export default function PresidentStanding({
  name,
  peers,
}: {
  name: string;
  peers: PresidentPeers;
}) {
  const [showTable, setShowTable] = useState(false);
  const { measures } = peers;
  const [lo, hi] = peers.rule.outside;
  return (
    <section
      id="where-they-stand"
      aria-labelledby="president-standing-heading"
      {...{ [SHARE_SECTION_ATTR]: "where-they-stand" }}
      className="font-sans"
    >
      <div className="flex flex-wrap items-end justify-between gap-3 border-t-[3px] border-ink-hi pt-3">
        <h2
          id="president-standing-heading"
          {...onThisPage("Among presidents")}
          className="font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]"
        >
          Where {name} stands among presidents
        </h2>
        <ShareSectionButton label="Among presidents" />
      </div>
      {measures.length > 0 && (
        <div className="mt-4 border border-white/25 border-l-4 border-l-signal-orange bg-surface px-4 py-3 sm:px-6">
          <p className="text-[13px] font-semibold text-signal-orange">Stands out</p>
          <p className="font-display text-lg font-semibold leading-snug text-ink-hi">
            {peers.standsOut}
          </p>
          <p className="mt-1 text-sm text-ink-lo">{peers.standsOutWhy}</p>
        </div>
      )}
      {measures.length > 0 && (
        <div className="mt-3 border border-white/25 bg-surface">
          {measures.map((m, i) => (
            <div key={m.key}>
              {m.question !== measures[i - 1]?.question && (
                <h3
                  className={`px-4 pt-4 font-display text-lg font-semibold text-ink-hi sm:px-6 ${i ? "border-t-2 border-ink-hi/60" : ""}`}
                >
                  {m.question}
                </h3>
              )}
              <Row m={m} name={name} showTable={showTable} />
            </div>
          ))}
        </div>
      )}
      {peers.missing.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 text-sm text-ink-lo">
          {peers.missing.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {measures.length > 0 && (
        <>
          <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo" aria-hidden="true">
            <span>
              <Dot size={6} fill="var(--chart-muted)" className="mr-1 inline-block" />
              one presidency
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
            <span>┆ the median, or what presidents starting from the same level saw</span>
          </p>
          <p className="mt-2 max-w-prose text-xs text-ink-lo">
            Each figure is compared with every other presidency that has it, within the era its
            series covers. The word is a fixed rule: outside the middle half ({lo}th to {hi}th
            percentile) reads &ldquo;above&rdquo; or &ldquo;below&rdquo;, and only with at least{" "}
            {peers.rule.minPeers} other presidencies. It says where a figure sits, not that it was
            the president&apos;s doing.
          </p>
          <div className="mt-3 flex flex-wrap gap-2" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
            <button
              type="button"
              aria-pressed={showTable}
              onClick={() => setShowTable((v) => !v)}
              className="min-h-11 border border-ink-hi bg-surface px-3 text-sm font-semibold text-ink-hi hover:bg-white/5"
            >
              {showTable ? "Hide the tables" : "Show as a table"}
            </button>
            <Link
              href="/about/presidents-and-justices#presidents"
              className="inline-flex min-h-11 items-center border border-ink-hi bg-surface px-3 text-sm font-semibold text-ink-hi hover:bg-white/5"
            >
              How presidents are compared ▸
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
