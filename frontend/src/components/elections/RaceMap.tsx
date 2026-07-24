"use client";

import type { KeyboardEvent } from "react";
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

export const FIPS_TO_STATE: Record<string, string> = {
  "01": "AL",
  "02": "AK",
  "04": "AZ",
  "05": "AR",
  "06": "CA",
  "08": "CO",
  "09": "CT",
  "10": "DE",
  "11": "DC",
  "12": "FL",
  "13": "GA",
  "15": "HI",
  "16": "ID",
  "17": "IL",
  "18": "IN",
  "19": "IA",
  "20": "KS",
  "21": "KY",
  "22": "LA",
  "23": "ME",
  "24": "MD",
  "25": "MA",
  "26": "MI",
  "27": "MN",
  "28": "MS",
  "29": "MO",
  "30": "MT",
  "31": "NE",
  "32": "NV",
  "33": "NH",
  "34": "NJ",
  "35": "NM",
  "36": "NY",
  "37": "NC",
  "38": "ND",
  "39": "OH",
  "40": "OK",
  "41": "OR",
  "42": "PA",
  "44": "RI",
  "45": "SC",
  "46": "SD",
  "47": "TN",
  "48": "TX",
  "49": "UT",
  "50": "VT",
  "51": "VA",
  "53": "WA",
  "54": "WV",
  "55": "WI",
  "56": "WY",
};

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

            return (
              <Geography
                key={geo.rsmKey}
                geography={geo}
                onClick={() => onStateClick(stateCode)}
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
                  default: {
                    fill: isSelected ? "#00ffff" : getFillColor(stateCode, isSelected),
                    stroke: "#0a1a0a",
                    strokeWidth: 0.5,
                    outline: "none",
                    cursor: "pointer",
                  },
                  hover: {
                    fill: isSelected ? "#00ffff" : getHoverFillColor(stateCode, isSelected),
                    stroke: "#00ff41",
                    strokeWidth: 1,
                    outline: "none",
                    cursor: "pointer",
                  },
                  pressed: {
                    fill: "#00ffff",
                    stroke: "#00ff41",
                    strokeWidth: 1,
                    outline: "none",
                  },
                }}
              />
            );
          })
        }
      </Geographies>
    </ComposableMap>
  );
}
