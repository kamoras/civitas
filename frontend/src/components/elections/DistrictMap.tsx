"use client";

import type { KeyboardEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { ComposableMap, Geographies, Geography } from "react-simple-maps";
import type { RaceWithCandidates } from "@/types/election";
import { formatPvi, majorPartyOf } from "@/lib/elections";

/**
 * Point at your neighbourhood; the page narrows to its district.
 *
 * The county picker answers "which district am I in" for most people,
 * but 13% of US counties span more than one district and a dense city
 * can hold ten inside one county — there a county answers nothing. A map
 * of the districts themselves is the only view that does, and it asks
 * for nothing: no address, no location permission, nothing sent.
 *
 * It is also the part of the page people play with. Hovering previews a
 * district's race before anyone commits to anything, so a reader can
 * wander the state seeing which seats are close without declaring where
 * they live — which is both more fun and strictly less data than asking.
 *
 * Geometry is the Census cartographic boundary file for the 119th
 * Congress, split per state and vendored under public/data/cd/ by
 * backend/scripts/build_district_topology.py. Each file carries its own
 * bbox, so fitting the projection is the few lines of Mercator arithmetic
 * below rather than another geo library in the bundle.
 *
 * Colour follows the SAME rule as the district list (pviColor: R red,
 * D blue), with intensity for distance from even, so a close seat reads
 * pale and a safe one saturated. Deliberately not a new "toss-up"
 * category: a second classification on the same page would eventually
 * disagree with the first.
 */

const DEM = "#82acff";
const REP = "#ff8989";
const EVEN = "#cdc7bc";
const UNKNOWN = "#3a352f";
const WIDTH = 800;

type Bbox = [number, number, number, number];

function mercatorY(latDeg: number): number {
  const phi = (latDeg * Math.PI) / 180;
  return Math.log(Math.tan(Math.PI / 4 + phi / 2));
}

/** Centre and scale that fit `bbox` into `width` (height follows the
 * state's own aspect ratio, clamped so a tall state is not a sliver and a
 * wide one is not a letterbox). Exported for tests. */
export function fitMercator(bbox: Bbox, width: number, pad = 0.06) {
  const [minLon, minLat, maxLon, maxLat] = bbox;
  const dx = ((maxLon - minLon) * Math.PI) / 180;
  const y0 = mercatorY(minLat);
  const y1 = mercatorY(maxLat);
  const dy = y1 - y0;
  const height = Math.round(Math.min(Math.max((width * dy) / dx, 320), 900));
  const scale = (1 - pad) * Math.min(width / dx, height / dy);
  const midY = (y0 + y1) / 2;
  const centerLat = ((2 * Math.atan(Math.exp(midY)) - Math.PI / 2) * 180) / Math.PI;
  return { center: [(minLon + maxLon) / 2, centerLat] as [number, number], scale, height };
}

/** Fill for a seat's lean — same direction as pviColor, intensity by
 * distance from even. Exported for tests. */
export function leanFill(pvi: number | null): { fill: string; opacity: number } {
  if (pvi == null) return { fill: UNKNOWN, opacity: 1 };
  if (pvi === 0) return { fill: EVEN, opacity: 0.55 };
  const strength = Math.min(Math.abs(pvi), 25) / 25;
  return { fill: pvi > 0 ? REP : DEM, opacity: 0.22 + 0.7 * strength };
}

interface Topo {
  type: "Topology";
  bbox: Bbox;
  objects: { districts: unknown };
}

type GeographyProp = Parameters<typeof Geographies>[0]["geography"];

export default function DistrictMap({
  state,
  races,
  picked,
  onPick,
}: {
  state: string;
  races: RaceWithCandidates[];
  picked: string | null;
  onPick: (raceId: string) => void;
}) {
  const [topo, setTopo] = useState<Topo | null>(null);
  const [failed, setFailed] = useState(false);
  const [hovered, setHovered] = useState<number | null>(null);

  // A single at-large district is one shape filling the frame: nothing to
  // choose between, so no map. Alaska's also spans the antimeridian.
  const multiDistrict = races.filter((r) => r.district != null).length > 1;

  useEffect(() => {
    if (!multiDistrict) return;
    let cancelled = false;
    fetch(`/data/cd/${state}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((t: Topo) => {
        if (!cancelled) setTopo(t);
      })
      .catch(() => {
        // The county picker and text filter still work; a missing map is
        // an absent convenience, not a broken page.
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [state, multiDistrict]);

  const byDistrict = useMemo(() => {
    const m = new Map<number, RaceWithCandidates>();
    for (const r of races) if (r.district != null) m.set(r.district, r);
    return m;
  }, [races]);

  const fit = useMemo(() => (topo?.bbox ? fitMercator(topo.bbox, WIDTH) : null), [topo]);

  if (!multiDistrict || failed || !topo || !fit) return null;

  const pickedDistrict = picked ? races.find((r) => r.id === picked)?.district ?? null : null;
  const focus = hovered ?? pickedDistrict;
  const focusRace = focus != null ? byDistrict.get(focus) : undefined;

  return (
    <div className="mb-4 border border-white/15">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/10 px-3 py-2">
        <p className="font-mono text-xs tracking-[0.1em] text-phos">POINT AT WHERE YOU LIVE</p>
        <p className="font-mono text-[10px] text-ink-min">
          redder = safer R · bluer = safer D · paler = closer
        </p>
      </div>

      <ComposableMap
        projection="geoMercator"
        projectionConfig={{ center: fit.center, scale: fit.scale }}
        width={WIDTH}
        height={fit.height}
        style={{ width: "100%", height: "auto" }}
        aria-label={`Congressional districts of ${state}`}
      >
        {/* react-simple-maps' types admit only GeoJSON, but its runtime
            converts a Topology itself — it checks type === "Topology" and
            runs topojson's feature() on the first object (verified in the
            installed dist). Handing it the Topology keeps the shared arcs,
            which is what keeps these files small. */}
        <Geographies geography={topo as unknown as GeographyProp}>
          {({ geographies }) =>
            geographies.map((geo) => {
              const district = geo.properties?.district as number;
              const race = byDistrict.get(district);
              const { fill, opacity } = leanFill(race?.pvi ?? null);
              const isPicked = district === pickedDistrict;
              const isHovered = district === hovered;
              const label = district === 0 ? `${state} at-large` : `${state}-${district}`;
              return (
                <Geography
                  key={geo.rsmKey}
                  geography={geo}
                  role="button"
                  aria-label={label}
                  aria-pressed={isPicked}
                  onMouseEnter={() => setHovered(district)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(district)}
                  onBlur={() => setHovered(null)}
                  onClick={() => race && onPick(race.id)}
                  onKeyDown={(e: KeyboardEvent) => {
                    if (e.key === "Enter" || e.key === " ") {
                      if (e.key === " ") e.preventDefault();
                      if (race) onPick(race.id);
                    }
                  }}
                  style={{
                    fill,
                    fillOpacity: opacity,
                    stroke: isPicked || isHovered ? "#00ff41" : "#0e0c0a",
                    strokeWidth: isPicked ? 2 : isHovered ? 1.2 : 0.6,
                    outline: "none",
                    cursor: race ? "pointer" : "default",
                  }}
                />
              );
            })
          }
        </Geographies>
      </ComposableMap>

      <div
        aria-live="polite"
        className="min-h-[3.25rem] border-t border-white/10 px-3 py-2 font-mono text-xs"
      >
        {focusRace ? (
          <DistrictPreview state={state} race={focusRace} />
        ) : (
          <span className="text-ink-min">
            Hover or tab to a district to preview its race. Nothing is sent or stored.
          </span>
        )}
      </div>
    </div>
  );
}

function DistrictPreview({ state, race }: { state: string; race: RaceWithCandidates }) {
  const top = [...race.candidates]
    .sort((a, b) => (b.contributions ?? 0) - (a.contributions ?? 0))
    .slice(0, 2);
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="text-ink-hi">
        {race.district === 0 ? `${state} at-large` : `${state}-${race.district}`}
      </span>
      <span className="text-ink-min">{formatPvi(race.pvi)}</span>
      {top.map((c) => {
        const major = majorPartyOf(c.party);
        return (
          <span
            key={c.id}
            className={major === "DEM" ? "text-dem-blue" : major === "REP" ? "text-rep-red" : "text-ink-lo"}
          >
            {c.name}
          </span>
        );
      })}
      <span className="text-phos">click to show this race →</span>
    </div>
  );
}
