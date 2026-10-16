"use client";

import { congressOrdinal } from "@/lib/sources";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type {
  DistrictShare,
  LeanCategory,
  NewDistrict,
  OldDistrict,
  StateRedistricting,
} from "@/types/redistricting";
import {
  districtAreaLabel,
  formatComputedLean,
  formatPvi,
  leanWindowWords,
  matchesDistrictQuery,
  placeDistrictSummary,
  pviColor,
  stateBallotHref,
} from "@/lib/elections";
import { safeHref } from "@/lib/formatting";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { redistrictingDownloads } from "@/lib/api";
import {
  REDISTRICTING_HREF,
  districtOrdinal,
  netWord,
  newDistrictSentence,
  oldDistrictSentence,
  redistrictingHref,
} from "@/lib/redistricting";
import { SERIES } from "@/components/admin/charts/palette";

/**
 * "Is my district different this year, and how much?" for one state that
 * redrew its U.S. House map.
 *
 * Two readers, one page. The top answers the casual question in a
 * sentence per district, findable by a town or county the reader already
 * knows (AGENTS.md §8: nothing is asked or sent). The bottom is the
 * researcher's: the whole overlap matrix in 2020 Census residents, every
 * source with its revision, the method, and the CSV and JSON.
 *
 * Every number is the backend's (app/redistricting.py); this only words
 * and draws it. Descriptive throughout: who is in which district, and the
 * lean Cook published — never why a line was drawn.
 */

const LEAN_BG: Record<LeanCategory, string> = {
  R: "bg-rep-red",
  D: "bg-dem-blue",
  EVEN: "bg-ink",
};
const LEAN_WORD: Record<LeanCategory, string> = {
  R: "Republican-leaning",
  D: "Democratic-leaning",
  EVEN: "even",
};

/** The headline seat figures: the same counts the strips draw. */
const SEAT_TILES: [LeanCategory, string][] = [
  ["R", "R-LEANING SEATS"],
  ["D", "D-LEANING SEATS"],
  ["EVEN", "EVEN SEATS"],
];

/** One block per seat, coloured by its lean: the state's whole delegation
 * before and after at a glance. The counts are in the text beside it, so
 * colour never carries the meaning alone. */
export function SeatStrip({
  label,
  counts,
}: {
  label: string;
  counts: Record<LeanCategory, number>;
}) {
  const order: LeanCategory[] = ["D", "EVEN", "R"];
  const words = order
    .filter((k) => counts[k] > 0)
    .map((k) => `${counts[k]} ${LEAN_WORD[k]}`)
    .join(", ");
  return (
    <div className="grid grid-cols-[52px_1fr] items-baseline gap-2">
      <span className="font-mono text-[11px] tracking-[0.14em] text-ink-min">{label}</span>
      <span className="min-w-0">
        <span aria-hidden="true" className="flex flex-wrap gap-[2px]">
          {order.flatMap((k) =>
            Array.from({ length: counts[k] }, (_, i) => (
              <span key={`${k}${i}`} className={`h-3.5 w-2 ${LEAN_BG[k]}`} />
            ))
          )}
        </span>
        <span className="mt-1 block text-xs text-ink-lo">{words}</span>
      </span>
    </div>
  );
}

const BAR_BLOCKS = 20;

/** Blocks per share on a BAR_BLOCKS-wide bar: at least one each, never
 * past the end. Drawing only; the shares are the backend's. */
function blockCounts(shares: number[]): number[] {
  const out: number[] = [];
  let used = 0;
  for (const pct of shares) {
    const n = Math.min(BAR_BLOCKS - used, Math.max(1, Math.round((pct * BAR_BLOCKS) / 100)));
    out.push(n);
    used += n;
  }
  return out;
}

/** Where a district's residents came from (or went), as a 20-block bar:
 * one colour per district, largest first, in the admin chart palette.
 * Anything past five districts, and the slivers, are the faint blocks. */
function ShareBar({ rows, prefix }: { rows: DistrictShare[]; prefix: "old" | "new" }) {
  const shown = rows.slice(0, SERIES.length);
  const blocks = blockCounts(shown.map((r) => r.sharePct));
  const used = blocks.reduce((a, b) => a + b, 0);
  return (
    <div className="mt-1.5">
      <span aria-hidden="true" className="flex gap-[2px]">
        {shown.flatMap((r, i) =>
          Array.from({ length: blocks[i] }, (_, j) => (
            <span
              key={`${r.district}${j}`}
              className="h-3 w-2.5"
              style={{ background: SERIES[i] }}
            />
          ))
        )}
        {Array.from({ length: Math.max(0, BAR_BLOCKS - used) }, (_, j) => (
          <span key={`rest${j}`} className="h-3 w-2.5 border border-white/[0.09]" />
        ))}
      </span>
      <span className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-ink-lo">
        {shown.map((r, i) => (
          <span key={r.district}>
            <span aria-hidden="true" style={{ color: SERIES[i] }}>
              ■{" "}
            </span>
            {prefix} {districtOrdinal(r.number)} {r.sharePct}%
          </span>
        ))}
        {rows.length > shown.length && <span>+{rows.length - shown.length} more</span>}
      </span>
    </div>
  );
}

function Num({ n }: { n: number }) {
  return (
    <span className="self-start border border-white/15 py-0.5 text-center font-mono text-sm text-ink-hi">
      {n === 0 ? "AL" : n}
    </span>
  );
}

function NewRow({ d, filter }: { d: NewDistrict; filter: string }) {
  const areas = districtAreaLabel([...d.counties, ...d.places], 3, true, filter);
  return (
    <li
      id={`new-${d.number}`}
      className="grid scroll-mt-24 grid-cols-[34px_1fr] gap-3 border-t border-white/[0.07] py-3"
    >
      <Num n={d.number} />
      <div className="min-w-0">
        {areas && <p className="truncate text-[11px] text-ink-min">{areas}</p>}
        <p className="text-sm leading-relaxed text-ink">{newDistrictSentence(d)}</p>
        {!d.unchanged && <ShareBar rows={d.sources} prefix="old" />}
        <p className="mt-1 font-mono text-[11px] text-ink-min">
          LEAN <span className={pviColor(d.pviBefore)}>{formatPvi(d.pviBefore)}</span> →{" "}
          <span className={pviColor(d.pviAfter)}>{formatPvi(d.pviAfter)}</span>
          {d.pviShift != null && d.pviShift !== 0 && (
            <span>
              {" "}
              · {Math.abs(d.pviShift)} {Math.abs(d.pviShift) === 1 ? "POINT" : "POINTS"}{" "}
              {d.pviShift > 0 ? "TOWARD R" : "TOWARD D"}
            </span>
          )}
          {d.pviBeforeWeighted != null && !d.unchanged && (
            <span> · RESIDENTS&apos; OLD DISTRICTS AVERAGED {formatPvi(d.pviBeforeWeighted)}</span>
          )}
        </p>
        {(d.computedLeanBefore != null || d.computedLeanAfter != null) && (
          <p className="font-mono text-[11px] text-ink-min">
            CIVITAS-COMPUTED {formatComputedLean(d.computedLeanBefore)} →{" "}
            {formatComputedLean(d.computedLeanAfter)}
          </p>
        )}
      </div>
    </li>
  );
}

function OldRow({ d, filter }: { d: OldDistrict; filter: string }) {
  const areas = districtAreaLabel([...d.counties, ...d.places], 3, true, filter);
  return (
    <li
      id={`old-${d.number}`}
      className="grid scroll-mt-24 grid-cols-[34px_1fr] gap-3 border-t border-white/[0.07] py-3"
    >
      <Num n={d.number} />
      <div className="min-w-0">
        {areas && <p className="truncate text-[11px] text-ink-min">{areas}</p>}
        <p className="text-sm leading-relaxed text-ink">{oldDistrictSentence(d)}</p>
        <ShareBar rows={d.destinations} prefix="new" />
        <p className="mt-1 font-mono text-[11px] text-ink-min">
          OLD LEAN <span className={pviColor(d.pvi)}>{formatPvi(d.pvi)}</span>
          {d.computedLean != null && (
            <span> · CIVITAS-COMPUTED {formatComputedLean(d.computedLean)}</span>
          )}
        </p>
      </div>
    </li>
  );
}

/** Old districts down, new across, 2020 residents in each cell. Built
 * only when opened: California's is 52 × 52. */
function OverlapMatrix({ data }: { data: StateRedistricting }) {
  const cell = new Map(data.cells.map(([o, n, p]) => [`${o}|${n}`, p]));
  const max = Math.max(...data.cells.map((c) => c[2]));
  return (
    <div
      className="max-w-full overflow-x-auto border border-white/[0.09]"
      tabIndex={0}
      aria-label="Overlap matrix, scrollable"
    >
      <table className="border-collapse font-mono text-[10px] text-ink-lo">
        <caption className="sr-only">
          Census residents in each pair of an old ({congressOrdinal(data.fromCongress)} Congress)
          and a new ({congressOrdinal(data.toCongress)} Congress) district. Rows are old districts,
          columns new ones.
        </caption>
        <thead>
          <tr>
            <th
              scope="col"
              className="sticky left-0 bg-surface-base px-1.5 py-1 text-left text-ink-min"
            >
              OLD ↓ NEW →
            </th>
            {data.newDistricts.map((n) => (
              <th key={n.district} scope="col" className="px-1.5 py-1 text-right text-ink-hi">
                {n.number === 0 ? "AL" : n.number}
              </th>
            ))}
            <th scope="col" className="px-1.5 py-1 text-right text-ink-min">
              TOTAL
            </th>
          </tr>
        </thead>
        <tbody>
          {data.oldDistricts.map((o) => (
            <tr key={o.district} className="border-t border-white/[0.07]">
              <th
                scope="row"
                className="sticky left-0 bg-surface-base px-1.5 py-1 text-left text-ink-hi"
              >
                {o.number === 0 ? "AL" : o.number}
              </th>
              {data.newDistricts.map((n) => {
                const p = cell.get(`${o.district}|${n.district}`);
                return (
                  <td key={n.district} className="relative px-1.5 py-1 text-right tabular-nums">
                    {p ? (
                      <>
                        <span
                          aria-hidden="true"
                          className="absolute inset-0 bg-signal-cyan"
                          style={{ opacity: 0.08 + 0.4 * (p / max) }}
                        />
                        <span className="relative text-ink-hi">{p.toLocaleString("en-US")}</span>
                      </>
                    ) : (
                      <span className="text-ink-min/40">·</span>
                    )}
                  </td>
                );
              })}
              <td className="px-1.5 py-1 text-right tabular-nums text-ink-min">
                {o.population.toLocaleString("en-US")}
              </td>
            </tr>
          ))}
          <tr className="border-t border-white/[0.09]">
            <th
              scope="row"
              className="sticky left-0 bg-surface-base px-1.5 py-1 text-left text-ink-min"
            >
              TOTAL
            </th>
            {data.newDistricts.map((n) => (
              <td key={n.district} className="px-1.5 py-1 text-right tabular-nums text-ink-min">
                {n.population.toLocaleString("en-US")}
              </td>
            ))}
            <td className="px-1.5 py-1 text-right tabular-nums text-ink-min">
              {data.summary.population.toLocaleString("en-US")}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function PviSourceLine({
  label,
  meta,
}: {
  label: string;
  meta: StateRedistricting["sources"]["pviBefore"];
}) {
  if (!meta) return <li>{label}: no pinned table on file.</li>;
  const rev = meta.revision;
  const url = rev
    ? `https://en.wikipedia.org/w/index.php?title=${encodeURIComponent(rev.page.replace(/ /g, "_"))}&oldid=${rev.revid}`
    : null;
  return (
    <li>
      <span className="text-ink-hi">{label}:</span> {meta.source}
      {rev && url && (
        <>
          {" "}
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos"
          >
            [ REVISION {rev.revid} ↗ ]
          </a>
        </>
      )}
      {meta.window && <span className="text-ink-min"> Window: {meta.window}.</span>}
    </li>
  );
}

function MethodAndData({ data }: { data: StateRedistricting }) {
  const [open, setOpen] = useState(false);
  const downloads = redistrictingDownloads(data.state);
  const actUrl = safeHref(data.map?.url ?? null);
  const leanYears = leanWindowWords(
    data.sources.pviAfter?.window ?? data.sources.pviBefore?.window
  );
  return (
    <section
      id="method-and-data"
      aria-labelledby="method"
      className="mt-10 scroll-mt-24 border-t-3 border-ink-min/60 pt-5"
    >
      <h2 id="method" className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        Method &amp; data
      </h2>
      <div className="mt-3 max-w-3xl space-y-3 text-[13px] leading-relaxed text-ink-lo">
        <p>
          Every block of the Census the maps were drawn to is assigned to one district on each map
          by the Census Bureau&apos;s block equivalency files. Each block&apos;s Census population
          is counted in the pair of districts (old, new) it falls in, so every resident is counted
          exactly once and each state&apos;s cells add up to its Census population (
          {data.summary.population.toLocaleString("en-US")}). The sentences above leave out overlaps
          under {data.minResidents} residents: a block the line cuts through is assigned whole to
          one side, so an overlap that small can be the block file&apos;s rounding rather than
          people who moved. The matrix and the downloads keep every overlap.
        </p>
        <p>
          A new district&apos;s <span className="text-ink-hi">predecessor</span> is the old district
          most of its residents came from; its &quot;before&quot; lean is the predecessor&apos;s.
          The weighted figure averages the leans of every old district its residents came from, by
          residents. The average resident&apos;s shift is how far each resident&apos;s district lean
          moved, either way, averaged over residents. Both leans are Cook PVI on the same
          presidential elections{leanYears ? ` (${leanYears})` : ""}, so the change is the lines,
          not new elections.
        </p>
        <p>
          <span className="text-ink-hi">Seats by lean</span> (the figures at the top) count each
          map&apos;s districts by the sign of their lean: R, D or EVEN. A different count:{" "}
          <span className="text-ink-hi">
            {data.summary.districtsLeaningDifferentlyFromPredecessor} of {data.summary.seats} new
            districts lean differently from their predecessor
          </span>
          . The two need not match: a new district built from several old ones is compared with its
          largest source only, an old district split up with no successor (
          {data.summary.oldWithoutSuccessor} here) is compared with nothing, and an EVEN seat is a
          category of its own. Average district lean: {formatPvi(data.summary.meanPviBefore)}{" "}
          before, {formatPvi(data.summary.meanPviAfter)} after; a redraw moves residents between
          districts, not out of the state, so the average barely moves while single seats can move a
          lot.
        </p>
        <p>
          Population is the decennial Census the maps were drawn to; it does not track moves since.
          Lean is how a district voted for president relative to the nation, not a forecast.
        </p>
        <ul className="list-none space-y-1.5 border-l border-white/[0.09] pl-3">
          <li>
            <span className="text-ink-hi">The new map:</span> {data.map?.title ?? data.map?.reason}
            {data.map?.issuedBy && <> ({data.map.issuedBy})</>}
            {actUrl && (
              <>
                {" "}
                <a
                  href={actUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="whitespace-nowrap text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos"
                >
                  [ OFFICIAL SOURCE ↗ ]
                </a>
              </>
            )}
          </li>
          <li>
            <span className="text-ink-hi">Residents:</span> {data.sources.overlap}
          </li>
          <PviSourceLine
            label={`Old lean (${congressOrdinal(data.fromCongress)} Congress lines)`}
            meta={data.sources.pviBefore}
          />
          <PviSourceLine
            label={`New lean (${congressOrdinal(data.toCongress)} Congress lines)`}
            meta={data.sources.pviAfter}
          />
          {data.sources.computedLean && (
            <li>
              <span className="text-ink-hi">Civitas-computed lean (both maps):</span>{" "}
              {data.sources.computedLean.source} Shown beside Cook&apos;s figures; the counts above
              are Cook&apos;s.{" "}
              <Link
                href={data.sources.computedLean.href}
                className="text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos"
              >
                How it is computed
              </Link>
            </li>
          )}
        </ul>
        <p className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs tracking-[0.1em]">
          <a
            href={downloads.csv}
            download
            className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
          >
            [ DOWNLOAD CSV ]
          </a>
          <a
            href={downloads.json}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
          >
            [ JSON ↗ ]
          </a>
        </p>
      </div>
      <details className="mt-4" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
        <summary className="min-h-[44px] cursor-pointer py-2 font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos">
          [ OVERLAP MATRIX · {data.cells.length} CELLS ]
        </summary>
        {open && <OverlapMatrix data={data} />}
      </details>
    </section>
  );
}

export default function RedistrictingStateView({ data }: { data: StateRedistricting }) {
  const [view, setView] = useState<"new" | "old">("new");
  const [filter, setFilter] = useState("");
  const s = data.summary;

  // A link to an old district (a member profile's note) opens that view.
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash.startsWith("#old-")) return;
    const frame = requestAnimationFrame(() => {
      setView("old");
      requestAnimationFrame(() => document.getElementById(hash.slice(1))?.scrollIntoView());
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  const rows = view === "new" ? data.newDistricts : data.oldDistricts;
  const shown = useMemo(
    () =>
      rows.filter((r) =>
        matchesDistrictQuery(
          { district: r.number, areas: [...r.counties, ...r.places], candidates: [] },
          filter
        )
      ),
    [rows, filter]
  );
  const placeSummary = placeDistrictSummary(
    rows.map((r) => ({ district: r.number, places: r.places })),
    filter
  ).map((line) => (view === "old" ? line.replace(/\bdistricts?\b/, (m) => `old ${m}`) : line));
  const actUrl = safeHref(data.map?.url ?? null);

  return (
    <>
      <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-ink-lo">
        {data.stateName} votes on a new U.S. House map in {data.cycle}. {s.residentsMovedPct}% of
        residents now live in a district made up mostly of people from another old district.
        Unchanged districts: {s.unchangedSeats} of {s.seats}.
      </p>
      {data.map && (
        <p className="mt-2 max-w-2xl font-mono text-[11px] leading-relaxed tracking-[0.06em] text-ink-min">
          THE MAP: {data.map.title ?? data.map.reason}
          {data.map.issuedBy && <> · {data.map.issuedBy}</>}
        </p>
      )}
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs tracking-[0.1em]">
        {actUrl && (
          <a
            href={actUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="min-h-[24px] text-signal-cyan hover:text-phos"
          >
            [ OFFICIAL SOURCE ↗ ]
          </a>
        )}
        <a href="#method-and-data" className="min-h-[24px] text-signal-cyan hover:text-phos">
          [ METHOD &amp; DATA ↓ ]
        </a>
      </p>

      <ShareSubjectProvider
        subject={{
          title: `${data.stateName}'s new House map`,
          subtitle: `${data.cycle} ballot · ${congressOrdinal(data.fromCongress)} Congress lines to ${congressOrdinal(data.toCongress)}`,
          url: absoluteUrl(redistrictingHref(data.state)),
          source:
            "U.S. Census Bureau blocks and block equivalency files; Cook PVI (pinned Wikipedia revisions)",
          // The new lines' Cook table, as of its pinned revision.
          asOf: data.sources.pviAfter?.revision?.timestamp ?? null,
        }}
      >
        <section
          aria-labelledby="at-a-glance"
          className="mt-6 border border-white/[0.09] bg-surface-base p-4"
          {...{ [SHARE_SECTION_ATTR]: "at-a-glance" }}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2
              id="at-a-glance"
              className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
            >
              The delegation, by lean
            </h2>
            <ShareSectionButton label="The delegation, by lean" />
          </div>
          <div className="mt-3 space-y-3">
            <SeatStrip label="BEFORE" counts={s.seatsByLeanBefore} />
            <SeatStrip label="AFTER" counts={s.seatsByLeanAfter} />
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-white/[0.07] pt-3 sm:grid-cols-4">
            {SEAT_TILES.filter(
              ([k]) => k !== "EVEN" || s.seatsByLeanBefore.EVEN > 0 || s.seatsByLeanAfter.EVEN > 0
            ).map(([k, label]) => (
              <div key={k}>
                <dt className="font-mono text-[11px] tracking-[0.1em] text-ink-min">{label}</dt>
                <dd className="font-mono text-lg text-ink-hi">
                  {s.seatsByLeanBefore[k]} → {s.seatsByLeanAfter[k]}
                  <span className="text-xs text-ink-min"> {netWord(s.netSeatChangeByLean[k])}</span>
                </dd>
              </div>
            ))}
            <div>
              <dt className="font-mono text-[11px] tracking-[0.1em] text-ink-min">
                AVG. RESIDENT&apos;S SHIFT
              </dt>
              <dd className="font-mono text-lg text-ink-hi">
                {s.meanResidentShift ?? "N/A"}
                <span className="text-xs text-ink-min"> PTS</span>
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[11px] tracking-[0.1em] text-ink-min">UNCHANGED</dt>
              <dd className="font-mono text-lg text-ink-hi">
                {s.unchangedSeats}
                <span className="text-xs text-ink-min"> OF {s.seats}</span>
              </dd>
            </div>
          </dl>
        </section>
      </ShareSubjectProvider>

      <section aria-labelledby="districts" className="mt-8">
        <h2 id="districts" className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
          Find your district
        </h2>
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Which map to list">
          {(
            [
              ["new", "NEW DISTRICTS"],
              ["old", "OLD DISTRICTS"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={view === key}
              onClick={() => setView(key)}
              className={`min-h-[44px] border px-3 font-mono text-xs tracking-[0.1em] ${
                view === key
                  ? "border-signal-cyan text-signal-cyan"
                  : "border-white/[0.09] text-ink-lo hover:text-ink-hi"
              }`}
            >
              [ {label} ]
            </button>
          ))}
        </div>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by city, town, county or district number"
          aria-label={`Filter ${view} districts by city, town, county or district number`}
          className="mt-3 w-full min-w-0 border border-white/15 bg-surface-base px-3 py-2 font-mono text-xs text-ink-hi placeholder:text-ink-min"
        />
        <p className="mt-1.5 text-[11px] text-ink-min">
          {view === "new"
            ? `Districts on the ${data.cycle} ballot, and where their residents lived before.`
            : `Districts the members now serving were elected in, and where their residents went.`}{" "}
          Filtered in your browser: nothing is sent anywhere.
        </p>
        <p role="status" aria-live="polite" className="sr-only">
          {filter.trim()
            ? `${shown.length} of ${rows.length} districts match ${filter}. ${placeSummary.join(" ")}`
            : ""}
        </p>
        {placeSummary.length > 0 && (
          <div
            aria-hidden="true"
            className="mt-2 border border-white/15 px-3 py-2 text-[13px] text-ink"
          >
            {placeSummary.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        )}
        <ol className="mt-3 border-b border-white/[0.07]">
          {view === "new"
            ? (shown as NewDistrict[]).map((d) => <NewRow key={d.district} d={d} filter={filter} />)
            : (shown as OldDistrict[]).map((d) => (
                <OldRow key={d.district} d={d} filter={filter} />
              ))}
        </ol>
        {shown.length === 0 && (
          <p className="border border-white/[0.09] p-4 text-xs text-ink-min">
            No district matches &ldquo;{filter}&rdquo;. Try the name of your city, town or county,
            or a district number.
          </p>
        )}
        <p className="mt-4 flex flex-wrap gap-x-6 font-mono text-xs tracking-[0.1em]">
          <Link
            href={stateBallotHref(data.state)}
            className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
          >
            [ WHO IS ON {data.stateName.toUpperCase()}&apos;S BALLOT → ]
          </Link>{" "}
          <Link
            href={REDISTRICTING_HREF}
            className="inline-flex min-h-6 items-center text-ink-lo hover:text-ink-hi"
          >
            [ EVERY REDRAWN STATE ]
          </Link>
        </p>
      </section>

      <MethodAndData data={data} />
    </>
  );
}
