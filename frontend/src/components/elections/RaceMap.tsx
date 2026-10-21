"use client";

import { overlay } from "@/lib/chartColors";
import type { KeyboardEvent, ReactNode } from "react";
import { useState } from "react";
import { FIPS_TO_STATE } from "@/lib/stateCodes";
import { MAP_LABEL_HALO } from "@/lib/results";
import { ComposableMap, Geographies, Geography, useMapContext } from "react-simple-maps";
import { STATE_TILES, TILE_COLS, TILE_ROWS } from "@/lib/electionMap";
import ScrollRegion from "@/components/shared/ScrollRegion";

// Extracted from the Action Center's old elections tab (2026-07), when it and
// /elections both drew this map; the tab was removed in 2026-09. Coloring is
// left entirely to the caller via getFillColor/getHoverFillColor — this component only knows how to render the US map
// and report clicks, not what a given race/state "means" (race type, PVI,
// results, etc. differ by caller).

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
  selectedState: string | null;
  onStateClick: (state: string) => void;
  /** Fill color for a state's default (non-hover) appearance. */
  getFillColor: (state: string, isSelected: boolean) => string;
  /** Fill color for a state on hover. */
  getHoverFillColor: (state: string, isSelected: boolean) => string;
  /** Each state's accessible name. Defaults to its code; a map whose fill
   * means something (the results map) says it here too, so colour is
   * never the only way to read it. */
  getStateLabel?: (state: string) => string;
  /** SVG <defs> (patterns) the fills may reference. */
  defs?: ReactNode;
}

export default function RaceMap({
  selectedState,
  onStateClick,
  getFillColor,
  getHoverFillColor,
  getStateLabel = (state) => state,
  defs,
}: RaceMapProps) {
  // react-simple-maps v5 dropped Geography's built-in default/hover/pressed
  // style object in favor of a plain `style` prop, same as any other SVG
  // element — hover is now tracked ourselves.
  const [hoveredFips, setHoveredFips] = useState<string | null>(null);
  // Keyboard focus gets the hover outline, thicker: the paths set
  // `outline: none` (a rectangle around a state's bounding box is
  // meaningless), which left a keyboard user tabbing through 50 states
  // with no visible focus at all (WCAG 2.4.7).
  const [focusedFips, setFocusedFips] = useState<string | null>(null);

  const activate = (e: KeyboardEvent, stateCode: string) => {
    if (e.key === "Enter" || e.key === " ") {
      if (e.key === " ") e.preventDefault(); // don't scroll the page
      onStateClick(stateCode);
    }
  };

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
      <StateTiles
        selectedState={selectedState}
        onStateClick={onStateClick}
        getFillColor={getFillColor}
        getStateLabel={getStateLabel}
      />
      <div className="hidden sm:block">
        <ShapeMap
          selectedState={selectedState}
          onStateClick={onStateClick}
          getFillColor={getFillColor}
          getHoverFillColor={getHoverFillColor}
          getStateLabel={getStateLabel}
          hoveredFips={hoveredFips}
          setHoveredFips={setHoveredFips}
          focusedFips={focusedFips}
          setFocusedFips={setFocusedFips}
          activate={activate}
        />
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
 * layout), every tile a 24px button. Same fills, names and clicks as the
 * shapes; from 640px up the shapes take over. Narrower than 12 tiles
 * (320px at 200% text), the grid scrolls in its own box.
 */
function StateTiles({
  selectedState,
  onStateClick,
  getFillColor,
  getStateLabel,
}: Pick<RaceMapProps, "selectedState" | "onStateClick" | "getFillColor"> & {
  getStateLabel: (state: string) => string;
}) {
  const states = new Set(Object.values(FIPS_TO_STATE));
  const width = TILE_COLS * TILE_STEP - TILE_GAP;
  const height = TILE_ROWS * TILE_STEP - TILE_GAP;
  return (
    <div className="sm:hidden">
      <ScrollRegion label="Map of the states, as tiles">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="block h-auto w-full"
          style={{ minWidth: width }}
        >
          {Object.entries(STATE_TILES)
            .filter(([st]) => states.has(st))
            .map(([st, [col, row]]) => {
              const selected = selectedState === st;
              const x = col * TILE_STEP;
              const y = row * TILE_STEP;
              return (
                <g
                  key={st}
                  role="button"
                  tabIndex={0}
                  aria-label={getStateLabel(st)}
                  onClick={() => onStateClick(st)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onStateClick(st);
                    }
                  }}
                  className="cursor-pointer outline-none [&:focus-visible>rect]:stroke-[var(--phos)] [&:focus-visible>rect]:[stroke-width:2.5]"
                >
                  <rect
                    x={x}
                    y={y}
                    width={TILE_PX}
                    height={TILE_PX}
                    style={{
                      fill: selected ? "var(--signal-cyan)" : getFillColor(st, selected),
                      stroke: selected ? "var(--phos)" : overlay(0.25),
                      strokeWidth: 1,
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
      </ScrollRegion>
    </div>
  );
}

function ShapeMap({
  selectedState,
  onStateClick,
  getFillColor,
  getHoverFillColor,
  getStateLabel,
  hoveredFips,
  setHoveredFips,
  focusedFips,
  setFocusedFips,
  activate,
}: Omit<RaceMapProps, "getStateLabel" | "defs"> & {
  getStateLabel: (state: string) => string;
  hoveredFips: string | null;
  setHoveredFips: (fips: string | null) => void;
  focusedFips: string | null;
  setFocusedFips: (fips: string | null) => void;
  activate: (e: KeyboardEvent, state: string) => void;
}) {
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
            const fips = geo.id as string;
            const stateCode = FIPS_TO_STATE[fips];
            if (!stateCode) return null;
            const isSelected = selectedState === stateCode;
            const isHovered = hoveredFips === fips || focusedFips === fips;
            const isFocused = focusedFips === fips;

            return (
              <Geography
                key={geo.rsmKey}
                geography={geo}
                onClick={() => onStateClick(stateCode)}
                onMouseEnter={() => setHoveredFips(fips)}
                onMouseLeave={() => setHoveredFips(null)}
                onFocus={() => setFocusedFips(fips)}
                onBlur={() => setFocusedFips(null)}
                // react-simple-maps hardcodes tabIndex=0 on each path, but
                // SVG paths don't fire onClick from Enter/Space — wire up
                // button semantics + keyboard activation ourselves.
                role="button"
                aria-label={getStateLabel(stateCode)}
                onKeyDown={(e: KeyboardEvent) => activate(e, stateCode)}
                style={{
                  fill: isSelected
                    ? "var(--signal-cyan)"
                    : isHovered
                      ? getHoverFillColor(stateCode, isSelected)
                      : getFillColor(stateCode, isSelected),
                  stroke: isHovered ? "var(--phos)" : "var(--surface-base)",
                  strokeWidth: isFocused ? 2.5 : isHovered ? 1 : 0.5,
                  outline: "none",
                  cursor: "pointer",
                }}
              />
            );
          }),
          <SmallStateCallouts
            key="callouts"
            geographies={geographies}
            selectedState={selectedState}
            hoveredFips={hoveredFips}
            focusedFips={focusedFips}
            setHoveredFips={setHoveredFips}
            setFocusedFips={setFocusedFips}
            onStateClick={onStateClick}
            activate={activate}
            getFillColor={getFillColor}
            getHoverFillColor={getHoverFillColor}
            getStateLabel={getStateLabel}
          />,
        ]}
      </Geographies>
    </ComposableMap>
  );
}

function SmallStateCallouts({
  geographies,
  selectedState,
  hoveredFips,
  focusedFips,
  setHoveredFips,
  setFocusedFips,
  onStateClick,
  activate,
  getFillColor,
  getHoverFillColor,
  getStateLabel,
}: {
  geographies: { id?: string | number; rsmKey: string }[];
  selectedState: string | null;
  hoveredFips: string | null;
  focusedFips: string | null;
  setHoveredFips: (fips: string | null) => void;
  setFocusedFips: (fips: string | null) => void;
  onStateClick: (state: string) => void;
  activate: (e: KeyboardEvent, state: string) => void;
  getFillColor: RaceMapProps["getFillColor"];
  getHoverFillColor: RaceMapProps["getHoverFillColor"];
  getStateLabel: (state: string) => string;
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
        const isSelected = selectedState === stateCode;
        const fips = String(geo.id);
        const isFocused = focusedFips === fips;
        const isHovered = hoveredFips === fips || isFocused;
        return (
          <g
            key={stateCode}
            role="button"
            tabIndex={0}
            aria-label={getStateLabel(stateCode)}
            onClick={() => onStateClick(stateCode)}
            onKeyDown={(e) => activate(e, stateCode)}
            onMouseEnter={() => setHoveredFips(fips)}
            onMouseLeave={() => setHoveredFips(null)}
            onFocus={() => setFocusedFips(fips)}
            onBlur={() => setFocusedFips(null)}
            style={{ cursor: "pointer", outline: "none" }}
          >
            <line
              x1={cx}
              y1={cy}
              x2={CALLOUT_X}
              y2={y}
              stroke={isHovered ? "var(--phos)" : overlay(0.25)}
              strokeWidth={0.75}
            />
            <rect
              x={CALLOUT_X}
              y={y - CALLOUT_H / 2}
              width={CALLOUT_W}
              height={CALLOUT_H}
              fill={
                isSelected
                  ? "var(--signal-cyan)"
                  : isHovered
                    ? getHoverFillColor(stateCode, isSelected)
                    : getFillColor(stateCode, isSelected)
              }
              stroke={isHovered ? "var(--phos)" : overlay(0.35)}
              strokeWidth={isFocused ? 2.5 : 1}
            />
            <text
              x={CALLOUT_X + CALLOUT_W / 2}
              y={y + 5}
              textAnchor="middle"
              fontFamily="ui-monospace, monospace"
              fontSize={14}
              fill="var(--ink-hi)"
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
