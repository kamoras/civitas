"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import ElectionMapMethod from "./ElectionMapMethod";
import ClosestSeats from "./ClosestSeats";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import { trapTab } from "@/lib/focusedDialog";
import { MAP_LABEL_HALO } from "@/lib/results";
import {
  CHAMBER_LABEL,
  CLOSE_HATCH,
  HOUSE_GAP,
  HOUSE_TILE,
  MAP_CHAMBERS,
  chamberView,
  chamberWord,
  formatAsOf,
  houseLayout,
  stateAriaLabel,
  type MapChamber,
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
  mapTitle,
  fundamentalsSentence,
  pollSourceSentence,
  racePageHref,
  nationalSentence,
  rangeLabel,
  seatAriaLabel,
  seatBin,
  seatName,
  stampSentence,
  type MapView,
} from "@/lib/electionMap";
import type {
  ElectionMapData,
  ElectionMapResponse,
  MapBin,
  MapSeat,
  RangedSeat,
} from "@/types/electionMap";

const SHARE_SOURCE =
  "Civitas, from official returns (MEDSL, FEC, House Clerk, VEST, Census); polls: Wikipedia contributors, CC BY-SA 4.0; past polls: Silver Bulletin";

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

const NO_POLL_STROKE = "#CDC7BC";

/** A tile's fill and outline in a view. A seat with no poll of its own
 * (usual vote and national swing only) is outlined in dashes in the Polls
 * view as in the Difference view, so it is never drawn as if polled; a
 * seat with no usual-vote figure is an empty dashed square, never the "no
 * contest" fill. A selected seat is outlined in ink. */
function tileStyle(
  s: MapSeat,
  view: MapView,
  bin: MapBin | null,
  hatch: string,
  width: number,
  selected = false
) {
  const ranged = s.status === "range";
  const base = (() => {
    if (s.status === "no_lean")
      return {
        fill: "transparent",
        stroke: NO_POLL_STROKE,
        strokeWidth: width * 2,
        strokeDasharray: "0.8 0.8",
      };
    if (view === "diff")
      return {
        fill: "transparent",
        stroke: s.usualBin ? binFill(s.usualBin) : NO_POLL_STROKE,
        strokeWidth: width * 2,
        strokeDasharray: ranged && s.difference.seatPolls ? undefined : "0.6 0.6",
      };
    if (view === "polls" && ranged && !s.difference.seatPolls)
      return {
        fill: fillFor(bin, hatch),
        stroke: NO_POLL_STROKE,
        strokeWidth: width * 3,
        strokeDasharray: "0.9 0.7",
      };
    return {
      fill: ranged || view === "usual" ? fillFor(bin, hatch) : NO_CONTEST_FILL,
      stroke: "rgba(255,255,255,0.18)",
      strokeWidth: width,
      strokeDasharray: undefined as string | undefined,
    };
  })();
  return selected ? { ...base, stroke: "#F2EEE7", strokeWidth: width * 4 } : base;
}

/** The Senate tile map: one tile per state, a seat's tile split in two
 * where a state has two contests. `mini` draws a thumbnail with no labels
 * and no interaction. */
function SenateTileMap({
  data,
  view,
  selected,
  onSelect,
  onState,
  mini = false,
}: {
  data: ElectionMapData;
  view: MapView;
  selected?: string | null;
  onSelect?: (seatId: string) => void;
  onState?: (state: string) => void;
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
      className="night block h-auto w-full"
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
                  y={y + TILE / 2}
                  textAnchor="middle"
                  dominantBaseline="central"
                  className={`map-tile-label fill-ink-min/75 font-mono ${MAP_LABEL_HALO}`}
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
                : "cursor-pointer outline-none [&:focus-visible>rect.frame]:stroke-signal-cyan [&:focus-visible>rect.frame]:[stroke-width:1]"
            }
            onClick={
              mini
                ? undefined
                : (e) => {
                    // A click on one half of a two-race state opens that race.
                    const hit = (e.target as Element).getAttribute("data-seat");
                    if (hit) onSelect?.(hit);
                    else if (seats.length > 1) onState?.(state);
                    else onSelect?.(seats[0].id);
                  }
            }
            onKeyDown={
              mini
                ? undefined
                : (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      // Two races: the keyboard gets a chooser listing both.
                      if (seats.length > 1) onState?.(state);
                      else onSelect?.(seats[0].id);
                    }
                  }
            }
          >
            {seats.map((s, i) => {
              const bin = seatBin(s, view);
              return (
                <rect
                  key={s.id}
                  data-seat={mini ? undefined : s.id}
                  x={x + i * part}
                  y={y}
                  width={part}
                  height={TILE}
                  {...tileStyle(s, view, bin, hatch, 0.25)}
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
                y={y + (view === "diff" ? 3.6 : TILE / 2)}
                textAnchor="middle"
                dominantBaseline={view === "diff" ? undefined : "central"}
                className={`pointer-events-none fill-ink-hi font-mono ${MAP_LABEL_HALO} ${
                  view === "diff" ? "map-tile-label-diff" : "map-tile-label"
                }`}
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
                    className={`pointer-events-none font-mono motion-safe:animate-[map-pulse_0.6s_ease-in-out_2] ${MAP_LABEL_HALO}`}
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

/** The House tile map: one equal-size tile per seat, each state a block
 * (houseLayout). A state is one keyboard stop, never 435; Enter or a tap
 * opens its seats as a list. On a wide screen a click on a tile opens that
 * seat directly. */
function HouseTileMap({
  data,
  view,
  selected,
  onSelect,
  onState,
  mini = false,
}: {
  data: ElectionMapData;
  view: MapView;
  selected?: string | null;
  onSelect?: (seatId: string) => void;
  onState?: (state: string) => void;
  mini?: boolean;
}) {
  const hatch = `hhatch-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const layout = useMemo(() => houseLayout(data.seats), [data.seats]);
  const step = HOUSE_TILE + HOUSE_GAP;
  const open = (state: string, seatId: string | undefined) => {
    const wide =
      typeof window !== "undefined" && window.matchMedia?.("(min-width: 1024px)").matches;
    if (wide && seatId) onSelect?.(seatId);
    else onState?.(state);
  };
  return (
    <svg
      viewBox={`-2 -2 ${layout.width + 4} ${layout.height + 4}`}
      className="night block h-auto w-full"
      role={mini ? undefined : "group"}
      aria-hidden={mini ? true : undefined}
      aria-label={mini ? undefined : "House seats, grouped by state"}
    >
      <HatchDefs id={hatch} />
      {layout.blocks.map((b) => (
        <g
          key={b.state}
          role={mini ? undefined : "button"}
          tabIndex={mini ? undefined : 0}
          aria-label={
            mini
              ? undefined
              : stateAriaLabel(b.seats[0].stateName, b.seats, view, data.bins.leansMax)
          }
          className={
            mini
              ? undefined
              : "cursor-pointer outline-none [&:focus-visible>rect.frame]:stroke-signal-cyan [&:focus-visible>rect.frame]:[stroke-width:1]"
          }
          onClick={
            mini
              ? undefined
              : (e) => open(b.state, (e.target as Element).getAttribute("data-seat") ?? undefined)
          }
          onKeyDown={
            mini
              ? undefined
              : (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onState?.(b.state);
                  }
                }
          }
        >
          {!mini && (
            <text
              x={b.x}
              y={b.y - 2}
              className="pointer-events-none fill-ink-min font-mono"
              fontSize="6"
            >
              {b.state}
            </text>
          )}
          {/* An invisible hit area around the block, so a small state
              is still a 24px target once the map is phone-sized. */}
          {!mini && (
            <rect
              x={b.x - 3}
              y={b.y - 9}
              width={b.cols * step + 5}
              height={b.rows * step + 11}
              fill="transparent"
            />
          )}
          {b.seats.map((s, i) => {
            const x = b.x + (i % b.cols) * step;
            const y = b.y + Math.floor(i / b.cols) * step;
            const bin = seatBin(s, view);
            const diff = view === "diff";
            const glyph = diff ? differenceGlyph(s) : null;
            return (
              <g key={s.id}>
                <rect
                  data-seat={s.id}
                  x={x}
                  y={y}
                  width={HOUSE_TILE}
                  height={HOUSE_TILE}
                  {...tileStyle(s, view, bin, hatch, 0.3, s.id === selected)}
                />
                {glyph && (
                  <text
                    x={x + HOUSE_TILE / 2}
                    y={y + 8}
                    textAnchor="middle"
                    fontSize="9"
                    fill={s.status === "range" && s.difference.direction === "D" ? DEM : REP}
                    className={`pointer-events-none font-mono motion-safe:animate-[map-pulse_0.6s_ease-in-out_2] ${MAP_LABEL_HALO}`}
                  >
                    {glyph}
                  </text>
                )}
              </g>
            );
          })}
          {!mini && (
            <rect
              className="frame"
              x={b.x - 1}
              y={b.y - 1}
              width={b.cols * step + 1}
              height={b.rows * step + 1}
              fill="none"
              stroke="transparent"
              strokeWidth="0.8"
            />
          )}
        </g>
      ))}
    </svg>
  );
}

function TileMap(props: {
  data: ElectionMapData;
  view: MapView;
  selected?: string | null;
  onSelect?: (seatId: string) => void;
  onState?: (state: string) => void;
  mini?: boolean;
}) {
  return props.data.chamber === "house" ? (
    <HouseTileMap {...props} />
  ) : (
    <SenateTileMap {...props} />
  );
}

/** The House on a phone: one row per state, a full-width target, with its
 * seats as equal tiles big enough to see (the tile map's 7 px tiles are a
 * picture at that width, not something to tap). */
function HouseStateList({
  data,
  view,
  onState,
}: {
  data: ElectionMapData;
  view: MapView;
  onState: (state: string) => void;
}) {
  const byState = useMemo(() => {
    const m = new Map<string, MapSeat[]>();
    for (const s of data.seats) m.set(s.state, [...(m.get(s.state) ?? []), s]);
    return [...m.entries()].sort((a, b) => a[1][0].stateName.localeCompare(b[1][0].stateName));
  }, [data.seats]);
  const swatch = (s: MapSeat): React.CSSProperties => {
    const bin = seatBin(s, view);
    const dashed =
      s.status === "no_lean" ||
      (view !== "usual" && s.status === "range" && !s.difference.seatPolls);
    const border = dashed ? `1px dashed ${NO_POLL_STROKE}` : "1px solid rgba(255,255,255,0.18)";
    if (view === "diff" || bin === null) return { border, background: "transparent" };
    if (bin === "close")
      return {
        border,
        background: `repeating-linear-gradient(-45deg, ${CLOSE_HATCH}99 0 1px, ${binFill("close")} 1px 3px)`,
      };
    return { border, background: binFill(bin) };
  };
  return (
    <ul className="divide-y divide-white/[0.07] border-y border-white/[0.07]">
      {byState.map(([state, seats]) => (
        <li key={state}>
          <button
            type="button"
            onClick={() => onState(state)}
            aria-label={stateAriaLabel(seats[0].stateName, seats, view, data.bins.leansMax)}
            className="flex min-h-11 w-full items-center gap-3 py-1.5 text-left"
          >
            <span className="w-28 shrink-0 text-sm text-ink-hi">{seats[0].stateName}</span>
            <span className="flex flex-wrap gap-[3px]" aria-hidden="true">
              {seats.map((s) => (
                <span key={s.id} className="relative block h-3 w-3" style={swatch(s)}>
                  {view === "diff" && differenceGlyph(s) && (
                    <span
                      className="absolute inset-0 text-center font-mono text-[9px] leading-3"
                      style={{
                        color: s.status === "range" && s.difference.direction === "D" ? DEM : REP,
                      }}
                    >
                      {differenceGlyph(s)}
                    </span>
                  )}
                </span>
              ))}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A House state's seats as a list (the phone's bottom sheet, and the
 * keyboard's way into a state). */
function StateSheet({
  data,
  state,
  onSelect,
  onClose,
}: {
  data: ElectionMapData;
  state: string;
  onSelect: (seatId: string) => void;
  onClose: () => void;
}) {
  const seats = data.seats.filter((s) => s.state === state);
  return (
    <div className="space-y-3 text-sm text-ink-lo">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-mono text-xs text-ink-hi">
          {seats[0]?.stateName} · {seats.length} {seats.length === 1 ? "seat" : "seats"}
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the state's seats"
          className="min-h-11 min-w-11 border border-white/20 px-3 font-mono text-xs text-ink-lo hover:text-ink-hi"
        >
          ✕
        </button>
      </div>
      <ul className="divide-y divide-white/[0.07]">
        {seats.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className="flex min-h-11 w-full flex-wrap items-baseline justify-between gap-x-3 py-2 text-left hover:text-ink-hi"
            >
              <span className="text-ink-hi">{seatName(s)}</span>
              <span className="font-mono text-xs">
                lean {s.leanLabel ?? "–"}
                {s.status === "range"
                  ? ` · margin ${rangeLabel(s)}${s.rangeBasisShort ? ` (${s.rangeBasisShort})` : ""}${crossesEven(s) ? " · close" : ""}`
                  : " · no D-vs-R range"}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <p className="font-mono text-xs text-ink-min">Not a race call.</p>
    </div>
  );
}

function DashedKey() {
  return (
    <svg viewBox="0 0 10 6" className="inline-block h-3 w-4 align-middle" aria-hidden="true">
      <rect
        x="0.5"
        y="0.5"
        width="9"
        height="5"
        fill="none"
        stroke={NO_POLL_STROKE}
        strokeWidth="0.8"
        strokeDasharray="1.2 0.9"
      />
    </svg>
  );
}

/** The Senate's faint tiles: a state with no seat on this ballot. Worded
 * for any cycle (a third of the states vote each time, never the same third). */
function NoRaceKey({ data }: { data: ElectionMapData }) {
  if (data.chamber !== "senate") return null;
  return (
    <p className="mt-1 flex items-center gap-2 font-mono text-[10px] text-ink-lo sm:text-xs">
      <svg viewBox="0 0 10 6" className="inline-block h-3 w-4 align-middle" aria-hidden="true">
        <rect width="10" height="6" fill={NO_CONTEST_FILL} />
      </svg>
      Faint tile, faint letters: no Senate race in this state this year.
    </p>
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
        <li>
          <DashedKey /> no seat poll, national swing only
        </li>
        <li>
          polls point toward R <span style={{ color: REP }}>› »</span>
        </li>
        <li className="basis-full text-ink-min">
          One mark: the usual vote (with incumbency and the national swing) is outside the
          seat&apos;s 80% poll range; two: outside its 95% range.
        </li>
        <li className="basis-full">
          <NoRaceKey data={data} />
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
      {view === "polls" && (
        <p className="mt-2 flex items-center gap-2 font-mono text-[10px] text-ink-lo sm:text-xs">
          <DashedKey /> no poll of this seat: the usual vote and the national swing only
        </p>
      )}
      <NoRaceKey data={data} />
      {data.seats.some((s) => s.status === "no_lean") && (
        <p className="mt-1 font-mono text-[10px] text-ink-lo sm:text-xs">
          Empty dashed square: Civitas has no usual-vote figure for that seat.
        </p>
      )}
      <p className="mt-1 font-mono text-[10px] text-ink-min sm:text-xs">
        {view === "usual"
          ? `${data.units?.lean ?? "Points more Republican (R) or Democratic (D) than the nation."} From the last two presidential elections.`
          : `A seat is coloured by the end of its range nearest even, so a range that crosses even is grey. Dark${data.chamber === "senate" ? ", with bright letters" : ""}: no range (no Democrat-versus-Republican race, or its ballot not yet confirmed).`}
      </p>
      {view === "polls" && data.units && (
        <p className="mt-1 font-mono text-[10px] text-ink-min sm:text-xs">{data.units.margin}</p>
      )}
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
        <span>Even</span>
        <span>R+{span}</span>
      </div>
    </div>
  );
}

function SeatDetail({
  data,
  seat,
  onClose,
  raceHref,
}: {
  data: ElectionMapData;
  seat: MapSeat;
  onClose: () => void;
  /** The race's own page, or its place on an archive page. */
  raceHref: string;
}) {
  const inc = (party: "D" | "R") => (seat.incumbentParty === party ? " (incumbent)" : "");
  return (
    <div className="space-y-3 text-sm text-ink-lo">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs text-ink-min">
            {seat.district != null ? "House" : "Senate"}
            {seat.special ? " · special election" : ""}
          </p>
          <h3 className="font-display text-xl font-semibold text-ink-hi">{seatName(seat)}</h3>
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
          <dt className="font-mono text-xs text-ink-min">Usual vote</dt>
          <dd className="text-ink-hi">
            {seat.leanLabel ? `${seat.leanLabel} lean` : "No figure"}{" "}
            <span className="text-ink-min">
              {seat.district == null
                ? "(Civitas, from official returns)"
                : `(Civitas-computed lean, on the ${data.cycle} district lines)`}
            </span>
          </dd>
        </div>
        {seat.status === "range" ? (
          <div>
            <dt className="font-mono text-xs text-ink-min">
              {seat.rangeBasisLabel ?? "Margin range"}
            </dt>
            <dd>
              <span className="text-ink-hi">margin {rangeLabel(seat)}</span>
              {crossesEven(seat) && (
                <span className="ml-2 font-mono text-xs">Too close to tell</span>
              )}
              <RangeBar seat={seat} />
              <p className="mt-1 text-xs text-ink-min">
                Bar: the 80% range; block: its middle ({seat.meanLabel}); dashed line: the usual
                vote with incumbency and the national swing ({seat.priorLabel}).
              </p>
            </dd>
          </div>
        ) : (
          <div>
            <dt className="font-mono text-xs text-ink-min">No range</dt>
            <dd>{seat.reason}</dd>
          </div>
        )}
      </dl>
      {seat.status === "range" && (
        <>
          <p className="text-xs">
            {coverageSentence(data.calibration, data.chamber, seat.fundamentalsOnly)}
          </p>
          <p className="text-xs">{pollSourceSentence(seat, data.pollWindowDays)}</p>
          {seat.polls.list.length > 0 && (
            <ul
              className="space-y-1 border-t border-white/[0.07] pt-2 text-xs"
              aria-label={
                seat.fundamentalsOnly ? "Newest polls, not in the range" : "Polls in the average"
              }
            >
              {seat.fundamentalsOnly && (
                <li className="font-mono text-ink-min">Newest polls · not in the range</li>
              )}
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
                  {(p.questions ?? [{ dem: p.dem, rep: p.rep }]).map((q, j) => (
                    <span key={j}>
                      {j ? "; " : ""}
                      <span className="text-dem-blue">D {q.dem}</span> /{" "}
                      <span className="text-rep-red">R {q.rep}</span>
                    </span>
                  ))}
                  {p.questions && (
                    <span className="text-ink-min">
                      {" "}
                      (one poll asked {p.questions.length} ways, counted once)
                    </span>
                  )}
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
      <p className="font-mono text-xs text-ink-min">Not a race call.</p>
      <Link
        href={raceHref}
        className="inline-flex min-h-11 items-center font-mono text-xs text-ink-hi underline"
      >
        {raceHref.startsWith("#") ? "→ Count and ballot" : "→ Race page"}
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
          return s.lean ?? Number.POSITIVE_INFINITY;
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
        className="min-h-11 font-mono text-xs text-ink-lo hover:text-ink-hi"
        onClick={() => setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))}
      >
        {label}
        {sort.key === key ? (sort.asc ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Every seat, as a table">
      <table className="w-full min-w-[40rem] border-collapse text-sm text-ink-lo">
        <caption className="sr-only">
          Every {data.cycle} {chamberWord(data.chamber)} seat: usual vote, margin range, polls used
          and difference
        </caption>
        <thead className="border-b border-white/20">
          <tr>
            {th("seat", "Seat")}
            {th("usual", "Usual vote (lean)")}
            {th("range", "Margin range (80%)")}
            {th("polls", "Polls used")}
            <th scope="col" className="px-2 py-1 text-left font-mono text-xs">
              Latest poll
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
              <td className="px-2 py-1 font-mono">{s.leanLabel ?? "–"}</td>
              <td className="px-2 py-1 font-mono">
                {s.status === "range" ? (
                  <>
                    {rangeLabel(s)}
                    {crossesEven(s) ? " · close" : ""}
                    {s.rangeBasisShort && (
                      <span className="font-sans text-xs text-ink-min"> ({s.rangeBasisShort})</span>
                    )}
                  </>
                ) : (
                  <span className="font-sans text-xs">
                    {s.status === "not_contested"
                      ? "No D-vs-R contest"
                      : s.status === "unconfirmed"
                        ? "Ballot not confirmed"
                        : s.status === "no_lean"
                          ? "No usual-vote figure"
                          : "Not a D-vs-R race"}
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
 * The general-election map: how each Senate seat usually votes, what
 * a range of each margin (from polls where a seat has any, never a winner), and
 * where the two disagree. All figures from GET /api/elections/map.
 *
 * View state: latched at mount from the server-read `?view=` (the page
 * renders with it, so a shared link's HTML already shows its view) and
 * written back with history.replaceState — never router.replace, which
 * does nothing on a route loaded with a query string (AGENTS.md).
 */
export default function ElectionMap({
  response,
  initialView,
  initialChamber,
  initialSeat = null,
  pageUrl,
  archiveCycle,
}: {
  /** The page's chamber only (?chamber=); the other loads on demand. */
  response: ElectionMapResponse;
  initialView: MapView;
  initialChamber: MapChamber;
  /** A seat to open on arrival (?seat=, from a race page's map link). */
  initialSeat?: string | null;
  /** The page's absolute URL without a query, for share links. */
  pageUrl: string;
  /** Drawn on an archived election's page: the other chamber and the CSV
   * come from that election's archive, and a seat links to its race
   * further down the same page. */
  archiveCycle?: number;
}) {
  const mapApi = archiveCycle ? `/api/elections/archive/${archiveCycle}/map` : "/api/elections/map";
  const [view, setView] = useState<MapView>(initialView);
  const [loaded, setLoaded] = useState<ElectionMapResponse>(response);
  const [chamber, setChamber] = useState<MapChamber>(
    response.chambers[initialChamber]
      ? initialChamber
      : (Object.keys(response.chambers)[0] as MapChamber)
  );
  const [loading, setLoading] = useState<MapChamber | null>(null);
  const [loadError, setLoadError] = useState(false);
  const data = useMemo(() => chamberView(loaded, chamber), [loaded, chamber]);
  const [selected, setSelected] = useState<string | null>(() =>
    initialSeat && data.seats.some((s) => s.id === initialSeat) ? initialSeat : null
  );
  const [sheetState, setSheetState] = useState<string | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const detailRef = useRef<HTMLDivElement>(null);
  // Where focus was when the detail opened, to return it there on close.
  const openerRef = useRef<HTMLElement | null>(null);
  const counts = useMemo(() => mapCounts(data.seats), [data.seats]);
  const hasPolls = counts.ranged > 0;
  const seat = data.seats.find((s) => s.id === selected) ?? null;
  const open = seat !== null || sheetState !== null;
  // Under the desktop layout the detail is a bottom sheet over the page: a
  // modal dialog, focus kept inside it until it closes.
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(max-width: 1023px)");
    if (!mq) return;
    const update = () => setNarrow(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  const modal = open && narrow;

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("chamber", chamber);
    url.searchParams.set("view", view);
    url.searchParams.delete("seat");
    window.history.replaceState(null, "", url.toString());
  }, [view, chamber]);

  const close = () => {
    setSelected(null);
    setSheetState(null);
    const opener = openerRef.current;
    openerRef.current = null;
    requestAnimationFrame(() => opener?.focus());
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        return;
      }
      // The sheet is modal on a phone: Tab cycles inside it.
      if (!modal || !detailRef.current) return;
      trapTab(e, [
        ...detailRef.current.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled]), select, [tabindex='0']"
        ),
      ]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, modal]);

  const remember = () => {
    const active = document.activeElement as HTMLElement | null;
    if (active && !detailRef.current?.contains(active)) openerRef.current = active;
  };
  const select = (id: string) => {
    remember();
    setSelected(id);
    setSheetState(null);
    requestAnimationFrame(() => detailRef.current?.focus());
  };
  const openState = (state: string) => {
    remember();
    setSelected(null);
    setSheetState(state);
    requestAnimationFrame(() => detailRef.current?.focus());
  };
  const switchChamber = async (c: MapChamber) => {
    setSelected(null);
    setSheetState(null);
    setLoadError(false);
    if (loaded.chambers[c]) {
      setChamber(c);
      return;
    }
    // The other chamber is fetched when it is first asked for: a reader of
    // one chamber never downloads both.
    setLoading(c);
    try {
      const res = await fetch(`${mapApi}?chamber=${c}`);
      if (!res.ok) throw new Error(String(res.status));
      const more = (await res.json()) as ElectionMapResponse;
      setLoaded((prev) => ({ ...prev, chambers: { ...prev.chambers, ...more.chambers } }));
      setChamber(c);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(null);
    }
  };
  // The thumbnails in the view buttons are drawn on a wide screen only, and
  // only in the browser: a phone never downloads three extra maps.
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(min-width: 1024px)");
    if (!mq) return;
    const update = () => setWide(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  return (
    <ShareSubjectProvider
      subject={{
        title: `${mapTitle(data.cycle, chamber)}, ${VIEW_LABEL[view].toLowerCase()}`,
        subtitle: `As of ${formatAsOf(data.asOf)} · Ranges, not results · Not a race call`,
        url: `${pageUrl}?chamber=${chamber}&view=${view}`,
        source: SHARE_SOURCE,
        asOf: data.asOf,
      }}
    >
      <div className="space-y-4">
        {/* Clear of the fixed site header when scrolled to: globals.css scroll-padding-top. */}
        <div id="map-views" {...{ [SHARE_EXCLUDE_ATTR]: "" }} className="space-y-3">
          <div role="group" aria-label="Chamber" className="flex flex-wrap font-mono text-xs">
            {MAP_CHAMBERS.map((c, i) => (
              <button
                key={c}
                type="button"
                aria-pressed={chamber === c}
                disabled={loading !== null}
                onClick={() => switchChamber(c)}
                className={`min-h-11 border px-4 ${
                  chamber === c
                    ? "border-phos bg-surface-raised text-ink-hi"
                    : "border-white/20 text-ink-min hover:text-ink-hi"
                } ${i ? "-ml-px" : ""}`}
              >
                {CHAMBER_LABEL[c]}
              </button>
            ))}
          </div>
          <p aria-live="polite" className="font-mono text-xs text-ink-lo">
            {loading
              ? `Loading the ${chamberWord(loading)} map…`
              : loadError
                ? "That map couldn't be loaded; try again."
                : ""}
          </p>
          <div role="group" aria-label="Map view" className="flex flex-wrap font-mono text-xs">
            {MAP_VIEWS.map((v, i) => {
              const disabled = v !== "usual" && !hasPolls;
              return (
                <button
                  key={v}
                  type="button"
                  aria-pressed={view === v}
                  // The name is the label alone: the thumbnail's glyphs
                  // (« ‹ › ») are a picture, never part of it (WCAG 4.1.2).
                  aria-label={VIEW_LABEL[v]}
                  disabled={disabled}
                  onClick={() => setView(v)}
                  className={`min-h-11 flex-1 border px-2 sm:flex-none sm:px-4 ${
                    view === v
                      ? "border-phos bg-surface-raised text-ink-hi"
                      : "border-white/20 text-ink-min hover:text-ink-hi disabled:opacity-60"
                  } ${i ? "-ml-px" : ""}`}
                >
                  {wide && (
                    <span className="block w-24 py-2" aria-hidden="true">
                      <TileMap data={data} view={v} mini />
                    </span>
                  )}
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
            {changedLine(view, counts, data.national, data.bins)}
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

        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <section
            id="general-map"
            aria-labelledby="general-map-stamp"
            // On a phone a full-width band, the map to the page's 16px
            // gutters: tiles 28 -> 30px at 390px (mobile spec P6).
            className="relative -mx-4 min-w-0 border-y border-phos/20 bg-surface px-4 py-3 sm:mx-0 sm:border-x sm:p-4"
            {...{ [SHARE_SECTION_ATTR]: "general-map" }}
            // A share image is drawn at a fixed layout width with the tile
            // map, whatever the screen: a phone's House state list made a
            // 1:8 strip a feed shrinks to nothing (globals.css).
            data-share-fixed-layout=""
            {...shareMetaAttrs({
              source: SHARE_SOURCE,
              asOf: data.asOf,
              scaleMax: null,
            })}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="general-map-stamp" className="font-mono text-xs text-ink-hi">
                  {VIEW_STAMP[view]} · {CHAMBER_LABEL[chamber]}
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
                label={`${mapTitle(data.cycle, chamber)}, ${VIEW_LABEL[view].toLowerCase()}`}
                anchor="general-map"
              />
            </div>
            {chamber === "house" && (
              // A phone-sized tile map makes a one-seat state a small
              // target; this picker is the same action at full size.
              <div className="mt-3 md:hidden" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                <label
                  htmlFor="house-state-picker"
                  className="block font-mono text-xs text-ink-min"
                >
                  Pick a state
                </label>
                <select
                  id="house-state-picker"
                  value={sheetState ?? ""}
                  onChange={(e) => e.target.value && openState(e.target.value)}
                  className="mt-1 min-h-11 w-full border border-white/20 bg-surface-raised px-3 font-mono text-sm text-ink-hi"
                >
                  <option value="">Choose a state…</option>
                  {[...new Map(data.seats.map((s) => [s.state, s.stateName])).entries()]
                    .sort((a, b) => a[1].localeCompare(b[1]))
                    .map(([code, name]) => (
                      <option key={code} value={code}>
                        {name}
                      </option>
                    ))}
                </select>
              </div>
            )}
            {!data.pollsInRange && view !== "usual" && (
              <p className="mt-2 font-mono text-xs text-ink-hi">
                Fundamentals only · {data.daysToElection} days out.{" "}
                <span className="text-ink-lo">{fundamentalsSentence(data.pollWindowDays)}</span>
              </p>
            )}
            {data.stale && (
              <p className="mt-2 font-mono text-xs text-signal-amber">
                Not updated since {formatAsOf(data.asOf)}: the latest refreshes failed, so these
                figures are older than usual.
              </p>
            )}
            {chamber === "house" && (
              <div className="mt-3 md:hidden" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                <HouseStateList data={data} view={view} onState={openState} />
              </div>
            )}
            <div
              className={`mx-auto mt-3 max-w-[38rem] ${chamber === "house" ? "hidden md:block" : ""}`}
              data-share-show=""
            >
              <TileMap
                data={data}
                view={view}
                selected={selected}
                onSelect={select}
                onState={openState}
              />
            </div>
            <div className="mt-3">
              <Legend data={data} view={view} />
              {view !== "usual" && (
                <p className="mt-2 font-mono text-xs text-ink-lo">
                  {nationalSentence(data.national)}
                </p>
              )}
            </div>
            {/* The phone's way in: the closest seats as rows, each opening
                the same sheet as a tile. Not in the shared picture. */}
            <div className="lg:hidden" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
              <ClosestSeats seats={data.seats} view={view} onSelect={select} />
            </div>
            <p className="mt-3 border-t border-white/[0.07] pt-2 font-mono text-[11px] leading-relaxed text-ink-min">
              {footerText(data)}
            </p>
          </section>

          {modal && (
            <div aria-hidden="true" className="fixed inset-0 z-30 bg-black/60" onClick={close} />
          )}
          <aside
            ref={detailRef}
            tabIndex={-1}
            aria-label="Seat detail"
            {...(modal ? { role: "dialog", "aria-modal": true } : {})}
            {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            className={`border border-white/[0.07] bg-surface-raised p-4 outline-none ${
              open
                ? "fixed inset-x-0 bottom-0 z-40 max-h-[75vh] overflow-y-auto lg:static lg:max-h-none"
                : "hidden lg:block"
            }`}
          >
            {seat ? (
              <SeatDetail
                data={data}
                seat={seat}
                onClose={close}
                raceHref={archiveCycle ? `#race-${seat.id}` : racePageHref(seat)}
              />
            ) : sheetState ? (
              <StateSheet data={data} state={sheetState} onSelect={select} onClose={close} />
            ) : (
              <p className="text-sm text-ink-lo">
                {chamber === "house"
                  ? "Choose a state for its seats, or a seat for its usual vote, its poll range, the polls behind it and a link to the race."
                  : "Choose a state for its usual vote, its poll range, the polls behind it and a link to the race."}
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
            className="min-h-11 border border-white/20 px-4 font-mono text-xs text-ink-lo hover:text-ink-hi"
          >
            Table {tableOpen ? "▴" : "▾"}
          </button>
          <a
            href={`${mapApi}.csv`}
            className="inline-flex min-h-11 items-center border border-white/20 px-4 font-mono text-xs text-ink-lo hover:text-ink-hi"
          >
            CSV
          </a>
        </div>
        <div id="general-map-table" hidden={!tableOpen}>
          {tableOpen && <SeatTable data={data} onSelect={select} />}
        </div>
        <div className="mt-12 max-w-3xl">
          <ElectionMapMethod data={data} />
        </div>
      </div>
    </ShareSubjectProvider>
  );
}
