"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import {
  CLOSE_HATCH,
  MAP_VIEWS,
  NO_CONTEST_FILL,
  STATE_TILES,
  TILE_COLS,
  TILE_ROWS,
  VIEW_EXPLAINER,
  VIEW_LABEL,
  VIEW_STAMP,
  binFill,
  changedLine,
  coverageSentence,
  crossesEven,
  differenceGlyph,
  footerText,
  legendCells,
  mapCounts,
  marginLabel,
  nationalSentence,
  rangeLabel,
  seatAriaLabel,
  seatBin,
  seatName,
  stampSentence,
  type MapView,
} from "@/lib/electionMap";
import type { ElectionMapData, MapBin, MapSeat, RangedSeat } from "@/types/electionMap";

const TILE = 10;
const GAP = 1;
const W = TILE_COLS * (TILE + GAP);
const H = TILE_ROWS * (TILE + GAP);
const DEM = "rgb(130, 172, 255)";
const REP = "rgb(255, 137, 137)";

/** The close hatch: grey under thin ink-lo lines at -45°, finer than any
 * results-map texture so the two maps never share a symbol. */
function HatchDefs({ id }: { id: string }) {
  return (
    <defs>
      <pattern
        id={id}
        patternUnits="userSpaceOnUse"
        width="2.2"
        height="2.2"
        patternTransform="rotate(-45)"
      >
        <rect width="2.2" height="2.2" fill={binFill("close")} />
        <rect width="0.6" height="2.2" fill={CLOSE_HATCH} fillOpacity="0.6" />
      </pattern>
    </defs>
  );
}

function fillFor(bin: MapBin | null, hatch: string): string {
  if (bin === null) return "transparent";
  return bin === "close" ? `url(#${hatch})` : binFill(bin);
}

/** The national tile map: one tile per state, a seat's tile split in two
 * where a state has two contests. `mini` draws a thumbnail with no labels
 * and no interaction. */
function TileMap({
  data,
  view,
  selected,
  onSelect,
  mini = false,
}: {
  data: ElectionMapData;
  view: MapView;
  selected?: string | null;
  onSelect?: (seatId: string) => void;
  mini?: boolean;
}) {
  const hatch = `hatch-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const byState = useMemo(() => {
    const m = new Map<string, MapSeat[]>();
    for (const s of data.seats) m.set(s.state, [...(m.get(s.state) ?? []), s]);
    return m;
  }, [data.seats]);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="block h-auto w-full"
      role={mini ? undefined : "group"}
      aria-hidden={mini ? true : undefined}
      aria-label={mini ? undefined : "Senate seats by state"}
    >
      <HatchDefs id={hatch} />
      {Object.entries(STATE_TILES).map(([state, [col, row]]) => {
        const x = col * (TILE + GAP);
        const y = row * (TILE + GAP);
        const seats = byState.get(state) ?? [];
        if (!seats.length) {
          return (
            <g key={state} aria-hidden="true">
              <rect x={x} y={y} width={TILE} height={TILE} fill={NO_CONTEST_FILL} />
              {!mini && (
                <text
                  x={x + TILE / 2}
                  y={y + TILE / 2 + 1.2}
                  textAnchor="middle"
                  className="fill-ink-min/50 font-mono"
                  fontSize="3"
                >
                  {state}
                </text>
              )}
            </g>
          );
        }
        const part = TILE / seats.length;
        return (
          <g
            key={state}
            role={mini ? undefined : "button"}
            tabIndex={mini ? undefined : 0}
            aria-label={mini ? undefined : seats.map((s) => seatAriaLabel(s, view)).join(" ")}
            aria-pressed={mini ? undefined : seats.some((s) => s.id === selected)}
            className={
              mini
                ? undefined
                : "cursor-pointer outline-none [&:focus-visible>rect.frame]:stroke-ink-hi"
            }
            onClick={mini ? undefined : () => onSelect?.(seats[0].id)}
            onKeyDown={
              mini
                ? undefined
                : (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect?.(seats[0].id);
                    }
                  }
            }
          >
            {seats.map((s, i) => {
              const bin = seatBin(s, view);
              const diff = view === "diff";
              const ranged = s.status === "range";
              return (
                <rect
                  key={s.id}
                  x={x + i * part}
                  y={y}
                  width={part}
                  height={TILE}
                  fill={
                    diff
                      ? "transparent"
                      : ranged || view === "usual"
                        ? fillFor(bin, hatch)
                        : NO_CONTEST_FILL
                  }
                  stroke={diff ? binFill(s.usualBin) : "rgba(255,255,255,0.18)"}
                  strokeWidth={diff ? 0.5 : 0.25}
                  strokeDasharray={
                    diff && !(ranged && s.difference.seatPolls) ? "0.6 0.6" : undefined
                  }
                  className="transition-[fill] duration-150 motion-reduce:transition-none"
                />
              );
            })}
            {!mini && (
              <rect
                className="frame"
                x={x - 0.5}
                y={y - 0.5}
                width={TILE + 1}
                height={TILE + 1}
                fill="none"
                stroke={seats.some((s) => s.id === selected) ? "#F2EEE7" : "transparent"}
                strokeWidth="0.6"
              />
            )}
            {!mini && (
              <text
                x={x + TILE / 2}
                y={y + (view === "diff" ? 3.6 : TILE / 2 + 1.2)}
                textAnchor="middle"
                className="pointer-events-none fill-ink-hi font-mono"
                fontSize="3"
              >
                {state}
              </text>
            )}
            {view === "diff" &&
              seats.map((s, i) => {
                const glyph = differenceGlyph(s);
                if (!glyph) return null;
                const d = s.status === "range" && s.difference.direction === "D";
                return (
                  <text
                    key={`g-${s.id}`}
                    x={x + i * part + part / 2}
                    y={y + (mini ? 7.5 : 8.6)}
                    textAnchor="middle"
                    fontSize={mini ? 7 : 5}
                    fill={d ? DEM : REP}
                    className="pointer-events-none font-mono motion-safe:animate-[map-pulse_0.6s_ease-in-out_2]"
                  >
                    {glyph}
                  </text>
                );
              })}
          </g>
        );
      })}
    </svg>
  );
}

function Legend({ data, view }: { data: ElectionMapData; view: MapView }) {
  const hatch = `lg-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  if (view === "diff") {
    return (
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs text-ink-lo">
        <li>
          <span style={{ color: DEM }}>« ‹</span> polls point toward D
        </li>
        <li>· · no seat poll, national swing only</li>
        <li>
          polls point toward R <span style={{ color: REP }}>› »</span>
        </li>
        <li className="basis-full text-ink-min">
          One mark: the usual vote (with incumbency and the national swing) is outside the
          seat&apos;s 80% poll range; two: outside its 95% range.
        </li>
      </ul>
    );
  }
  return (
    <div>
      <svg width="0" height="0" className="absolute" aria-hidden="true">
        <HatchDefs id={hatch} />
      </svg>
      <ul className="grid grid-cols-7 gap-px font-mono text-[10px] leading-tight text-ink-lo sm:text-xs">
        {legendCells(data.bins, view).map(({ bin, label }) => (
          <li key={bin} className="flex flex-col items-center gap-1 text-center">
            <svg viewBox="0 0 10 6" className="h-3 w-full" aria-hidden="true">
              <rect
                width="10"
                height="6"
                fill={fillFor(bin, hatch)}
                stroke="rgba(255,255,255,0.3)"
                strokeWidth="0.3"
              />
            </svg>
            <span>{label}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 font-mono text-[10px] text-ink-min sm:text-xs">
        {view === "usual"
          ? "Points more Republican (R) or Democratic (D) than the nation, in the last two presidential elections."
          : "Margin in points. A seat is coloured by the end of its range nearest even, so a range that crosses even is grey. Dark with a label: no Democrat-versus-Republican race to range."}
      </p>
    </div>
  );
}

/** The interval bar: D on the left, R on the right, even in the middle. */
function RangeBar({ seat }: { seat: RangedSeat }) {
  const span = 40;
  const pos = (v: number) => 50 + (Math.max(-span, Math.min(span, v)) / span) * 50;
  const [lo, hi] = seat.range80;
  return (
    <div className="mt-2" aria-hidden="true">
      <div className="relative h-5 border-x border-white/20">
        <span className="absolute inset-y-0 left-1/2 w-px bg-white/40" />
        <span
          className="absolute top-1.5 h-2 bg-ink-lo/50"
          style={{ left: `${pos(lo)}%`, width: `${Math.max(pos(hi) - pos(lo), 0.5)}%` }}
        />
        <span
          className="absolute top-1 h-3 w-1 -translate-x-1/2 bg-ink-hi"
          style={{ left: `${pos(seat.mean)}%` }}
        />
        <span
          className="absolute top-0 h-5 w-px -translate-x-1/2 border-l border-dashed border-ink-min"
          style={{ left: `${pos(seat.prior)}%` }}
        />
      </div>
      <div className="mt-0.5 flex justify-between font-mono text-[10px] text-ink-min">
        <span>D+{span}</span>
        <span>EVEN</span>
        <span>R+{span}</span>
      </div>
    </div>
  );
}

function SeatDetail({
  data,
  seat,
  onClose,
}: {
  data: ElectionMapData;
  seat: MapSeat;
  onClose: () => void;
}) {
  const inc = (party: "D" | "R") => (seat.incumbentParty === party ? " (incumbent)" : "");
  return (
    <div className="space-y-3 text-sm text-ink-lo">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            Senate{seat.special ? " · special election" : ""}
          </p>
          <h3 className="font-display text-xl font-bold text-ink-hi">{seatName(seat)}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the seat detail"
          className="min-h-11 min-w-11 border border-white/20 px-3 font-mono text-xs text-ink-lo hover:text-ink-hi"
        >
          ✕
        </button>
      </div>
      {(seat.dem || seat.rep) && (
        <p>
          {seat.dem && (
            <span className="text-dem-blue">
              {seat.dem} (D){inc("D")}
            </span>
          )}
          {seat.dem && seat.rep && " · "}
          {seat.rep && (
            <span className="text-rep-red">
              {seat.rep} (R){inc("R")}
            </span>
          )}
        </p>
      )}
      <dl className="space-y-2">
        <div>
          <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Usual vote</dt>
          <dd className="text-ink-hi">
            {seat.leanLabel} <span className="text-ink-min">(Cook PVI {seat.leanLabel})</span>
          </dd>
        </div>
        {seat.status === "range" ? (
          <div>
            <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
              Polls suggest
            </dt>
            <dd>
              <span className="text-ink-hi">a margin from {rangeLabel(seat.range80)}</span>
              {crossesEven(seat) && (
                <span className="ml-2 font-mono text-xs">TOO CLOSE TO TELL</span>
              )}
              <RangeBar seat={seat} />
              <p className="mt-1 text-xs text-ink-min">
                Bar: the 80% range; block: its middle ({marginLabel(seat.mean)}); dashed line: the
                usual vote with incumbency and the national swing ({seat.priorLabel}).
              </p>
            </dd>
          </div>
        ) : (
          <div>
            <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">No range</dt>
            <dd>{seat.reason}</dd>
          </div>
        )}
      </dl>
      {seat.status === "range" && (
        <>
          <p className="text-xs">{coverageSentence(data.calibration)}</p>
          <p className="text-xs">
            {seat.polls.used
              ? `${seat.polls.used} ${seat.polls.used === 1 ? "poll" : "polls"} in the average (the latest from each pollster in the last 45 days, newest ${seat.polls.latest}); they carry ${Math.round(seat.pollShare * 100)}% of the range's weight, the usual vote the rest.`
              : "No poll of this race in the last 45 days: the range is the usual vote with incumbency and the national swing alone, which is why it is wide."}
          </p>
          {seat.polls.list.length > 0 && (
            <ul className="space-y-1 border-t border-white/[0.07] pt-2 text-xs">
              {seat.polls.list.slice(0, 6).map((p, i) => (
                <li key={`${p.pollster}-${p.end}-${i}`}>
                  {p.url ? (
                    <a
                      href={p.url}
                      className="underline decoration-white/30 hover:text-ink-hi"
                      rel="noopener noreferrer"
                    >
                      {p.pollster}
                    </a>
                  ) : (
                    p.pollster
                  )}
                  {p.partisan && (
                    <span className="text-ink-min">
                      {" "}
                      (marked partisan, {p.partisan}, by the article)
                    </span>
                  )}{" "}
                  · {p.end}
                  {p.sample ? ` · ${p.sample.toLocaleString("en-US")} ${p.population}` : ""} ·{" "}
                  <span className="text-dem-blue">D {p.dem}</span> /{" "}
                  <span className="text-rep-red">R {p.rep}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {seat.article?.url && (
        <p className="text-xs text-ink-min">
          Polls read from{" "}
          <a
            href={seat.article.url}
            className="underline decoration-white/30 hover:text-ink-hi"
            rel="noopener noreferrer"
          >
            Wikipedia, revision {seat.article.revid}
          </a>{" "}
          (Wikipedia contributors, CC BY-SA 4.0).
        </p>
      )}
      <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Not a race call.</p>
      <Link
        href={`/elections/${seat.id}`}
        className="inline-flex min-h-11 items-center font-mono text-xs text-ink-hi underline"
      >
        → Race page
      </Link>
    </div>
  );
}

type SortKey = "seat" | "usual" | "range" | "polls" | "diff";

function SeatTable({ data, onSelect }: { data: ElectionMapData; onSelect: (id: string) => void }) {
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({ key: "seat", asc: true });
  const rows = useMemo(() => {
    const val = (s: MapSeat): number | string => {
      switch (sort.key) {
        case "seat":
          return seatName(s);
        case "usual":
          return s.lean;
        case "range":
          return s.status === "range" ? s.mean : Number.POSITIVE_INFINITY;
        case "polls":
          return s.status === "range" ? s.polls.used : -1;
        case "diff":
          return s.status === "range"
            ? (s.difference.direction === "D" ? -1 : 1) * s.difference.strength
            : 0;
      }
    };
    return [...data.seats].sort((a, b) => {
      const x = val(a);
      const y = val(b);
      const c = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return sort.asc ? c : -c;
    });
  }, [data.seats, sort]);
  const th = (key: SortKey, label: string) => (
    <th
      scope="col"
      aria-sort={sort.key === key ? (sort.asc ? "ascending" : "descending") : "none"}
      className="px-2 py-1 text-left"
    >
      <button
        type="button"
        className="min-h-11 font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-ink-hi"
        onClick={() => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}
      >
        {label}
        {sort.key === key ? (sort.asc ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] border-collapse text-sm text-ink-lo">
        <caption className="sr-only">
          Every 2026 Senate seat: usual vote, poll range, polls used and difference
        </caption>
        <thead className="border-b border-white/20">
          <tr>
            {th("seat", "Seat")}
            {th("usual", "Usual vote")}
            {th("range", "Poll range (80%)")}
            {th("polls", "Polls used")}
            <th
              scope="col"
              className="px-2 py-1 text-left font-mono text-xs uppercase tracking-[0.12em]"
            >
              As of
            </th>
            {th("diff", "Difference")}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} className="border-b border-white/[0.07]">
              <th scope="row" className="px-2 py-1 text-left font-normal">
                <button
                  type="button"
                  className="min-h-11 text-left text-ink-hi underline decoration-white/30"
                  onClick={() => onSelect(s.id)}
                >
                  {seatName(s)}
                </button>
              </th>
              <td className="px-2 py-1 font-mono">{s.leanLabel}</td>
              <td className="px-2 py-1 font-mono">
                {s.status === "range" ? (
                  <>
                    {rangeLabel(s.range80)}
                    {crossesEven(s) ? " · close" : ""}
                  </>
                ) : (
                  <span className="font-sans text-xs">
                    {s.status === "not_contested" ? "No D-vs-R contest" : "Not a D-vs-R race"}
                  </span>
                )}
              </td>
              <td className="px-2 py-1 font-mono">{s.status === "range" ? s.polls.used : "–"}</td>
              <td className="px-2 py-1 font-mono">
                {s.status === "range" ? (s.polls.latest ?? "–") : "–"}
              </td>
              <td className="px-2 py-1 font-mono">
                {s.status === "range" && s.difference.strength > 0
                  ? `${differenceGlyph(s)} toward ${s.difference.direction}`
                  : s.status === "range" && !s.difference.seatPolls
                    ? "no seat poll"
                    : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The 2026 general-election map: how each Senate seat usually votes, what
 * this year's polls suggest (a range of the margin, never a winner), and
 * where the two disagree. All figures from GET /api/elections/map.
 *
 * View state: latched at mount from the server-read `?view=` (the page
 * renders with it, so a shared link's HTML already shows its view) and
 * written back with history.replaceState — never router.replace, which
 * does nothing on a route loaded with a query string (AGENTS.md).
 */
export default function ElectionMap({
  data,
  initialView,
}: {
  data: ElectionMapData;
  initialView: MapView;
}) {
  const [view, setView] = useState<MapView>(initialView);
  const [selected, setSelected] = useState<string | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const detailRef = useRef<HTMLDivElement>(null);
  const counts = useMemo(() => mapCounts(data.seats, data.bins), [data.seats, data.bins]);
  const hasPolls = counts.ranged > 0;
  const seat = data.seats.find((s) => s.id === selected) ?? null;

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("chamber", "senate");
    url.searchParams.set("view", view);
    window.history.replaceState(null, "", url.toString());
  }, [view]);

  useEffect(() => {
    if (!seat) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSelected(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seat]);

  const select = (id: string) => {
    setSelected(id);
    requestAnimationFrame(() => detailRef.current?.focus());
  };

  return (
    <div className="space-y-4">
      {/* Clear of the fixed site header when scrolled to (scroll-mt). */}
      <div
        id="map-views"
        {...{ [SHARE_EXCLUDE_ATTR]: "" }}
        className="scroll-mt-[var(--header-clearance)] space-y-3"
      >
        <div
          role="group"
          aria-label="Map view"
          className="flex font-mono text-xs tracking-[0.12em]"
        >
          {MAP_VIEWS.map((v, i) => {
            const disabled = v !== "usual" && !hasPolls;
            return (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                disabled={disabled}
                onClick={() => setView(v)}
                className={`min-h-11 flex-1 border px-2 sm:flex-none sm:px-4 ${
                  view === v
                    ? "border-phos bg-surface-raised text-ink-hi"
                    : "border-white/20 text-ink-min hover:text-ink-hi disabled:opacity-60"
                } ${i ? "-ml-px" : ""}`}
              >
                <span className="hidden w-24 py-2 lg:block" aria-hidden="true">
                  <TileMap data={data} view={v} mini />
                </span>
                {VIEW_LABEL[v]}
              </button>
            );
          })}
        </div>
        {!hasPolls && (
          <p className="text-sm text-ink-lo">
            Polls and Difference are off: no seat has a poll-informed range yet.
          </p>
        )}
        <p className="min-w-0 break-words font-mono text-xs text-ink-lo">
          {changedLine(view, counts, data.national)}
          {view !== "diff" && counts.differ > 0 && (
            <>
              {" "}
              <button
                type="button"
                onClick={() => setView("diff")}
                className="inline min-h-6 whitespace-normal break-words text-left text-ink-hi underline"
              >
                {counts.differ} {counts.differ === 1 ? "seat" : "seats"} where polls tell a
                different story →
              </button>
            </>
          )}
        </p>
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section
          id="general-map"
          aria-labelledby="general-map-stamp"
          className="relative min-w-0 scroll-mt-[var(--header-clearance)] border border-phos/20 bg-surface p-3 sm:p-4"
          {...{ [SHARE_SECTION_ATTR]: "general-map" }}
          {...shareMetaAttrs({
            source:
              "Civitas, from official returns (MEDSL, FEC); polls: Wikipedia contributors, CC BY-SA 4.0",
            asOf: data.asOf,
            scaleMax: null,
          })}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2
                id="general-map-stamp"
                className="font-mono text-xs uppercase tracking-[0.14em] text-ink-hi"
              >
                {VIEW_STAMP[view]} · SENATE
              </h2>
              <p aria-live="polite" className="sr-only">
                {stampSentence(view, counts, data.asOf)}
              </p>
              <p className="mt-1 text-sm text-ink-lo">
                {VIEW_EXPLAINER[view]}{" "}
                <a
                  href="#method"
                  className="underline decoration-white/30 hover:text-ink-hi"
                  {...{ [SHARE_EXCLUDE_ATTR]: "" }}
                >
                  How this is measured
                </a>
              </p>
            </div>
            <ShareSectionButton
              label={`2026 Senate map, ${VIEW_LABEL[view].toLowerCase()}`}
              anchor="general-map"
            />
          </div>
          <div className="mx-auto mt-3 max-w-[38rem]">
            <TileMap data={data} view={view} selected={selected} onSelect={select} />
          </div>
          <div className="mt-3">
            <Legend data={data} view={view} />
            {view !== "usual" && (
              <p className="mt-2 font-mono text-xs text-ink-lo">
                {nationalSentence(data.national)}
              </p>
            )}
          </div>
          <p className="mt-3 border-t border-white/[0.07] pt-2 font-mono text-[11px] leading-relaxed text-ink-min">
            {footerText(data)}
          </p>
        </section>

        <aside
          ref={detailRef}
          tabIndex={-1}
          aria-label="Seat detail"
          {...{ [SHARE_EXCLUDE_ATTR]: "" }}
          className={`border border-white/[0.07] bg-surface-raised p-4 outline-none ${
            seat
              ? "fixed inset-x-0 bottom-0 z-40 max-h-[75vh] overflow-y-auto lg:static lg:max-h-none"
              : "hidden lg:block"
          }`}
        >
          {seat ? (
            <SeatDetail data={data} seat={seat} onClose={() => setSelected(null)} />
          ) : (
            <p className="text-sm text-ink-lo">
              Choose a state for its usual vote, its poll range, the polls behind it and a link to
              the race.
            </p>
          )}
        </aside>
      </div>

      <div {...{ [SHARE_EXCLUDE_ATTR]: "" }} className="flex flex-wrap gap-3">
        <button
          type="button"
          aria-expanded={tableOpen}
          aria-controls="general-map-table"
          onClick={() => setTableOpen((o) => !o)}
          className="min-h-11 border border-white/20 px-4 font-mono text-xs tracking-[0.12em] text-ink-lo hover:text-ink-hi"
        >
          TABLE {tableOpen ? "▴" : "▾"}
        </button>
        <a
          href="/api/elections/map.csv"
          className="inline-flex min-h-11 items-center border border-white/20 px-4 font-mono text-xs tracking-[0.12em] text-ink-lo hover:text-ink-hi"
        >
          CSV
        </a>
      </div>
      <div id="general-map-table" hidden={!tableOpen}>
        <SeatTable data={data} onSelect={select} />
      </div>
    </div>
  );
}
