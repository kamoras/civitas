"use client";

import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  decodeArcs,
  districtsAt,
  inPolygons,
  linesOf,
  loadDistrictLookup,
  polygonsOf,
  type DistrictsAtPoint,
  type LookupKind,
  type Point,
  type Ring,
  type Topology,
} from "@/lib/districtAt";
import { stateFromPoint } from "@/lib/stateFromPoint";
import { districtAreaLabel, placeName, placesNamed, stateBallotHref } from "@/lib/elections";
import { STATE_NAMES } from "@/lib/stateCodes";

/** What a found location or a tapped part of a town settles. */
export interface FoundDistricts extends DistrictsAtPoint {
  state: string;
}

interface HouseDistrict {
  district: number;
  places?: string[] | null;
}

const BUTTON =
  "inline-flex min-h-11 items-center border border-white/15 px-3 font-mono text-xs uppercase tracking-[0.12em] text-ink-hi hover:border-phos/40 hover:text-phos disabled:opacity-70";
const INLINE_LINK = "underline underline-offset-2 hover:text-phos";

// A fix this loose can't settle a street near a district line: say so.
const LOOSE_FIX_M = 500;

// One fetch per file per page view, shared by the button and the map.
const files = new Map<string, Promise<Topology | null>>();
function lookup(
  lines: string,
  state: string,
  kind: LookupKind = "house"
): Promise<Topology | null> {
  const key = `${lines}/${state}/${kind}`;
  if (!files.has(key)) {
    files.set(
      key,
      loadDistrictLookup(lines, state, kind).catch(() => {
        files.delete(key);
        return null;
      })
    );
  }
  return files.get(key)!;
}

/** Test hook: forget the fetched files. */
export function __resetDistrictLookupCache() {
  files.clear();
}

function districtWord(d: number): string {
  return d === 0 ? "the at-large district" : `district ${d}`;
}

/**
 * The two private ways to settle a district a town name can't (AGENTS.md
 * §8): "Find my district from my location", placed on this device against the site's own
 * district shapes, and, for a town split between districts, a map of just
 * that town where the reader taps their part. Plus the official lookup,
 * worded so the reader knows that site would get their address.
 *
 * Nothing leaves the page. The location button runs only on a click; the
 * position goes to stateFromPoint and districtsAt and no further, and only
 * the districts found are handed to the page (which keeps the House one
 * for this tab at most). The map's taps are the same: a district, never a
 * point.
 *
 * `lines` names the map ("congress-119" for who represents the reader now,
 * "election-2026" for the ballot) and `linesLabel` says it in words; the
 * page passes the districts on that same map, so a town's split and its
 * shapes always describe the same lines.
 */
export default function DistrictLocator({
  state,
  lines,
  linesLabel,
  districts,
  query,
  onFound,
  onOtherState,
  officialLookup,
  legislature = false,
  houseGov = true,
}: {
  state: string;
  lines: string;
  linesLabel: string;
  districts: HouseDistrict[];
  /** What the reader typed in the town filter. */
  query: string;
  onFound: (found: FoundDistricts, how: "location" | "map") => void;
  /** A location in another state: the page switches to it, or, without
   * this, the reader is offered that state's ballot page. */
  onOtherState?: (state: string) => void;
  /** The state's own lookup (its voter or legislative lookup), when the
   * page has one. */
  officialLookup?: { href: string; label: string } | null;
  /** Report the state legislative districts, not the House one. */
  legislature?: boolean;
  /** Link house.gov's lookup. Not on a ballot on new lines: it answers by
   * the sitting member, on the old map. */
  houseGov?: boolean;
}) {
  const [status, setStatus] = useState<ReactNode>("");
  const [locating, setLocating] = useState(false);
  const [mapFor, setMapFor] = useState<string | null>(null);
  const split = placesNamed(districts, query).filter((p) => p.districts.length > 1);
  const stateName = STATE_NAMES[state] ?? state;

  const locate = () => {
    if (!("geolocation" in navigator)) {
      setStatus("This browser can't share a location. Use the town filter or the map instead.");
      return;
    }
    setLocating(true);
    setStatus("Finding your district on this device…");
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        const { longitude: lon, latitude: lat, accuracy } = coords;
        let message: ReactNode;
        try {
          const st = await stateFromPoint(lon, lat);
          if (!st) {
            message = "That location isn't in a U.S. state.";
          } else if (st !== state && !onOtherState) {
            message = (
              <>
                That location is in {STATE_NAMES[st] ?? st}, not {stateName}.{" "}
                <Link href={stateBallotHref(st)} className={INLINE_LINK}>
                  Go to {STATE_NAMES[st] ?? st}&apos;s ballot
                </Link>
                .
              </>
            );
          } else {
            const topo = await lookup(lines, st, legislature ? "legislature" : "house");
            const found = topo ? districtsAt(topo, lon, lat) : null;
            if (!found || (legislature ? !found.sldu && !found.sldl : found.house == null)) {
              message = topo
                ? "That location isn't inside any district on this map (open water, perhaps). Use the town filter instead."
                : "District shapes for this map aren't available yet. Use the town filter instead.";
            } else {
              if (st !== state) onOtherState?.(st);
              onFound({ ...found, state: st }, "location");
              const loose =
                accuracy > LOOSE_FIX_M
                  ? ` Your browser placed you only to within about ${
                      accuracy >= 1000
                        ? `${Math.round(accuracy / 1000)} km`
                        : `${Math.round(accuracy)} m`
                    }, so if you live near a district line, check with the map or the official lookup.`
                  : "";
              const where = legislature
                ? [
                    found.sldu && `state senate district ${found.sldu}`,
                    found.sldl && `state house district ${found.sldl}`,
                  ]
                    .filter(Boolean)
                    .join(" and ")
                : districtWord(found.house!);
              message = `Your location is in ${where}${
                st !== state ? ` of ${STATE_NAMES[st] ?? st}` : ""
              }, on ${linesLabel}.${loose}`;
            }
          }
        } catch {
          message = "Couldn't work out your district. Use the town filter instead.";
        }
        setLocating(false);
        setStatus(message);
      },
      () => {
        setLocating(false);
        setStatus("Location not available. Use the town filter or the map instead.");
      },
      // A district needs a street-level fix, not the coarse one a state
      // does; a minute-old one is fine.
      { enableHighAccuracy: true, maximumAge: 60_000, timeout: 20_000 }
    );
  };

  return (
    <div className="mt-3" data-district-locator>
      {split.length > 0 && (
        <p className="mb-2 text-sm text-ink-lo">
          To tell which part of {split.map((p) => p.name).join(" or ")} is yours, use your location
          or tap your part of the town on its map. Either way it is worked out on this device and
          nothing is sent.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={locate} disabled={locating} className={BUTTON}>
          Find my district from my location
        </button>
        {split.map((p) => (
          <button
            key={p.fullName}
            type="button"
            aria-expanded={mapFor === p.fullName}
            onClick={() => setMapFor(mapFor === p.fullName ? null : p.fullName)}
            className={BUTTON}
          >
            {mapFor === p.fullName ? `Hide ${p.name}'s map` : `Show ${p.name} on a map`}
          </button>
        ))}
      </div>
      <p aria-live="polite" data-locator-status className="mt-2 text-sm text-ink-lo">
        {status}
      </p>
      {split
        .filter((p) => p.fullName === mapFor)
        .map((p) => (
          <TownMap
            key={p.fullName}
            state={state}
            lines={lines}
            town={p.fullName}
            label={p.name}
            splitDistricts={p.districts.map(Number)}
            districts={districts}
            onPick={(house) => {
              onFound({ state, house, sldu: null, sldl: null }, "map");
              setStatus(`You picked ${districtWord(house)} for your part of ${p.name}.`);
            }}
          />
        ))}
      {((houseGov && !legislature) || officialLookup) && (
        <p className="mt-2 text-xs text-ink-min">
          For an exact answer from your street address,{" "}
          {houseGov && !legislature && (
            <a
              href="https://www.house.gov/representatives/find-your-representative"
              target="_blank"
              rel="noopener noreferrer"
              className={INLINE_LINK}
            >
              house.gov&apos;s Find Your Representative
            </a>
          )}
          {officialLookup ? (
            <>
              {houseGov && !legislature ? " or " : ""}
              <a
                href={officialLookup.href}
                target="_blank"
                rel="noopener noreferrer"
                className={INLINE_LINK}
              >
                {officialLookup.label}
              </a>
            </>
          ) : null}{" "}
          will look it up. You would be giving your address to that site, not to Civitas.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The split town's map
// ---------------------------------------------------------------------------

// Fills told apart by colour and by the number printed in each part.
const FILLS = ["var(--signal-cyan)", "var(--signal-amber)", "var(--signal-magenta)", "var(--phos)"];
const VIEW_W = 320;
// Grid used to find a point inside each part for its number, and to say
// which roads cross it.
const LABEL_GRID = 40;
const MAX_ROAD_LABELS = 6;
// Viewbox units (the map is VIEW_W wide) between two road labels.
const LABEL_GAP = 40;

interface Part {
  district: number;
  polygons: Ring[][];
  label: Point | null;
  roads: string[];
}

function bounds(rings: Ring[]): [number, number, number, number] {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const r of rings)
    for (const [x, y] of r) {
      x0 = Math.min(x0, x);
      y0 = Math.min(y0, y);
      x1 = Math.max(x1, x);
      y1 = Math.max(y1, y);
    }
  return [x0, y0, x1, y1];
}

/**
 * Just the town: its outline, each district's part of it (the district
 * shapes clipped to the town by the browser), its main roads, and a
 * number in each part. Tapping a part picks that district; the list under
 * the map is the same choice for a keyboard or a screen reader, each part
 * described by the roads that cross it and the district's other places,
 * so the map itself is hidden from assistive technology rather than read
 * twice.
 */
function TownMap({
  state,
  lines,
  town,
  label,
  splitDistricts,
  districts,
  onPick,
}: {
  state: string;
  lines: string;
  town: string;
  label: string;
  splitDistricts: number[];
  districts: HouseDistrict[];
  onPick: (district: number) => void;
}) {
  const clipId = useId();
  const [topo, setTopo] = useState<{ house: Topology; towns: Topology } | null | undefined>(
    undefined
  );
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    // The district shapes and the town's outline and roads, from two files.
    Promise.all([lookup(lines, state), lookup(lines, state, "towns")]).then(
      ([house, towns]) => live && setTopo(house && towns ? { house, towns } : null)
    );
    return () => {
      live = false;
    };
  }, [lines, state]);

  const view = useMemo(() => {
    if (!topo) return null;
    const arcs = decodeArcs(topo.towns);
    const houseArcs = decodeArcs(topo.house);
    const townPolys = (topo.towns.objects.towns?.geometries ?? [])
      .filter((g) => g.properties?.n === town)
      .flatMap((g) => polygonsOf(arcs, g));
    if (!townPolys.length) return null;
    const [x0, y0, x1, y1] = bounds(townPolys.flat());
    const k = Math.cos((((y0 + y1) / 2) * Math.PI) / 180);
    const scale = VIEW_W / Math.max((x1 - x0) * k, 1e-9);
    const height = Math.max(120, Math.min(420, (y1 - y0) * scale));
    const sy = height / Math.max(y1 - y0, 1e-9);
    const s = Math.min(scale, sy);
    const project = ([x, y]: Point) => [(x - x0) * k * s, (y1 - y) * s] as Point;
    const path = (polys: Ring[][]) =>
      polys
        .flat()
        .map(
          (r) =>
            "M" +
            r
              .map((p) =>
                project(p)
                  .map((v) => v.toFixed(1))
                  .join(" ")
              )
              .join("L") +
            "Z"
        )
        .join("");
    const roads = (topo.towns.objects.roads?.geometries ?? []).flatMap((g) =>
      linesOf(arcs, g)
        .filter((l) => l.some(([x, y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1))
        .map((l) => ({ name: g.properties?.n ?? "", line: l }))
    );
    const inTown = (p: Point) => inPolygons(p, townPolys);
    const parts: Part[] = splitDistricts.map((d) => {
      const polygons = (topo.house.objects.house?.geometries ?? [])
        .filter((g) => Number(g.properties?.d) === d)
        .flatMap((g) => polygonsOf(houseArcs, g));
      const inside: Point[] = [];
      for (let i = 0; i < LABEL_GRID; i++)
        for (let j = 0; j < LABEL_GRID; j++) {
          const p: Point = [
            x0 + ((i + 0.5) * (x1 - x0)) / LABEL_GRID,
            y0 + ((j + 0.5) * (y1 - y0)) / LABEL_GRID,
          ];
          if (inTown(p) && inPolygons(p, polygons)) inside.push(p);
        }
      // The inside grid point furthest from the part's edge (from any
      // grid point outside it), so the number sits in the part's body.
      const outside: Point[] = [];
      for (let i = 0; i < LABEL_GRID; i++)
        for (let j = 0; j < LABEL_GRID; j++) {
          const p: Point = [
            x0 + ((i + 0.5) * (x1 - x0)) / LABEL_GRID,
            y0 + ((j + 0.5) * (y1 - y0)) / LABEL_GRID,
          ];
          if (!inside.some((q) => q[0] === p[0] && q[1] === p[1])) outside.push(p);
        }
      const room = (p: Point) =>
        Math.min(...outside.map((q) => Math.hypot((p[0] - q[0]) * k, p[1] - q[1])));
      const labelAt = inside.length
        ? inside
            .map((p) => [p, room(p)] as const)
            .reduce((best, c) => (c[1] > best[1] ? c : best))[0]
        : null;
      const roadNames = [
        ...new Set(
          roads
            .filter((r) => r.name && r.line.some((p) => inTown(p) && inPolygons(p, polygons)))
            .map((r) => r.name)
        ),
      ];
      return { district: d, polygons, label: labelAt, roads: roadNames.slice(0, 4) };
    });
    // Each road named once, at the middle of its longest piece.
    const longest = new Map<string, Ring>();
    for (const r of roads) {
      if (r.name && r.line.length > (longest.get(r.name)?.length ?? 0)) longest.set(r.name, r.line);
    }
    return {
      height,
      width: (x1 - x0) * k * s,
      townPath: path(townPolys),
      parts: parts.map((p) => ({ ...p, path: path(p.polygons), at: p.label && project(p.label) })),
      roads: roads.map((r) => ({
        name: r.name,
        d:
          "M" +
          r.line
            .map((p) =>
              project(p)
                .map((v) => v.toFixed(1))
                .join(" ")
            )
            .join("L"),
      })),
      // The longest few only: a city's dozens of names would bury the map.
      roadLabels: [...longest]
        .filter(([, line]) => inTown(line[Math.floor(line.length / 2)]))
        .sort((a, b) => b[1].length - a[1].length)
        .map(([name, line]) => ({ name, at: project(line[Math.floor(line.length / 2)]) }))
        // No label on top of another.
        .reduce<{ name: string; at: Point }[]>(
          (kept, l) =>
            kept.length < MAX_ROAD_LABELS &&
            kept.every((o) => Math.hypot(o.at[0] - l.at[0], o.at[1] - l.at[1]) > LABEL_GAP)
              ? [...kept, l]
              : kept,
          []
        ),
    };
  }, [topo, town, splitDistricts]);

  if (topo === undefined) return <p className="mt-2 text-xs text-ink-min">Loading the map…</p>;
  if (!view)
    return (
      <p className="mt-2 text-xs text-ink-min">
        No map of {label} is available on these lines. Use your location or the official lookup.
      </p>
    );

  const pick = (d: number) => {
    setPicked(d);
    onPick(d);
  };
  const otherPlaces = (d: number) =>
    districtAreaLabel(
      (districts.find((x) => x.district === d)?.places ?? [])
        .filter((p) => p !== town)
        .map(placeName),
      3,
      false
    );

  return (
    <figure className="mt-3 max-w-md border border-white/15 bg-surface p-3">
      <svg
        viewBox={`-4 -4 ${view.width + 8} ${view.height + 8}`}
        className="h-auto w-full"
        aria-hidden="true"
      >
        <defs>
          <clipPath id={clipId}>
            <path d={view.townPath} />
          </clipPath>
        </defs>
        <g clipPath={`url(#${clipId})`}>
          {view.parts.map((p, i) => (
            <path
              key={p.district}
              d={p.path}
              data-district={p.district}
              onClick={() => pick(p.district)}
              style={{ fill: FILLS[i % FILLS.length], cursor: "pointer" }}
              fillOpacity={picked === p.district ? 0.55 : 0.25}
              fillRule="evenodd"
            />
          ))}
          {view.roads.map((r, i) => (
            <path
              key={i}
              d={r.d}
              fill="none"
              stroke="var(--ink-lo)"
              strokeWidth={1}
              pointerEvents="none"
            />
          ))}
        </g>
        <path
          d={view.townPath}
          fill="none"
          stroke="var(--ink-hi)"
          strokeWidth={1.5}
          pointerEvents="none"
        />
        {view.roadLabels.map((r) => (
          <text
            key={r.name}
            x={r.at[0]}
            y={r.at[1]}
            fontSize={8}
            fill="var(--ink-lo)"
            pointerEvents="none"
            className="font-mono"
          >
            {r.name}
          </text>
        ))}
        {view.parts.map(
          (p) =>
            p.at && (
              <text
                key={p.district}
                x={p.at[0]}
                y={p.at[1]}
                fontSize={14}
                textAnchor="middle"
                dominantBaseline="middle"
                fill="var(--ink-hi)"
                pointerEvents="none"
                className="font-mono font-semibold"
              >
                {p.district}
              </text>
            )
        )}
      </svg>
      <figcaption className="mt-2 text-xs text-ink-min">
        {label}, drawn from the Census Bureau&apos;s district and town lines. Tap your part, or pick
        it below.
      </figcaption>
      <ul className="mt-2 space-y-1">
        {view.parts.map((p, i) => {
          const others = otherPlaces(p.district);
          return (
            <li key={p.district}>
              <button
                type="button"
                aria-pressed={picked === p.district}
                onClick={() => pick(p.district)}
                className="flex min-h-11 w-full items-start gap-2 border border-white/[0.09] px-2 py-1.5 text-left text-sm hover:border-phos/40"
              >
                <span
                  aria-hidden="true"
                  className="mt-1 inline-block h-3 w-3 shrink-0"
                  style={{ background: FILLS[i % FILLS.length], opacity: 0.6 }}
                />
                <span>
                  <span className="font-semibold text-ink-hi">
                    District {p.district === 0 ? "at large" : p.district}
                  </span>
                  <span className="text-ink-lo">
                    : the part of {label}
                    {p.roads.length ? ` along ${p.roads.join(", ")}` : ""}
                    {others ? `; the district also covers ${others}` : ""}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </figure>
  );
}
