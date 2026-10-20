"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import { MEASURES_HREF } from "@/lib/routes";
import Footer from "@/components/layout/Footer";
import { axisMax, fmt } from "@/components/scorecard/WhereTheyStand";
import { STATE_NAMES } from "@/lib/stateCodes";
import type { ChamberMeasure, ChamberMeasures, ChamberMember, ChamberPoint } from "@/types/peers";

const PARTY_FILL: Record<string, string> = {
  D: "var(--dem-blue)",
  R: "var(--rep-red)",
};

/** A member's mark: D a circle, R a square, anyone else a diamond, so
 *  party never rests on colour alone (dem and rep are 1.2:1 apart). */
function Mark({ party, size = 7 }: { party: string; size?: number }) {
  const fill = PARTY_FILL[party] ?? "var(--ind-purple)";
  const r = size / 2;
  return (
    <svg width={size} height={size} aria-hidden="true" className="inline-block">
      {party === "D" ? (
        <circle cx={r} cy={r} r={r} fill={fill} />
      ) : party === "R" ? (
        <rect width={size} height={size} fill={fill} />
      ) : (
        <polygon points={`${r},0 ${size},${r} ${r},${size} 0,${r}`} fill={fill} />
      )}
    </svg>
  );
}

const JITTER = [10, 4, 16, 7, 13, 1, 19, 10, 6, 14];

function seatName(p: { state: string; district: number | null }, house: boolean): string {
  if (!house) return p.state;
  return `${p.state}-${p.district == null || p.district === 0 ? "AL" : p.district}`;
}

function Callout({
  title,
  points,
  house,
}: {
  title: string;
  points: ChamberPoint[];
  house: boolean;
}) {
  if (points.length === 0) return null;
  return (
    <div>
      <p className="text-[13px] font-semibold text-ink-lo">{title}</p>
      <ul className="mt-1 text-sm">
        {points.map((p) => (
          <li key={p.id}>
            <Link
              href={`/politicians/${p.id}`}
              className="text-ink-hi underline-offset-2 hover:underline"
            >
              {p.name}
            </Link>{" "}
            <span className="text-ink-lo">
              {p.party}-{seatName(p, house)} · {p.shown}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ChamberStrip({ m, house }: { m: ChamberMeasure; house: boolean }) {
  const max = axisMax({
    unit: m.unit,
    distribution: m.points.map((p) => p.value),
    value: null,
    expected: m.median,
  });
  const at = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <section
      aria-labelledby={`m-${m.key}`}
      className="border-t border-white/25 px-4 py-5 first:border-t-0 sm:px-6"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={`m-${m.key}`} className="font-display text-[22px] font-semibold text-ink-hi">
          {m.label}
        </h2>
        <p className="text-sm text-ink-lo">
          {m.median != null && <>Median {fmt(m, m.median)} · </>}
          {m.n} members ·{" "}
          <a
            href={m.source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-phos underline underline-offset-2"
          >
            {m.source.name} ↗
          </a>
        </p>
      </div>
      <p className="max-w-prose text-sm text-ink">{m.why}</p>
      <div aria-hidden="true" className="relative mt-2 h-14">
        <div className="absolute inset-x-0 top-[30px] h-px bg-white/40" />
        {m.points.map((p, i) => (
          <span
            key={p.id}
            title={`${p.name} (${p.party}-${seatName(p, house)}): ${p.shown}`}
            className="absolute -translate-x-1/2"
            style={{ left: at(p.value), top: `${14 + JITTER[i % JITTER.length]}px` }}
          >
            <Mark party={p.party} />
          </span>
        ))}
        {m.median != null && (
          <span
            className="absolute top-1 h-12 -translate-x-1/2 border-l-2 border-ink-hi"
            style={{ left: at(m.median) }}
          />
        )}
      </div>
      <div aria-hidden="true" className="flex justify-between text-xs text-ink-min">
        <span>{fmt(m, 0)}</span>
        <span>{fmt(m, max)}</span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Callout title="Furthest above expected" points={m.mostAbove} house={house} />
        <Callout title="Furthest below expected" points={m.mostBelow} house={house} />
      </div>
    </section>
  );
}

type SortKey =
  "name" | "state" | "smallGifts" | "pacMoney" | "breaks" | "billsPastCommittee" | "passed" | "law";

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "name", label: "Member", numeric: false },
  { key: "state", label: "Seat", numeric: false },
  { key: "breaks", label: "Votes against party / party-line votes", numeric: true },
  { key: "pacMoney", label: "From PACs", numeric: true },
  { key: "smallGifts", label: "From small donors", numeric: true },
  { key: "billsPastCommittee", label: "Bills past committee", numeric: true },
  { key: "passed", label: "Passed", numeric: true },
  { key: "law", label: "Law", numeric: true },
];

/** Seat-lean bands for the House filter, from the seat's Cook PVI. The
 *  bands are conventions: 10 points either way is a safe seat in Cook's
 *  own usage, and within 3 is "even" here. */
const LEANS: { key: string; label: string; test: (pvi: number) => boolean }[] = [
  { key: "safe-d", label: "Safe D (D+10 or more)", test: (p) => p <= -10 },
  { key: "lean-d", label: "Leans D (D+4 to D+9)", test: (p) => p <= -4 && p > -10 },
  { key: "even", label: "Even (within 3)", test: (p) => Math.abs(p) <= 3 },
  { key: "lean-r", label: "Leans R (R+4 to R+9)", test: (p) => p >= 4 && p < 10 },
  { key: "safe-r", label: "Safe R (R+10 or more)", test: (p) => p >= 10 },
];

function pct(v: number | null): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

function sortValue(m: ChamberMember, key: SortKey): string | number | null {
  if (key === "breaks") return m.partyVotes ? (m.breaks ?? 0) / m.partyVotes : null;
  if (key === "state") return `${m.state}${String(m.district ?? 0).padStart(2, "0")}`;
  return m[key];
}

const SELECT = "min-h-11 border border-white/40 bg-surface px-2 text-base text-ink-hi";

function FactTable({ members, house }: { members: ChamberMember[]; house: boolean }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "name", dir: 1 });
  const [state, setState] = useState("");
  const [party, setParty] = useState("");
  const [lean, setLean] = useState("");
  const states = useMemo(() => [...new Set(members.map((m) => m.state))].sort(), [members]);
  const shown = useMemo(() => {
    const band = LEANS.find((l) => l.key === lean);
    const rows = members.filter(
      (m) =>
        (!state || m.state === state) &&
        (!party || (party === "I" ? !["D", "R"].includes(m.party) : m.party === party)) &&
        (!band || (m.lean != null && band.test(m.lean)))
    );
    return [...rows].sort((a, b) => {
      const x = sortValue(a, sort.key);
      const y = sortValue(b, sort.key);
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [members, state, party, lean, sort]);

  return (
    <section aria-labelledby="facts-heading" className="mt-10">
      <h2
        id="facts-heading"
        className="border-t-[3px] border-ink-hi pt-3 font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]"
      >
        Every member, the plain facts
      </h2>
      <p className="mt-1 text-sm text-ink-lo">
        Alphabetical by default. Sort by any column: a sort is a fact, not a rank.
      </p>
      <div className="mt-3 flex flex-wrap gap-3">
        <label className="flex flex-col text-sm text-ink-lo">
          State
          <select value={state} onChange={(e) => setState(e.target.value)} className={SELECT}>
            <option value="">Every state</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {STATE_NAMES[s] ?? s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm text-ink-lo">
          Party
          <select value={party} onChange={(e) => setParty(e.target.value)} className={SELECT}>
            <option value="">Every party</option>
            <option value="D">Democrats</option>
            <option value="R">Republicans</option>
            <option value="I">Independents</option>
          </select>
        </label>
        {house && (
          <label className="flex flex-col text-sm text-ink-lo">
            Seat lean (Cook PVI)
            <select value={lean} onChange={(e) => setLean(e.target.value)} className={SELECT}>
              <option value="">Every seat</option>
              {LEANS.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="mt-2 text-sm text-ink-lo" aria-live="polite">
        {shown.length} of {members.length} members
      </p>
      <div className="mt-2 overflow-x-auto border border-white/25 bg-surface">
        <table className="w-full min-w-[44rem] text-sm">
          <thead>
            <tr className="border-b border-white/25 text-left">
              {COLUMNS.map((c) => {
                const active = sort.key === c.key;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                    className={`px-3 py-1 font-semibold text-ink-lo ${c.numeric ? "text-right" : ""}`}
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setSort((s) => ({
                          key: c.key,
                          dir: s.key === c.key ? (-s.dir as 1 | -1) : c.numeric ? -1 : 1,
                        }))
                      }
                      className="min-h-11 w-full text-inherit hover:text-ink-hi"
                    >
                      {c.label}
                      <span aria-hidden="true">{active ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((m) => (
              <tr key={m.id} className="border-t border-white/10">
                <td className="px-3 py-1.5">
                  <Mark party={m.party} />{" "}
                  <Link
                    href={`/politicians/${m.id}`}
                    className="text-ink-hi underline-offset-2 hover:underline"
                  >
                    {m.name}
                  </Link>
                </td>
                <td className="px-3 py-1.5 text-ink-lo">
                  {m.party}-{seatName(m, house)}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {m.partyVotes == null ? "—" : `${m.breaks ?? 0} / ${m.partyVotes}`}
                </td>
                <td className="px-3 py-1.5 text-right">{pct(m.pacMoney)}</td>
                <td className="px-3 py-1.5 text-right">{pct(m.smallGifts)}</td>
                <td className="px-3 py-1.5 text-right">{m.billsPastCommittee ?? "—"}</td>
                <td className="px-3 py-1.5 text-right">{m.passed ?? "—"}</td>
                <td className="px-3 py-1.5 text-right">{m.law ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const TAB_ON = "border-ink-hi bg-ink-hi text-surface-base";
const TAB_OFF = "border-ink-hi bg-surface text-ink-hi hover:bg-white/5";

/**
 * The chamber measure by measure, in place of the leaderboard: four strips
 * of every member (shapes by party), each with its median and the members
 * furthest from what their seat predicts, then one table of plain facts.
 * No rank, no score and no default order by any judgment. Every figure
 * comes from the API (backend app/peer_comparison.chamber_measures).
 */
export default function MeasuresClient({
  chamber,
  data,
}: {
  chamber: "senate" | "house";
  data: ChamberMeasures | null;
}) {
  const house = chamber === "house";
  const name = house ? "The House" : "The Senate";
  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main
        id="main-content"
        tabIndex={-1}
        className="px-4 pb-16 pt-[var(--header-clearance)] font-sans"
      >
        <div className="mx-auto max-w-6xl">
          <h1 className="font-display text-[30px] font-semibold leading-tight sm:text-[44px]">
            {name}, measure by measure
          </h1>
          <p className="mt-2 max-w-2xl text-[17px] text-ink">
            Four plain measures from FEC filings, roll calls and Congress.gov, every member at once.
            No rank and no overall score: each member page shows where that member stands among
            colleagues in the same party.
          </p>
          <nav aria-label="Chamber" className="mt-4 flex">
            {(["senate", "house"] as const).map((c) => (
              <Link
                key={c}
                href={`${MEASURES_HREF}?chamber=${c}`}
                aria-current={c === chamber ? "page" : undefined}
                className={`-ml-px inline-flex min-h-11 items-center border px-4 text-sm font-semibold first:ml-0 ${c === chamber ? TAB_ON : TAB_OFF}`}
              >
                {c === "senate" ? "Senate" : "House"}
              </Link>
            ))}
          </nav>
          {!data ? (
            <p className="mt-6 text-ink-lo">The measures could not be loaded. Try again shortly.</p>
          ) : (
            <>
              <p className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-lo">
                <span>
                  <Mark party="D" /> Democrat
                </span>
                <span>
                  <Mark party="R" /> Republican
                </span>
                <span>
                  <Mark party="I" /> Independent
                </span>
                <span>│ median</span>
              </p>
              <div className="mt-3 border border-white/25 bg-surface">
                {data.measures.map((m) => (
                  <ChamberStrip key={m.key} m={m} house={house} />
                ))}
              </div>
              <FactTable members={data.members} house={house} />
            </>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
