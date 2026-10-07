import Link from "next/link";
import { pageMetadata } from "@/lib/site";
import { usableRecord } from "@/lib/ssrPayload";
import { displayScore } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import { ordinal } from "@/components/scorecard/format";
import type { President } from "@/types/president";
import type {
  HistoricalLegacyFacts,
  PresidentEffectivenessFacts,
  PresidentScoreBreakdown,
  PublicMandateFacts,
} from "@/types/scoreBreakdown";

export const metadata = pageMetadata({
  title: "Compare Presidents Side by Side",
  description:
    "Compare any two presidents side by side: the Presidential Score, its four dimensions, and the approval, jobs, growth, rulemaking and historians' figures each is scored on.",
  path: "/compare/presidents",
});

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** Every president. Throws when the backend can't be read, so an outage
 *  shows the error page instead of an empty list. */
async function fetchPresidents(): Promise<President[]> {
  const res = await fetch(`${BACKEND}/api/presidents`, { next: { revalidate: 120 } });
  if (!res.ok) throw new Error(`/api/presidents: HTTP ${res.status}`);
  const body = await res.json();
  if (!Array.isArray(body)) throw new Error("/api/presidents: not a list");
  return body as President[];
}

/** Null when it fails: the scores still compare without the figures. */
async function fetchBreakdown(id: string): Promise<PresidentScoreBreakdown | null> {
  try {
    const res = await fetch(`${BACKEND}/api/presidents/${encodeURIComponent(id)}/score-breakdown`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return usableRecord<PresidentScoreBreakdown>(await res.json(), "publicMandate");
  } catch {
    return null;
  }
}

type Side = { president: President; breakdown: PresidentScoreBreakdown | null };
type Row = { label: string; value: (s: Side) => string; score?: (s: Side) => number | null };

const NONE = "—";
const one = (n: number) => n.toFixed(1);

/** "value vs mean", or a dash when either is missing. */
function versus(
  value: number | null | undefined,
  mean: number | null | undefined,
  fmt: (n: number) => string
) {
  return value == null || mean == null ? NONE : `${fmt(value)} vs ${fmt(mean)}`;
}

function scored(n: number | null) {
  return n == null ? "Not scored" : String(displayScore(n));
}

const SCORE_ROWS: Row[] = [
  {
    label: "Presidential Score",
    value: ({ president: p }) =>
      p.score.dimensionsAvailable === 0 ? "Not scored" : String(displayScore(p.score.overall)),
    score: ({ president: p }) => (p.score.dimensionsAvailable === 0 ? null : p.score.overall),
  },
  ...(
    [
      ["Public Mandate", "publicMandate"],
      ["Effectiveness", "effectiveness"],
      ["Historical Legacy", "historicalLegacy"],
    ] as const
  ).map(([label, key]) => ({
    label,
    value: ({ president: p }: Side) => scored(p.score[key]),
    score: ({ president: p }: Side) => p.score[key],
  })),
];

// Each figure beside the all-president mean it is scored against, as the
// breakdown states them.
const FACT_ROWS: Row[] = [
  {
    label: "Average approval (vs all presidents)",
    value: ({ breakdown: b }) => {
      const f = b?.publicMandate.facts as PublicMandateFacts | undefined;
      return versus(f?.approval, f?.approvalMean, (n) => `${one(n)}%`);
    },
  },
  {
    label: "Jobs a year (vs presidencies since 1939)",
    value: ({ breakdown: b }) => {
      const f = b?.effectiveness.facts as PresidentEffectivenessFacts | undefined;
      return versus(f?.jobsPerYear, f?.jobsMean, (n) => `${n.toFixed(2)}M`);
    },
  },
  {
    label: "Real GDP growth a year (vs the same era)",
    value: ({ breakdown: b }) => {
      const f = b?.effectiveness.facts as PresidentEffectivenessFacts | undefined;
      return versus(f?.gdpGrowth, f?.gdpMean, (n) => `${one(n)}%`);
    },
  },
  {
    label: "Historians' points (vs all presidents)",
    value: ({ breakdown: b }) => {
      const f = b?.historicalLegacy.facts as HistoricalLegacyFacts | undefined;
      return versus(f?.points, f?.pointsMean, (n) => String(Math.round(n)));
    },
  },
];

function term(p: President) {
  return `${p.termStart.slice(0, 4)}–${p.termEnd ? p.termEnd.slice(0, 4) : "present"}`;
}

function PresidentSelect({
  name,
  label,
  presidents,
  value,
}: {
  name: string;
  label: string;
  presidents: President[];
  value?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={`president-${name}`}
        className="font-mono text-xs uppercase tracking-widest text-ink-lo"
      >
        {label}
      </label>
      <select
        id={`president-${name}`}
        name={name}
        defaultValue={value ?? ""}
        required
        className="w-full border border-white/15 bg-white/[0.03] px-3 py-2 font-mono text-sm text-ink-hi focus:border-signal-cyan/40 focus:outline-none"
      >
        <option value="">Choose a president</option>
        {presidents.map((p) => (
          <option key={p.id} value={p.id}>
            {p.number}. {p.name} ({term(p)})
          </option>
        ))}
      </select>
    </div>
  );
}

function PresidentHeading({ side: { president: p } }: { side: Side }) {
  return (
    <th scope="col" className="w-[38%] px-3 py-3 font-display text-base">
      <Link
        href={`/politicians/${p.id}`}
        className="underline-offset-4 hover:text-phos hover:underline"
      >
        {p.name}
      </Link>
      <span className="block font-mono text-xs font-normal text-ink-min">
        {ordinal(p.number)} president · {term(p)}
      </span>
    </th>
  );
}

function ComparisonTable({ left, right }: { left: Side; right: Side }) {
  const cell = (row: Row, side: Side) => {
    const s = row.score?.(side);
    return (
      <td
        className={`px-3 py-2 text-center font-mono ${s != null ? getScoreColor(displayScore(s)) : "text-ink"}`}
      >
        {row.value(side)}
      </td>
    );
  };
  const body = (rows: Row[]) =>
    rows.map((row) => (
      <tr key={row.label}>
        {cell(row, left)}
        <th scope="row" className="px-3 py-2 text-center text-sm font-normal text-ink-lo">
          {row.label}
        </th>
        {cell(row, right)}
      </tr>
    ));
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">
          {left.president.name} and {right.president.name} compared
        </caption>
        <thead className="border-b border-white/[0.07] bg-white/[0.03]">
          <tr>
            <PresidentHeading side={left} />
            <td />
            <PresidentHeading side={right} />
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.07]">{body(SCORE_ROWS)}</tbody>
        <tbody className="divide-y divide-white/[0.07] border-t border-white/[0.12]">
          {body(FACT_ROWS)}
        </tbody>
      </table>
    </div>
  );
}

export default async function ComparePresidentsPage({
  searchParams,
}: {
  searchParams: Promise<{ left?: string; right?: string }>;
}) {
  const { left: leftId, right: rightId } = await searchParams;
  const presidents = await fetchPresidents();
  const byId = new Map(presidents.map((p) => [p.id, p]));
  const leftP = leftId ? byId.get(leftId) : undefined;
  const rightP = rightId ? byId.get(rightId) : undefined;
  const [leftB, rightB] = await Promise.all([
    leftP && rightP ? fetchBreakdown(leftP.id) : null,
    leftP && rightP ? fetchBreakdown(rightP.id) : null,
  ]);

  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-5xl">
          <PageMasthead
            className="mb-8"
            eyebrow="Compare · two presidents, side by side"
            title="Compare presidents"
          >
            <p>
              Pick two presidents to set their Presidential Scores, the four dimensions and the
              figures each is scored on side by side. Members of Congress compare on the{" "}
              <Link
                href="/compare"
                className="underline decoration-white/30 underline-offset-4 hover:text-phos"
              >
                legislator comparison
              </Link>
              .
            </p>
          </PageMasthead>

          <form
            method="get"
            className="panel mb-6 grid grid-cols-1 items-end gap-4 p-4 md:grid-cols-[1fr_1fr_auto]"
          >
            <PresidentSelect
              name="left"
              label="First president"
              presidents={presidents}
              value={leftP?.id}
            />
            <PresidentSelect
              name="right"
              label="Second president"
              presidents={presidents}
              value={rightP?.id}
            />
            <button
              type="submit"
              className="border border-signal-cyan/40 px-4 py-2 font-mono text-xs uppercase tracking-widest text-signal-cyan transition-colors hover:bg-signal-cyan/10"
            >
              Compare
            </button>
          </form>

          {leftP && rightP ? (
            <ComparisonTable
              left={{ president: leftP, breakdown: leftB }}
              right={{ president: rightP, breakdown: rightB }}
            />
          ) : (
            <p className="panel p-8 text-center font-mono text-sm text-ink-min">
              {leftP || rightP
                ? "Choose a second president to compare."
                : "Choose two presidents to compare."}
            </p>
          )}
        </div>
      </main>
      <Footer />
    </div>
  );
}
