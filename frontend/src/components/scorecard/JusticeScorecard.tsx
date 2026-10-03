import Link from "next/link";
import type { Justice, JusticeLoyalty } from "@/types/justice";
import { displayScore } from "@/lib/formatting";
import { getJusticeLabel, getScoreBgColor, getScoreColor } from "@/lib/representation";
import { SectionHeadingLevelProvider } from "@/components/shared/CollapsibleSection";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, type ShareSubject } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import ScoreColumn from "./ScoreColumn";

const PARTY: Record<string, { label: string; text: string; border: string }> = {
  D: { label: "D", text: "text-dem-blue", border: "border-dem-blue/40" },
  R: { label: "R", text: "text-signal-red", border: "border-signal-red/40" },
};
const NO_PARTY = { label: "", text: "text-ink", border: "border-white/30" };

const LINK = "font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos";

// The estimate's axis, in points either way. Shrunk estimates on the
// 1937-2025 Court fall within +-20 (docs/research/justice-scores.md); one
// beyond is drawn at the edge.
const AXIS_POINTS = 30;

function pts(share: number) {
  return (share * 100).toFixed(1);
}

function pct(share: number) {
  return `${Math.round(share * 100)}%`;
}

function formatDate(iso: string | null) {
  if (!iso) return null;
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? null
    : d.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
}

/** The estimate and one standard error either side, on an axis centred on
 *  no favoritism. */
function EstimateScale({ loyalty, score }: { loyalty: JusticeLoyalty; score: number }) {
  const at = (share: number) =>
    `${((Math.max(-AXIS_POINTS, Math.min(AXIS_POINTS, share * 100)) + AXIS_POINTS) / (2 * AXIS_POINTS)) * 100}%`;
  const bg = getScoreBgColor(displayScore(score));
  const label = `${pts(loyalty.estimate)} points, plus or minus ${pts(loyalty.se)}`;
  return (
    <div role="img" aria-label={label}>
      <div className="relative h-8" aria-hidden="true">
        <span className="absolute inset-x-0 top-[15px] h-0.5 bg-white/[0.14]" />
        <span className="absolute left-1/2 top-[7px] h-[18px] w-0.5 bg-ink-lo" />
        <span
          className={`absolute top-[14px] h-1 opacity-60 ${bg}`}
          style={{
            left: at(loyalty.estimate - loyalty.se),
            right: `calc(100% - ${at(loyalty.estimate + loyalty.se)})`,
          }}
        />
        <span
          className={`absolute top-[5px] -ml-1.5 h-[22px] w-3 ${bg}`}
          style={{ left: at(loyalty.estimate) }}
        />
      </div>
      <div className="relative h-4 font-mono text-xs text-ink-min" aria-hidden="true">
        <span className="absolute left-0">-{AXIS_POINTS} against</span>
        <span className="absolute left-1/2 -translate-x-1/2">0</span>
        <span className="absolute right-0">+{AXIS_POINTS} toward</span>
      </div>
    </div>
  );
}

function LoyaltyColumn({ justice }: { justice: Justice }) {
  const l = justice.loyalty;
  const score = justice.score.loyalty;
  const appointer = justice.appointingPresident ?? "the appointing president";
  return (
    <ScoreColumn title="Independence from the appointing president" shareId="loyalty" score={score}>
      {l && score != null ? (
        <>
          <p className="text-base leading-relaxed text-ink">
            Sided with the federal government in {pct(l.rateIn)} of {l.votesIn} votes while{" "}
            {appointer} was president, and in {pct(l.rateOut)} of {l.votesOut} under other
            presidents. With the government&apos;s side of the case held fixed, and set against
            every justice since 1937, that is {pts(Math.abs(l.estimate))} points{" "}
            {l.estimate >= 0 ? "more" : "less"} often for the appointing president&apos;s
            government, give or take {pts(l.se)}.
          </p>
          <EstimateScale loyalty={l} score={score} />
          <p className="text-sm leading-relaxed text-ink-lo">
            100 is no favoritism either way; the score falls to 0 at twice the spread between
            justices. Cases the federal government argued, from the Supreme Court Database
            {l.throughTerm ? ` through the ${l.throughTerm} term` : ""}, as Epstein and Posner
            (2016) measure it.
          </p>
        </>
      ) : (
        <p className="text-base leading-relaxed text-ink">
          Not yet measured. The Supreme Court Database adds a term after it ends, and the measure
          needs votes both under the appointing president and under others.
        </p>
      )}
    </ScoreColumn>
  );
}

function IdeologyColumn({ points }: { points: [number, number][] }) {
  const first = points[0];
  const last = points[points.length - 1];
  return (
    <ScoreColumn title="Ideology" shareId="ideology" score={null} aside="Not scored">
      {last ? (
        <p className="text-base leading-relaxed text-ink">
          Martin-Quinn position {last[1] > 0 ? "+" : ""}
          {last[1].toFixed(2)} in the {last[0]} term, where negative is liberal and positive
          conservative
          {first && first[0] !== last[0]
            ? `; ${first[1] > 0 ? "+" : ""}${first[1].toFixed(2)} in the first, ${first[0]}`
            : ""}
          .
        </p>
      ) : (
        <p className="text-base leading-relaxed text-ink">No Martin-Quinn position yet.</p>
      )}
      <p className="text-sm leading-relaxed text-ink-lo">
        Shown for context. Where a justice sits says nothing about favoring the president who made
        the appointment, which is what the score measures.
      </p>
    </ScoreColumn>
  );
}

function RecordColumn({ justice: j }: { justice: Justice }) {
  const rows: [string, string][] = [
    ["In the majority", `${Math.round(j.majorityPct)}%`],
    ["In dissent", `${Math.round(j.dissentPct)}%`],
    ["Unanimous cases", `${Math.round(j.unanimousPct)}%`],
    ["Majority in close cases", `${Math.round(j.closeCaseMajorityPct)}%`],
    ["Opinions for the Court", `${j.authoredMajority}`],
    ["Dissents written", `${j.authoredDissent}`],
    ["Concurrences written", `${j.authoredConcurrence}`],
  ];
  return (
    <ScoreColumn title="Voting record" shareId="voting-record" score={null} aside="Not scored">
      <p className="text-base leading-relaxed text-ink">
        {j.casesDecided} cases decided in the recent terms Oyez records.
      </p>
      <dl className="flex flex-col gap-1.5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3">
            <dt className="text-ink-lo">{k}</dt>
            <dd className="text-right font-mono text-ink-hi">{v}</dd>
          </div>
        ))}
      </dl>
    </ScoreColumn>
  );
}

/**
 * A justice's scorecard, at a glance, laid out like a member's and a
 * president's: who and the Judicial Score, then the scored measure beside
 * what is on record and not scored, then agreement with each sitting
 * justice. Every number is the API's.
 */
export default function JusticeScorecard({
  justice: j,
  rank,
  titleAs: Title = "h1",
}: {
  justice: Justice;
  rank?: { rank: number; of: number } | null;
  titleAs?: "h1" | "h2";
}) {
  const party = (j.appointingParty && PARTY[j.appointingParty]) || NO_PARTY;
  const overall = j.score.overall == null ? null : displayScore(j.score.overall);
  const since = formatDate(j.dateStart);
  // Named and ordered by the API (most agreement first). Absent from a
  // response cached before the field replaced agreementMatrix (nginx and the
  // browser both hold these), which must not take the page down.
  const agreement = j.agreement ?? [];
  // What every section's share image says it is from, as on the member and
  // president scorecards.
  const shareSubject: ShareSubject = {
    title: j.name,
    subtitle: [j.roleTitle, j.appointingPresident && `appointed by ${j.appointingPresident}`]
      .filter(Boolean)
      .join(" · "),
    badge:
      overall == null
        ? undefined
        : { label: "Judicial score", value: String(overall), colorClass: getScoreColor(overall) },
    url: absoluteUrl(`/politicians/${encodeURIComponent(j.id)}`),
  };

  return (
    <SectionHeadingLevelProvider value="h3">
      <ShareSubjectProvider subject={shareSubject}>
        <div className="flex flex-col gap-6">
          <header
            id="overview"
            {...{ [SHARE_SECTION_ATTR]: "overview" }}
            className={`grid scroll-mt-[var(--header-clearance)] gap-6 border border-white/25 border-t-[3px] bg-surface px-5 py-6 font-sans sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] ${party.border}`}
          >
            {/* Portrait above the name below sm: beside it, the name had
                about 146px at 320px and long surnames split mid-word. */}
            <div className="flex min-w-0 flex-col gap-5 sm:flex-row">
              {j.thumbnailUrl && (
                // eslint-disable-next-line @next/next/no-img-element -- external, varied justice-photo hosts; not worth per-host next/image remotePatterns
                <img
                  src={j.thumbnailUrl}
                  alt=""
                  className={`h-28 w-24 shrink-0 border-2 object-cover ${party.border}`}
                />
              )}
              <div className="flex min-w-0 flex-col gap-2">
                <Title className="break-words text-3xl font-extrabold leading-tight text-ink-hi sm:text-4xl">
                  {j.name}
                </Title>
                <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
                  {j.roleTitle}
                  {j.appointingPresident && (
                    <>
                      {" "}
                      · appointed by {j.appointingPresident}
                      {party.label && <span className={party.text}> ({party.label})</span>}
                    </>
                  )}
                  {since && ` · since ${since}`}
                </p>
                <p className="flex flex-wrap gap-x-4 gap-y-1" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                  <a href="https://www.oyez.org" className={LINK}>
                    Oyez
                  </a>
                  <a href="http://scdb.la.psu.edu" className={LINK}>
                    Supreme Court Database
                  </a>
                  <a href="https://mqscores.wustl.edu" className={LINK}>
                    Martin-Quinn scores
                  </a>
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-2 border-t border-white/[0.12] pt-5 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
              <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                Judicial Score
              </p>
              {overall == null ? (
                <p className="font-display text-2xl font-extrabold text-ink-min">
                  Not yet measured
                </p>
              ) : (
                <div className="flex items-baseline gap-4">
                  <span
                    className={`font-display text-7xl font-extrabold leading-none ${getScoreColor(overall)}`}
                  >
                    {overall}
                  </span>
                  <div className="flex flex-col gap-1">
                    <span
                      className={`font-mono text-sm tracking-[0.1em] ${getScoreColor(overall)}`}
                    >
                      {getJusticeLabel(overall)}
                    </span>
                    {rank && (
                      <Link href="/leaderboard?branch=scotus" className={LINK}>
                        #{rank.rank} of {rank.of} justices
                      </Link>
                    )}
                  </div>
                </div>
              )}
              <p className="text-sm leading-relaxed text-ink-lo">
                Higher means the justice sided with the federal government about as often under the
                president who made the appointment as under any other. It measures independence from
                that president, not whether a ruling was right.
              </p>
              {/* The header names the justice itself: no title strip. */}
              <div className="flex justify-end">
                <ShareSectionButton label="Scorecard summary" withStrip={false} />
              </div>
            </div>
          </header>

          <div className="grid items-stretch gap-5 lg:grid-cols-3">
            <LoyaltyColumn justice={j} />
            <IdeologyColumn points={j.idealPoints} />
            <RecordColumn justice={j} />
          </div>

          {agreement.length > 0 && (
            <section
              id="agreement"
              {...{ [SHARE_SECTION_ATTR]: "agreement" }}
              className="flex scroll-mt-[var(--header-clearance)] flex-col gap-4 border border-white/25 bg-surface px-5 py-5 font-sans"
              aria-labelledby="agreement-heading"
            >
              <div>
                <h2 id="agreement-heading" className="text-[19px] font-bold text-ink-hi">
                  Agreement with each justice
                </h2>
                <p className="mt-1 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                  Share of the cases both decided the same way · not scored
                </p>
              </div>
              <dl className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
                {agreement.map(({ id, name, share }) => (
                  <div key={id} className="flex items-center gap-3">
                    <dt className="w-40 truncate text-sm text-ink">{name}</dt>
                    <dd className="flex flex-1 items-center gap-3">
                      <span className="h-1.5 flex-1 bg-white/10" aria-hidden="true">
                        <span className="block h-full bg-ink-lo" style={{ width: `${share}%` }} />
                      </span>
                      <span className="w-12 text-right font-mono text-sm tabular-nums text-ink-hi">
                        {Math.round(share)}%
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
              <div className="flex justify-end">
                <ShareSectionButton label="Agreement with each justice" />
              </div>
            </section>
          )}
        </div>
      </ShareSubjectProvider>
    </SectionHeadingLevelProvider>
  );
}
