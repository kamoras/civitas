"use client";

import type { KeyboardEvent } from "react";
import { useState } from "react";
import { FIPS_TO_STATE } from "@/lib/stateCodes";
import { ComposableMap, Geographies, Geography, useMapContext } from "react-simple-maps";

// Extracted from ElectionsTab.tsx (2026-07) so the Action Center teaser's
// map and the full /elections map share one implementation instead of
// forking it. Coloring is left entirely to the caller via getFillColor/
// getHoverFillColor — this component only knows how to render the US map
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

interface RaceMapProps {
  selectedState: string | null;
  onStateClick: (state: string) => void;
  /** Fill color for a state's default (non-hover) appearance. */
  getFillColor: (state: string, isSelected: boolean) => string;
  /** Fill color for a state on hover. */
  getHoverFillColor: (state: string, isSelected: boolean) => string;
}

export default function RaceMap({
  selectedState,
  onStateClick,
  getFillColor,
  getHoverFillColor,
}: RaceMapProps) {
  // react-simple-maps v5 dropped Geography's built-in default/hover/pressed
  // style object in favor of a plain `style` prop, same as any other SVG
  // element — hover is now tracked ourselves.
  const [hoveredFips, setHoveredFips] = useState<string | null>(null);

  const activate = (e: KeyboardEvent, stateCode: string) => {
    if (e.key === "Enter" || e.key === " ") {
      if (e.key === " ") e.preventDefault(); // don't scroll the page
      onStateClick(stateCode);
    }
  };

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
            const isHovered = hoveredFips === fips;

            return (
              <Geography
                key={geo.rsmKey}
                geography={geo}
                onClick={() => onStateClick(stateCode)}
                onMouseEnter={() => setHoveredFips(fips)}
                onMouseLeave={() => setHoveredFips(null)}
                // react-simple-maps hardcodes tabIndex=0 on each path, but
                // SVG paths don't fire onClick from Enter/Space — wire up
                // button semantics + keyboard activation ourselves.
                role="button"
                aria-label={stateCode}
                onKeyDown={(e: KeyboardEvent) => activate(e, stateCode)}
                style={{
                  fill: isSelected
                    ? "#00ffff"
                    : isHovered
                      ? getHoverFillColor(stateCode, isSelected)
                      : getFillColor(stateCode, isSelected),
                  stroke: isHovered ? "#00ff41" : "#0a1a0a",
                  strokeWidth: isHovered ? 1 : 0.5,
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
            setHoveredFips={setHoveredFips}
            onStateClick={onStateClick}
            activate={activate}
            getFillColor={getFillColor}
            getHoverFillColor={getHoverFillColor}
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
  setHoveredFips,
  onStateClick,
  activate,
  getFillColor,
  getHoverFillColor,
}: {
  geographies: { id?: string | number; rsmKey: string }[];
  selectedState: string | null;
  hoveredFips: string | null;
  setHoveredFips: (fips: string | null) => void;
  onStateClick: (state: string) => void;
  activate: (e: KeyboardEvent, state: string) => void;
  getFillColor: RaceMapProps["getFillColor"];
  getHoverFillColor: RaceMapProps["getHoverFillColor"];
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
        const isHovered = hoveredFips === fips;
        return (
          <g
            key={stateCode}
            role="button"
            tabIndex={0}
            aria-label={stateCode}
            onClick={() => onStateClick(stateCode)}
            onKeyDown={(e) => activate(e, stateCode)}
            onMouseEnter={() => setHoveredFips(fips)}
            onMouseLeave={() => setHoveredFips(null)}
            style={{ cursor: "pointer", outline: "none" }}
          >
            <line
              x1={cx}
              y1={cy}
              x2={CALLOUT_X}
              y2={y}
              stroke={isHovered ? "#00ff41" : "rgba(255,255,255,0.25)"}
              strokeWidth={0.75}
            />
            <rect
              x={CALLOUT_X}
              y={y - 12}
              width={46}
              height={24}
              fill={
                isSelected
                  ? "#00ffff"
                  : isHovered
                    ? getHoverFillColor(stateCode, isSelected)
                    : getFillColor(stateCode, isSelected)
              }
              stroke={isHovered ? "#00ff41" : "rgba(255,255,255,0.35)"}
              strokeWidth={1}
            />
            <text
              x={CALLOUT_X + 23}
              y={y + 5}
              textAnchor="middle"
              fontFamily="ui-monospace, monospace"
              fontSize={14}
              fill="#e8e4dc"
            >
              {stateCode}
            </text>
          </g>
        );
      })}
    </g>
  );
}
