import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import {
  HOUSE_GAP,
  HOUSE_TILE,
  NO_CONTEST_FILL,
  STATE_TILES,
  TILE_COLS,
  TILE_ROWS,
  binFill,
  formatAsOf,
  houseLayout,
} from "@/lib/electionMap";
import { MAP_LABEL_HALO, raceLabel } from "@/lib/results";
import { overlay } from "@/lib/chartColors";
import type { MapBin, MapChamberKey } from "@/types/electionMap";
import type {
  AccountabilitySeat,
  ChamberAccountability,
  CoverageCell,
  MapAccountability as Data,
} from "@/types/mapAccountability";

const CHAMBERS: MapChamberKey[] = ["senate", "house"];
const CHAMBER_NAME: Record<MapChamberKey, string> = { senate: "Senate", house: "House" };
const TILE = 10;
const GAP = 1;
/** The source line of the held election's check and swing map (archive
 * page), inside every share image of it. */
const ACCOUNTABILITY_SOURCE =
  "Civitas: the pre-election map as frozen on election day; counts from each state's own results system, used once the state lists them official; usual vote from official returns (MEDSL, FEC, VEST, Census); polls: Wikipedia contributors, CC BY-SA 4.0";

const WAITING_STROKE = "var(--ink-lo)";

function pct(share: number | null): string {
  return share == null ? "n/a" : `${Math.round(share * 100)}%`;
}

export function seatLabel(s: AccountabilitySeat): string {
  return raceLabel({
    state: s.state,
    office: s.chamber === "senate" ? "S" : "H",
    district: s.district,
    isSpecial: s.special,
  });
}

/** What a seat with no swing is waiting on, in words. */
const COUNT_WORDS: Record<AccountabilitySeat["count"], string> = {
  official: "official",
  counting: "count not yet official",
  no_count: "no count read",
  not_two_party: "official, but not a Democrat against a Republican",
};

/** "N of M ranged seats have an official count", or, before any, that
 * nothing is graded yet: an empty check is said, never shown as zeros. */
export function statusSentence(data: Data): string {
  const cs = CHAMBERS.map((c) => data.chambers[c]).filter(Boolean) as ChamberAccountability[];
  const ranged = cs.reduce((n, c) => n + c.ranged, 0);
  const certified = cs.reduce((n, c) => n + c.certified, 0);
  if (ranged === 0) return "The map gave no seat a range, so there is nothing to check.";
  if (certified === 0)
    return `No count is official yet: 0 of ${ranged} seats with a range. Each seat is checked once its state lists the count official; until then nothing is graded.`;
  const rest = data.stillCounting
    ? " The rest are checked as their states list them official."
    : certified < ranged
      ? " The rest never had an official Democrat-versus-Republican count Civitas could read."
      : "";
  return `${certified} of ${ranged} seats with a range have an official count.${rest}`;
}

/** One coverage strip: the measured share with its exact interval, and the
 * level the range promised, on a 0 to 100% track. */
function CoverageStrip({
  level,
  cell,
  promised,
}: {
  level: 80 | 95;
  cell: CoverageCell;
  promised: number;
}) {
  const share = cell[`share${level}`];
  const interval = cell[`interval${level}`];
  const held = cell[`held${level}`];
  return (
    <div className="grid grid-cols-1 gap-1 sm:grid-cols-[8.5rem_minmax(0,1fr)] sm:items-center sm:gap-3">
      <span className="font-mono text-xs text-ink-lo">
        {level}% RANGE · {held}/{cell.graded}
      </span>
      <div>
        <div className="relative h-4 border border-white/15 bg-surface-base" aria-hidden="true">
          {interval && (
            <div
              className="absolute inset-y-0 bg-phos/25"
              style={{
                left: `${interval[0] * 100}%`,
                width: `${(interval[1] - interval[0]) * 100}%`,
              }}
            />
          )}
          <div
            className="absolute -inset-y-1 w-px bg-ink-hi"
            style={{ left: `${promised * 100}%` }}
            title={`Promised ${pct(promised)}`}
          />
          {share != null && (
            <div
              className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 bg-phos"
              style={{ left: `${share * 100}%` }}
            />
          )}
        </div>
        <p className="mt-1 text-xs text-ink-lo">
          {share == null
            ? "None checked yet."
            : `${pct(share)} inside (exact 95% interval ${pct(interval?.[0] ?? null)} to ${pct(interval?.[1] ?? null)}); promised ${pct(promised)}.`}
        </p>
      </div>
    </div>
  );
}

// The furthest misses a chamber lists open; the rest are one tap away
// (a House check can miss dozens of seats, every one still listed).
const MISSES_SHOWN = 8;

function MissList({ misses }: { misses: ChamberAccountability["misses"] }) {
  return (
    <ul className="mt-1 space-y-1 text-sm">
      {misses.map((s) => (
        <li key={s.id} className="text-ink">
          <a
            href={`#race-${s.id}`}
            className="underline decoration-white/30 underline-offset-4 hover:text-ink-hi"
          >
            {seatLabel(s)}
          </a>
          <span className="text-ink-lo">
            {": "}
            {s.marginLabel}, range {s.range80Label}
            {s.inside95 === false ? `, outside the 95% range too (${s.range95Label})` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ChamberCheck({ name, c }: { name: string; c: ChamberAccountability }) {
  const held = c.promised.heldOut;
  const fw = c.promised.forward;
  return (
    <div className="space-y-4 border border-white/[0.09] bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-lg font-semibold uppercase text-ink-hi">{name}</h3>
        <span className="font-mono text-xs text-ink-min">
          {c.certified} OF {c.ranged} OFFICIAL · {c.counts.counting} COUNTING · {c.counts.no_count}{" "}
          NO COUNT
        </span>
      </div>
      <div className="space-y-3">
        <CoverageStrip level={80} cell={c.all} promised={c.promised.nominal80} />
        <CoverageStrip level={95} cell={c.all} promised={c.promised.nominal95} />
      </div>
      {held.coverage80 != null && (
        <p className="text-xs text-ink-min">
          What the map said beforehand: on {held.races} past races
          {held.cycles ? ` (${held.cycles[0]} to ${held.cycles[1]})` : ""} it never saw, its 80%
          ranges held {pct(held.coverage80)} and its 95% ranges {pct(held.coverage95)}
          {fw
            ? `; on ${fw.cycle}, fitted on earlier cycles only, ${pct(fw.coverage80)} and ${pct(fw.coverage95)}`
            : ""}
          .
        </p>
      )}
      {c.byBasis.length > 0 && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">{name} ranges checked, by what each range rested on</caption>
          <thead className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-min">
            <tr>
              <th scope="col" className="py-1 pr-3 font-normal">
                Range basis
              </th>
              <th scope="col" className="py-1 pr-3 text-right font-normal">
                Checked
              </th>
              <th scope="col" className="py-1 pr-3 text-right font-normal">
                Inside 80%
              </th>
              <th scope="col" className="py-1 text-right font-normal">
                Inside 95%
              </th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {c.byBasis.map((b) => (
              <tr key={b.basis} className="border-t border-white/[0.07]">
                <th scope="row" className="py-1.5 pr-3 font-sans font-normal text-ink">
                  {b.label}
                </th>
                <td className="py-1.5 pr-3 text-right text-ink-lo">
                  {b.graded} of {b.ranged}
                </td>
                <td className="py-1.5 pr-3 text-right text-ink-hi">
                  {b.graded ? `${b.held80} · ${pct(b.share80)}` : "n/a"}
                </td>
                <td className="py-1.5 text-right text-ink-hi">
                  {b.graded ? `${b.held95} · ${pct(b.share95)}` : "n/a"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div>
        <h4 className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-min">
          Outside the 80% range, furthest first
        </h4>
        {c.misses.length === 0 ? (
          <p className="mt-1 text-sm text-ink-lo">
            {c.all.graded ? "None so far." : "None checked yet."}
          </p>
        ) : (
          <>
            <MissList misses={c.misses.slice(0, MISSES_SHOWN)} />
            {c.misses.length > MISSES_SHOWN && (
              <details className="mt-1">
                <summary className="inline-flex min-h-11 cursor-pointer items-center font-mono text-xs tracking-[0.12em] text-ink-lo hover:text-ink-hi">
                  ALL {c.misses.length} MISSES
                </summary>
                <MissList misses={c.misses.slice(MISSES_SHOWN)} />
              </details>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function tileFill(s: AccountabilitySeat): {
  fill: string;
  stroke: string;
  strokeDasharray?: string;
} {
  if (s.swingBin) return { fill: binFill(s.swingBin as MapBin), stroke: overlay(0.18) };
  return { fill: NO_CONTEST_FILL, stroke: WAITING_STROKE, strokeDasharray: "0.8 0.8" };
}

function tileTitle(s: AccountabilitySeat): string {
  return s.swingLabel
    ? `${seatLabel(s)}: ${s.marginLabel}, usual ${s.usualMarginLabel}, swing ${s.swingLabel}`
    : `${seatLabel(s)}: ${COUNT_WORDS[s.count]}`;
}

function SenateSwing({ seats }: { seats: AccountabilitySeat[] }) {
  const byState = new Map<string, AccountabilitySeat[]>();
  for (const s of seats) byState.set(s.state, [...(byState.get(s.state) ?? []), s]);
  return (
    <svg
      viewBox={`0 0 ${TILE_COLS * (TILE + GAP)} ${TILE_ROWS * (TILE + GAP)}`}
      className="block h-auto w-full"
      aria-hidden="true"
    >
      {Object.entries(STATE_TILES).map(([state, [col, row]]) => {
        const x = col * (TILE + GAP);
        const y = row * (TILE + GAP);
        const list = byState.get(state) ?? [];
        const part = TILE / Math.max(list.length, 1);
        return (
          <g key={state}>
            {list.length === 0 ? (
              <rect x={x} y={y} width={TILE} height={TILE} fill={NO_CONTEST_FILL} />
            ) : (
              list.map((s, i) => (
                <rect
                  key={s.id}
                  x={x + i * part}
                  y={y}
                  width={part}
                  height={TILE}
                  strokeWidth={0.25}
                  {...tileFill(s)}
                >
                  <title>{tileTitle(s)}</title>
                </rect>
              ))
            )}
            <text
              x={x + TILE / 2}
              y={y + TILE / 2 + 1.2}
              textAnchor="middle"
              fontSize="3"
              className={`pointer-events-none font-mono ${MAP_LABEL_HALO} ${list.length ? "fill-ink-hi" : "fill-ink-min/75"}`}
            >
              {state}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function HouseSwing({ seats }: { seats: AccountabilitySeat[] }) {
  const layout = houseLayout(seats);
  const step = HOUSE_TILE + HOUSE_GAP;
  return (
    <svg
      viewBox={`-2 -2 ${layout.width + 4} ${layout.height + 4}`}
      className="block h-auto w-full"
      aria-hidden="true"
    >
      {layout.blocks.map((b) => (
        <g key={b.state}>
          <text x={b.x} y={b.y - 2} fontSize="6" className="fill-ink-min font-mono">
            {b.state}
          </text>
          {b.seats.map((s, i) => (
            <rect
              key={s.id}
              x={b.x + (i % b.cols) * step}
              y={b.y + Math.floor(i / b.cols) * step}
              width={HOUSE_TILE}
              height={HOUSE_TILE}
              strokeWidth={0.3}
              {...tileFill(s)}
            >
              <title>{tileTitle(s)}</title>
            </rect>
          ))}
        </g>
      ))}
    </svg>
  );
}

function SwingLegend({ bins }: { bins: Data["bins"] }) {
  const lo = bins.leansMax;
  const hi = bins.usuallyMax;
  const cells: { bin: MapBin; label: string }[] = [
    { bin: "D_strongly", label: `D ${hi}+` },
    { bin: "D_usually", label: `D ${lo}–${hi}` },
    { bin: "D_leans", label: `D <${lo}` },
    { bin: "close", label: "AS USUAL" },
    { bin: "R_leans", label: `R <${lo}` },
    { bin: "R_usually", label: `R ${lo}–${hi}` },
    { bin: "R_strongly", label: `R ${hi}+` },
  ];
  return (
    <div className="space-y-2">
      <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-min">
        Ran toward, against its usual vote (margin points)
      </p>
      <ul className="grid grid-cols-4 gap-1 font-mono text-[11px] text-ink-lo sm:grid-cols-8">
        {cells.map((c) => (
          <li key={c.bin} className="flex items-center gap-1.5">
            <span
              className="inline-block h-3 w-4 shrink-0"
              style={{ backgroundColor: binFill(c.bin) }}
              aria-hidden="true"
            />
            {c.label}
          </li>
        ))}
        <li className="col-span-4 flex items-center gap-1.5 sm:col-span-1">
          <span
            className="inline-block h-3 w-4 shrink-0 border border-dashed"
            style={{ borderColor: WAITING_STROKE, backgroundColor: NO_CONTEST_FILL }}
            aria-hidden="true"
          />
          NO OFFICIAL COUNT
        </li>
      </ul>
    </div>
  );
}

function swingSentence(name: string, c: ChamberAccountability): string {
  const sw = c.swing;
  if (!sw.seats) return `${name}: no seat has an official Democrat-versus-Republican count yet.`;
  return `${name}: across ${sw.seats} ${sw.seats === 1 ? "seat" : "seats"} with an official count, the median seat ran ${sw.medianLabel} against its usual vote (mean ${sw.meanLabel}).`;
}

function SwingTable({ seats }: { seats: AccountabilitySeat[] }) {
  const rows = seats
    .filter((s) => s.swing != null)
    .sort((a, b) => Math.abs(b.swing!) - Math.abs(a.swing!));
  if (!rows.length) return null;
  return (
    <details className="mt-3" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
      <summary className="inline-flex min-h-11 cursor-pointer items-center font-mono text-xs tracking-[0.12em] text-ink-lo hover:text-ink-hi">
        TABLE · {rows.length} SEATS, LARGEST SWING FIRST
      </summary>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[30rem] text-left text-sm">
          <thead className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-min">
            <tr>
              <th scope="col" className="py-1 pr-3 font-normal">
                Seat
              </th>
              <th scope="col" className="py-1 pr-3 text-right font-normal">
                Usual vote, as a margin
              </th>
              <th scope="col" className="py-1 pr-3 text-right font-normal">
                Official margin
              </th>
              <th scope="col" className="py-1 text-right font-normal">
                Swing
              </th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {rows.map((s) => (
              <tr key={s.id} className="border-t border-white/[0.07]">
                <th scope="row" className="py-1.5 pr-3 font-sans font-normal text-ink">
                  <a href={`#race-${s.id}`} className="underline decoration-white/30">
                    {seatLabel(s)}
                  </a>
                </th>
                <td className="py-1.5 pr-3 text-right text-ink-lo">{s.usualMarginLabel}</td>
                <td className="py-1.5 pr-3 text-right text-ink-hi">{s.marginLabel}</td>
                <td className="py-1.5 text-right text-ink-hi">{s.swingLabel}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/**
 * The held election's map against its count (/elections/{cycle}): whether
 * each official margin fell inside the 80% and 95% ranges the frozen map
 * published, per chamber and range basis, beside what the map promised; and
 * the swing map, each seat's official margin minus its usual vote. Every
 * figure is the backend's (GET /api/elections/archive/{cycle}/accountability);
 * a count not yet official is shown as waiting, never graded, and no race is
 * called.
 */
export default function MapAccountability({ data, pageUrl }: { data: Data; pageUrl: string }) {
  const chambers = CHAMBERS.filter((c) => data.chambers[c]);
  const asOf = formatAsOf(data.mapAsOf);
  return (
    <ShareSubjectProvider
      subject={{
        title: `${data.cycle} election: the map against the count`,
        subtitle: `Map frozen ${asOf} · Official counts only · Not a race call`,
        url: pageUrl,
        source: ACCOUNTABILITY_SOURCE,
        asOf: data.mapAsOf,
      }}
    >
      <section
        id="map-check"
        aria-labelledby="map-check-title"
        className="space-y-4 border border-phos/20 bg-surface-base p-3 sm:p-4"
        {...{ [SHARE_SECTION_ATTR]: "map-check" }}
        {...shareMetaAttrs({ source: ACCOUNTABILITY_SOURCE, asOf: data.mapAsOf })}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id="map-check-title"
              className="font-mono text-sm uppercase tracking-[0.14em] text-ink-hi"
            >
              Did the map&apos;s ranges hold?
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-ink-lo">
              Each official margin against the 80% and 95% ranges the map published before the
              election, as it stood on {asOf}. An honest 80% range holds about 8 results in 10; the
              band is the exact 95% interval for the share measured, the tick what the range
              promised. Misses are listed, not hidden.
            </p>
          </div>
          <ShareSectionButton label="Did the map's ranges hold?" anchor="map-check" />
        </div>
        <p
          className={`border px-3 py-2 text-sm ${data.stillCounting ? "border-signal-amber/40 text-ink-hi" : "border-white/[0.09] text-ink-lo"}`}
        >
          {statusSentence(data)}
        </p>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {chambers.map((c) => (
            <ChamberCheck key={c} name={CHAMBER_NAME[c]} c={data.chambers[c]!} />
          ))}
        </div>
        <p className="border-t border-white/[0.07] pt-2 font-mono text-[11px] leading-relaxed text-ink-min">
          {data.units.margin} Source: {ACCOUNTABILITY_SOURCE}.
        </p>
      </section>

      <section
        id="swing-map"
        aria-labelledby="swing-map-title"
        className="space-y-4 border border-phos/20 bg-surface-base p-3 sm:p-4"
        {...{ [SHARE_SECTION_ATTR]: "swing-map" }}
        data-share-fixed-layout=""
        {...shareMetaAttrs({ source: ACCOUNTABILITY_SOURCE, asOf: data.mapAsOf })}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id="swing-map-title"
              className="font-mono text-sm uppercase tracking-[0.14em] text-ink-hi"
            >
              Swing from the usual vote
            </h2>
            <p className="mt-1 max-w-3xl text-sm text-ink-lo">
              Each seat&apos;s official margin minus its usual vote, measured on the same scale: a
              lean is doubled to read as a margin, the margin the seat gives when the country splits
              evenly. A measurement of the count, not a call.
            </p>
          </div>
          <ShareSectionButton label="Swing from the usual vote" anchor="swing-map" />
        </div>
        {data.stillCounting && (
          <p className="font-mono text-xs text-signal-amber">
            STILL COUNTING · SEATS FILL IN AS THEIR STATES LIST THE COUNT OFFICIAL
          </p>
        )}
        {chambers.map((c) => {
          const ch = data.chambers[c]!;
          return (
            <div key={c} className="space-y-2">
              <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-hi">
                {CHAMBER_NAME[c]}
              </h3>
              <p className="text-sm text-ink-lo">{swingSentence(CHAMBER_NAME[c], ch)}</p>
              <div
                className="mx-auto max-w-[38rem]"
                role="img"
                aria-label={`${CHAMBER_NAME[c]} swing map. ${swingSentence(CHAMBER_NAME[c], ch)} The table below lists every seat.`}
              >
                {c === "senate" ? (
                  <SenateSwing seats={ch.seats} />
                ) : (
                  <HouseSwing seats={ch.seats} />
                )}
              </div>
              <SwingTable seats={ch.seats} />
            </div>
          );
        })}
        <SwingLegend bins={data.bins} />
        <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
          <a
            href={`/api/elections/archive/${data.cycle}/swing.csv`}
            className="inline-flex min-h-11 items-center border border-white/20 px-4 font-mono text-xs tracking-[0.12em] text-ink-lo hover:text-ink-hi"
          >
            CSV · SWING AND RANGES
          </a>
        </div>
        <p className="border-t border-white/[0.07] pt-2 font-mono text-[11px] leading-relaxed text-ink-min">
          {data.units.lean} {data.units.margin} Source: {ACCOUNTABILITY_SOURCE}.
        </p>
      </section>
    </ShareSubjectProvider>
  );
}
