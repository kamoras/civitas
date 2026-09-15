"use client";

import type { KeyboardEvent } from "react";
import { useState } from "react";
import { FIPS_TO_STATE } from "@/lib/stateCodes";
import { ComposableMap, Geographies, Geography } from "react-simple-maps";

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

  return (
    <ComposableMap
      projection="geoAlbersUsa"
      projectionConfig={{ scale: 1000 }}
      width={980}
      height={600}
      style={{ width: "100%", height: "auto" }}
    >
      <Geographies geography={GEO_URL}>
        {({ geographies }) =>
          geographies.map((geo) => {
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
                onKeyDown={(e: KeyboardEvent) => {
                  if (e.key === "Enter" || e.key === " ") {
                    if (e.key === " ") e.preventDefault(); // don't scroll the page
                    onStateClick(stateCode);
                  }
                }}
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
          })
        }
      </Geographies>
    </ComposableMap>
  );
}
