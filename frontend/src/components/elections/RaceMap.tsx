"use client";

import { overlay } from "@/lib/chartColors";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { FIPS_TO_STATE } from "@/lib/stateCodes";
import { MAP_LABEL_HALO } from "@/lib/results";
import { ComposableMap, Geographies, Geography, useMapContext } from "react-simple-maps";
import { STATE_TILES, TILE_COLS, TILE_ROWS } from "@/lib/electionMap";
import ScrollRegion from "@/components/shared/ScrollRegion";
import ChartReadout, { type ChartMark } from "@/components/shared/ChartReadout";
import { MARK_ATTR, type ReadoutMark } from "@/types/readout";

// Extracted from the Action Center's old elections tab (2026-07), when it and
// /elections both drew this map; the tab was removed in 2026-09. Colouring is
// left entirely to the caller via getFillColor/getHoverFillColor, and what
// each state's readout says to `marks`: this component only knows how to
// draw the US map and make each state readable.

// Vendored copy of us-atlas@3's states-10m.json (see public/data/) so the
// map doesn't depend on a third-party CDN at runtime.
const GEO_URL = "/data/states-10m.json";

// Re-exported so existing callers keep importing it from here; the map
// itself lives in lib/stateCodes.ts so server modules can use it too.
export { FIPS_TO_STATE };

// States too small to hit on the map itself — Rhode Island renders at
// 4x5 pixels on a phone, Delaware 5x8 — measured with a hit test against
// the live page at 390px wide. Each gets a labelled box off the coast,
// joined to it by a leader line, the way printed election maps do it.
// North to south, so the boxes read in the same order as the coast.
const CALLOUT_STATES = ["VT", "NH", "MA", "RI", "CT", "NJ", "DE", "MD"];
const CALLOUT_X = 880; // Maine, the furthest-east land, ends at x≈851
const CALLOUT_TOP = 130;
const CALLOUT_STEP = 34;
// Each box as tall as the step allows (2 units apart), so it is 24px from
// a 735px-wide map up (WCAG 2.5.8); narrower, the tiles take over.
const CALLOUT_H = 32;
const CALLOUT_W = 56;

interface RaceMapProps {
  /** The map's accessible name: what its colour means. */
  label: string;
  /** Each state's readout (name, figure, page), by code: the API's words. */
  marks: Record<string, ReadoutMark>;
  /** Fill color for a state's default (non-hover) appearance. */
  getFillColor: (state: string) => string;
  /** Fill color for a state on hover or focus. */
  getHoverFillColor: (state: string) => string;
  /** SVG <defs> (patterns) the fills may reference. */
  defs?: ReactNode;
}

/**
 * The states, each readable on hover, tap and the keyboard (ChartReadout):
 * one Tab stop, the arrow keys walking the states west to east, north to
 * south, and the label naming the state, its figure and its page, which a
 * click (a second tap on a phone) or Enter opens. On a phone the map is a
 * grid of equal 24px tiles; from 640px up, the shapes.
 */
export default function RaceMap({
  label,
  marks,
  getFillColor,
  getHoverFillColor,
  defs,
}: RaceMapProps) {
  const [active, setActive] = useState<string | null>(null);
  // Geographic order: row by row of the tile grid, west to east.
  const ordered = useMemo<ChartMark[]>(
    () =>
      Object.entries(STATE_TILES)
        .filter(([st]) => marks[st])
        .sort(([, [ac, ar]], [, [bc, br]]) => ar - br || ac - bc)
        .map(([st]) => marks[st]),
    [marks]
  );
  const fill = (st: string) => (active === st ? getHoverFillColor(st) : getFillColor(st));
  return (
    <>
      {/* The fills' patterns, once, in an SVG that is never display:none:
          Chrome paints no pattern whose element sits in a hidden subtree,
          and each map is hidden at one width or the other. */}
      {defs && (
        <svg aria-hidden="true" width="0" height="0" className="absolute">
          {defs}
        </svg>
      )}
      <StateTiles label={label} marks={ordered} active={active} onActive={setActive} fill={fill} />
      <div className="hidden sm:block">
        <ChartReadout
          label={label}
          marks={ordered}
          labelAt="mark"
          ring={false}
          onActive={setActive}
        >
          <ShapeMap active={active} fill={fill} />
        </ChartReadout>
      </div>
    </>
  );
}

// A phone's tile: 24px at the least (WCAG 2.5.8), in viewBox units that map
// one to one onto CSS pixels at the grid's minimum width.
const TILE_PX = 24;
const TILE_GAP = 2;
const TILE_STEP = TILE_PX + TILE_GAP;

/**
 * On a phone the shapes are too small to tap (Rhode Island is 4x5 pixels,
 * Alabama 17x27, Indiana 14x23 at 375px), so the map is a grid of equal
 * tiles, each state roughly where it sits (STATE_TILES, the seat map's
 * layout), every tile a 24px target. Same fills and readouts as the
 * shapes; from 640px up the shapes take over. Narrower than 12 tiles
 * (320px at 200% text), the grid scrolls in its own box.
 */
function StateTiles({
  label,
  marks,
  active,
  onActive,
  fill,
}: {
  label: string;
  marks: ChartMark[];
  active: string | null;
  onActive: (st: string | null) => void;
  fill: (st: string) => string;
}) {
  const states = new Set(Object.values(FIPS_TO_STATE));
  const width = TILE_COLS * TILE_STEP - TILE_GAP;
  const height = TILE_ROWS * TILE_STEP - TILE_GAP;
  return (
    <div className="sm:hidden">
      <ScrollRegion label="Map of the states, as tiles">
        <ChartReadout label={label} marks={marks} labelAt="mark" ring={false} onActive={onActive}>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="block h-auto w-full"
            style={{ minWidth: width }}
          >
            {Object.entries(STATE_TILES)
              .filter(([st]) => states.has(st))
              .map(([st, [col, row]]) => {
                const x = col * TILE_STEP;
                const y = row * TILE_STEP;
                const on = active === st;
                return (
                  <g key={st} {...{ [MARK_ATTR]: st }}>
                    <rect
                      x={x}
                      y={y}
                      width={TILE_PX}
                      height={TILE_PX}
                      style={{
                        fill: fill(st),
                        stroke: on ? "var(--phos)" : overlay(0.25),
                        strokeWidth: on ? 2.5 : 1,
                      }}
                    />
                    <text
                      x={x + TILE_PX / 2}
                      y={y + TILE_PX / 2}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={13}
                      fill="var(--ink-hi)"
                      pointerEvents="none"
                      className={`font-mono ${MAP_LABEL_HALO}`}
                    >
                      {st}
                    </text>
                  </g>
                );
              })}
          </svg>
        </ChartReadout>
      </ScrollRegion>
    </div>
  );
}

function ShapeMap({ active, fill }: { active: string | null; fill: (st: string) => string }) {
  return (
    <ComposableMap
      projection="geoAlbersUsa"
      projectionConfig={{ scale: 1000 }}
      width={980}
      height={600}
      style={{ width: "100%", height: "auto" }}
    >
      <Geographies geography={GEO_URL}>
        {({ geographies }) => [
          ...geographies.map((geo) => {
            const stateCode = FIPS_TO_STATE[geo.id as string];
            if (!stateCode) return null;
            const on = active === stateCode;
            return (
              <Geography
                key={geo.rsmKey}
                geography={geo}
                // react-simple-maps makes every path a Tab stop; the map is
                // one, and moves among the states itself.
                tabIndex={-1}
                {...{ [MARK_ATTR]: stateCode }}
                style={{
                  fill: fill(stateCode),
                  stroke: on ? "var(--phos)" : "var(--surface-base)",
                  strokeWidth: on ? 2.5 : 0.5,
                  outline: "none",
                }}
              />
            );
          }),
          <SmallStateCallouts
            key="callouts"
            geographies={geographies}
            active={active}
            fill={fill}
          />,
        ]}
      </Geographies>
    </ComposableMap>
  );
}

function SmallStateCallouts({
  geographies,
  active,
  fill,
}: {
  geographies: { id?: string | number; rsmKey: string }[];
  active: string | null;
  fill: (st: string) => string;
}) {
  // The map's own path generator, so each leader line starts at the
  // state as drawn — no second projection to keep in step with this one.
  // d3's GeoPath carries .centroid(); the library's type only declares
  // the call signature.
  const { path } = useMapContext() as unknown as {
    path: { centroid: (geo: unknown) => [number, number] };
  };
  const byState = new Map(geographies.map((g) => [FIPS_TO_STATE[String(g.id)], g]));

  return (
    <g>
      {CALLOUT_STATES.map((stateCode, i) => {
        const geo = byState.get(stateCode);
        if (!geo) return null;
        const [cx, cy] = path.centroid(geo);
        const y = CALLOUT_TOP + i * CALLOUT_STEP;
        const on = active === stateCode;
        return (
          <g key={stateCode}>
            <line
              x1={cx}
              y1={cy}
              x2={CALLOUT_X}
              y2={y}
              stroke={on ? "var(--phos)" : overlay(0.25)}
              strokeWidth={0.75}
            />
            <rect
              {...{ [MARK_ATTR]: stateCode }}
              x={CALLOUT_X}
              y={y - CALLOUT_H / 2}
              width={CALLOUT_W}
              height={CALLOUT_H}
              fill={fill(stateCode)}
              stroke={on ? "var(--phos)" : overlay(0.35)}
              strokeWidth={on ? 2.5 : 1}
            />
            <text
              x={CALLOUT_X + CALLOUT_W / 2}
              y={y + 5}
              textAnchor="middle"
              fontFamily="ui-monospace, monospace"
              fontSize={14}
              fill="var(--ink-hi)"
              pointerEvents="none"
              className={MAP_LABEL_HALO}
            >
              {stateCode}
            </text>
          </g>
        );
      })}
    </g>
  );
}
