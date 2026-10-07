"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { President } from "@/types/president";
import type {
  HistoricalLegacyFacts,
  PresidentEffectivenessFacts,
  PresidentScoreBreakdown,
  PublicMandateFacts,
  ScoreBreakdownDimension,
} from "@/types/scoreBreakdown";
import { useConfig } from "@/hooks/useConfig";
import { fetchPresidentStockTrades } from "@/lib/api";
import { displayScore } from "@/lib/formatting";
import { getPresidentLabel, getScoreBgColor, getScoreColor } from "@/lib/representation";
import { SectionHeadingLevelProvider } from "@/components/shared/CollapsibleSection";
import Holdings from "@/components/checker/Holdings";
import ScoreTrendSection from "@/components/checker/ScoreTrendSection";
import StockTrades from "@/components/checker/StockTrades";
import ComparisonScale from "./ComparisonScale";
import ComponentBars from "./ComponentBars";
import ScoreColumn from "./ScoreColumn";
import ScorecardDrawer from "./ScorecardDrawer";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, type ShareSubject } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { ordinal } from "./format";

const PARTY: Record<string, { label: string; text: string; border: string }> = {
  D: { label: "Democrat", text: "text-dem-blue", border: "border-dem-blue/40" },
  R: { label: "Republican", text: "text-signal-red", border: "border-signal-red/40" },
  DR: { label: "Democratic-Republican", text: "text-signal-cyan", border: "border-signal-cyan/40" },
  F: { label: "Federalist", text: "text-ind-purple", border: "border-ind-purple/40" },
  W: { label: "Whig", text: "text-signal-amber", border: "border-signal-amber/40" },
};
const NO_PARTY = { label: "No party", text: "text-ink", border: "border-white/30" };

/** A president's party label and colours (also the leaderboard's summary). */
export function presidentParty(code: string) {
  return PARTY[code] ?? NO_PARTY;
}

const LINK = "font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos";

function tone(score: number | null | undefined) {
  const shown = score == null ? 50 : displayScore(score);
  return { text: getScoreColor(shown), bg: getScoreBgColor(shown) };
}

function one(n: number) {
  return n.toFixed(1);
}

function signed(n: number) {
  return `${n > 0 ? "+" : ""}${one(n)}`;
}

function Facts({ rows }: { rows: [string, string][] }) {
  if (rows.length === 0) return null;
  return (
    <dl className="flex flex-col gap-1.5 border-t border-white/[0.08] pt-4 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3">
          <dt className="text-ink-lo">{k}</dt>
          <dd className="text-right font-mono text-ink-hi">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Lede({ children }: { children: React.ReactNode }) {
  return <p className="text-base leading-relaxed text-ink">{children}</p>;
}

function MandateColumn({
  dim,
  score,
  weight,
}: {
  dim?: ScoreBreakdownDimension;
  score: number | null;
  weight?: number;
}) {
  const f = dim?.facts as PublicMandateFacts | undefined;
  // The sitting president is compared with predecessors over the same
  // stretch of their terms; everyone else with completed terms.
  const months = f?.comparedOverDays != null ? Math.round(f.comparedOverDays / 30.44) : null;
  const over = months != null ? `over their first ${months} months` : "over their terms";
  const rose = (n: number) => (n >= 0 ? "rose" : "fell");
  return (
    <ScoreColumn title="Public Mandate" shareId="public-mandate" weight={weight} score={score}>
      {f?.approval != null && f.approvalMean != null ? (
        <>
          <Lede>
            Averaged {one(f.approval)}% approval {months != null ? "so far" : "over the term"};
            presidents averaged {one(f.approvalMean)}% {over}.
            {f.approvalTrend != null && f.trendExpected != null && f.approvalStart != null
              ? ` Approval ${rose(f.approvalTrend)} ${one(Math.abs(f.approvalTrend))} points from a start of ${one(f.approvalStart)}%; presidents who started there ${rose(f.trendExpected)} about ${one(Math.abs(f.trendExpected))}.`
              : f.approvalTrend != null &&
                f.trendMean != null &&
                ` Approval ${rose(f.approvalTrend)} ${one(Math.abs(f.approvalTrend))} points over the term, against a typical ${f.trendMean >= 0 ? "rise" : "fall"} of ${one(Math.abs(f.trendMean))}.`}
          </Lede>
          <ComparisonScale
            value={f.approval}
            norm={f.approvalMean}
            min={0}
            max={100}
            axis={["0%", "100%"]}
            valueLabel={`This term ${one(f.approval)}%`}
            normLabel={
              months != null
                ? `predecessors' first ${months} months ${one(f.approvalMean)}%`
                : `all presidents ${one(f.approvalMean)}%`
            }
            tone={tone(score)}
          />
        </>
      ) : f?.electionMargin != null && f.marginMean != null ? (
        <Lede>
          Before approval polling: won by an average margin of {one(f.electionMargin)} points;
          presidents average {one(f.marginMean)}.
        </Lede>
      ) : (
        score == null && (
          <Lede>
            {dim?.note ?? "Never won a presidential election in their own right, so not scored."}
          </Lede>
        )
      )}
      {dim && <ComponentBars components={dim.components} />}
      <Facts
        rows={[
          ...(f?.recentApproval != null
            ? [["Last 90 days", `${one(f.recentApproval)}%`] as [string, string]]
            : []),
        ]}
      />
    </ScoreColumn>
  );
}

function EffectivenessColumn({
  dim,
  score,
  weight,
  isCurrent,
}: {
  dim?: ScoreBreakdownDimension;
  score: number | null;
  weight?: number;
  isCurrent: boolean;
}) {
  const f = dim?.facts as PresidentEffectivenessFacts | undefined;
  const jobs = f?.jobsPerYear != null && f.jobsMean != null && f.jobsMillions != null;
  return (
    <ScoreColumn title="Effectiveness" shareId="effectiveness" weight={weight} score={score}>
      {(f || score == null) && (
        <Lede>
          {jobs &&
            `${signed(f!.jobsMillions!)} million jobs, ${f!.jobsPerYear!.toFixed(2)} million a year once the first year is set aside. Presidencies since 1939 average ${f!.jobsMean!.toFixed(2)} million. `}
          {f?.gdpGrowth != null && f.gdpMean != null
            ? `Real growth averaged ${one(f.gdpGrowth)}% a year, first year excluded; presidencies ${f.gdpSince ? "since" : "before"} 1947 average ${one(f.gdpMean)}%.`
            : isCurrent
              ? "GDP growth is measured from the second full year of a term."
              : "No GDP figure for this term."}
          {!jobs && f?.gdpGrowth == null && !isCurrent && " Payroll jobs are counted from 1939."}
        </Lede>
      )}
      {jobs && (
        <ComparisonScale
          value={f!.jobsPerYear!}
          norm={f!.jobsMean!}
          min={Math.min(0, f!.jobsPerYear!)}
          max={Math.max(f!.jobsPerYear!, f!.jobsMean!) * 1.25}
          axis={[
            `${Math.min(0, f!.jobsPerYear!).toFixed(1)}M`,
            `${(Math.max(f!.jobsPerYear!, f!.jobsMean!) * 1.25).toFixed(1)}M a year`,
          ]}
          valueLabel={`This term ${f!.jobsPerYear!.toFixed(2)}M a year`}
          normLabel={`presidencies since 1939: ${f!.jobsMean!.toFixed(2)}M`}
          tone={tone(score)}
        />
      )}
      {dim && <ComponentBars components={dim.components} />}
    </ScoreColumn>
  );
}

function LegacyColumn({
  dim,
  score,
  weight,
}: {
  dim?: ScoreBreakdownDimension;
  score: number | null;
  weight?: number;
}) {
  const f = dim?.facts as HistoricalLegacyFacts | undefined;
  return (
    <ScoreColumn
      title="Historical Legacy"
      shareId="historical-legacy"
      weight={weight}
      score={score}
    >
      {f?.points != null && f.pointsMean != null ? (
        <Lede>
          {f.points} points in C-SPAN&apos;s 2021 survey of historians; presidents average{" "}
          {Math.round(f.pointsMean)}.
        </Lede>
      ) : (
        score == null && (
          <Lede>
            Not rated. C-SPAN&apos;s Presidential Historians Survey rates completed terms, and its
            2025 survey was postponed.
          </Lede>
        )
      )}
      {f?.otherTerms?.map((t) => (
        <p key={t.id} className="text-sm text-ink-lo">
          The {ordinal(t.number)}-president term:{" "}
          <Link
            href={`/politicians/${t.id}`}
            className="underline underline-offset-2 hover:text-phos"
          >
            {t.points} points{t.score != null ? `, scored ${displayScore(t.score)}` : ""}
          </Link>
          .
        </p>
      ))}
      {dim && <ComponentBars components={dim.components} />}
    </ScoreColumn>
  );
}

/**
 * A president's scorecard, at a glance, laid out like a member's: who and
 * the Presidential Score, then the four scored dimensions side by side, each
 * with the figures it is scored on beside the all-president averages, then
 * what is on record but not scored. Every number is the API's: the stored
 * scores, and the score breakdown's components and `facts`.
 */
export default function PresidentScorecard({
  president,
  breakdown,
  rank,
  titleAs: Title = "h1",
}: {
  president: President;
  breakdown: PresidentScoreBreakdown | null;
  rank?: { rank: number; of: number } | null;
  titleAs?: "h1" | "h2";
}) {
  // Each score's actual share of this president's overall, from the
  // backend; the nominal weights only for a payload that predates it.
  const nominal = useConfig()?.presidentScoreWeights;
  const weights = president.score.effectiveWeights ?? nominal;
  const [trades, setTrades] = useState<{ total: number } | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const s = president.score;
  const party = presidentParty(president.party);
  const overall = displayScore(s.overall);
  const termEnd = president.termEnd ? president.termEnd.slice(0, 4) : "present";
  // What every section's share image says it is from, as on the member
  // scorecard: the president, and the headline score beside them.
  const shareSubject: ShareSubject = {
    title: president.name,
    subtitle: `${ordinal(president.number)} President · ${party.label} · ${president.termStart.slice(0, 4)} to ${termEnd}`,
    badge:
      s.dimensionsAvailable === 0
        ? undefined
        : {
            label: "Presidential score",
            value: String(overall),
            colorClass: getScoreColor(overall),
          },
    url: absoluteUrl(`/politicians/${encodeURIComponent(president.id)}`),
  };

  useEffect(() => {
    if (!president.isCurrent) return;
    let live = true;
    fetchPresidentStockTrades(president.id, { page: 1, perPage: 1 })
      .then((r) => live && setTrades({ total: r.total }))
      .catch(() => live && setTrades(null));
    return () => {
      live = false;
    };
  }, [president.id, president.isCurrent]);

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
              <div
                className={`flex h-24 w-20 shrink-0 flex-col items-center justify-center border-2 sm:h-28 sm:w-24 ${party.border} ${party.text}`}
                aria-hidden="true"
              >
                <span className="font-display text-4xl font-extrabold leading-none">
                  {president.number}
                </span>
                <span className="mt-1 font-mono text-[10px] tracking-[0.12em]">PRESIDENT</span>
              </div>
              <div className="flex min-w-0 flex-col gap-2">
                <Title className="break-words text-3xl font-extrabold leading-tight text-ink-hi sm:text-4xl">
                  {president.name}
                </Title>
                <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
                  {ordinal(president.number)} President · {party.label} ·{" "}
                  {president.termStart.slice(0, 4)} to {termEnd}
                </p>
                {/* Source links are only useful clicked; a picture of them is
                  noise. */}
                <p className="flex flex-wrap gap-x-4 gap-y-1" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
                  <a
                    href="https://www.presidency.ucsb.edu/statistics/data/presidential-job-approval"
                    className={LINK}
                  >
                    Approval polls
                  </a>
                  <a href="https://data.bls.gov/timeseries/CES0000000001" className={LINK}>
                    BLS jobs
                  </a>
                  <a href="https://www.federalregister.gov" className={LINK}>
                    Federal Register
                  </a>
                  <a href="https://www.c-span.org/presidentsurvey2021/" className={LINK}>
                    C-SPAN survey
                  </a>
                  <Link
                    href={`/compare/presidents?left=${encodeURIComponent(president.id)}`}
                    className={LINK}
                  >
                    Compare with another president
                  </Link>
                </p>
              </div>
            </div>
            <div className="flex flex-col gap-2 border-t border-white/[0.12] pt-5 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
              <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                Presidential Score
              </p>
              {s.dimensionsAvailable === 0 ? (
                <p className="font-display text-2xl font-extrabold text-ink-min">
                  Not yet calculated
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
                      {getPresidentLabel(overall)}
                    </span>
                    {rank ? (
                      <Link href="/leaderboard?branch=president" className={LINK}>
                        #{rank.rank} of {rank.of} presidents
                      </Link>
                    ) : (
                      president.isCurrent && (
                        <span className="font-mono text-[13px] text-ink-lo">
                          Ranked once the term ends
                        </span>
                      )
                    )}
                  </div>
                </div>
              )}
              {s.dimensionsAvailable > 0 && s.dimensionsAvailable < 3 && (
                <p className="text-sm leading-relaxed text-ink-lo">
                  Built from {s.dimensionsAvailable} of 3 scores; a score with no data shares its
                  weight among the others rather than counting as zero, and each score shows its
                  actual share.
                </p>
              )}
              <ScoreTrendSection entityId={president.id} entityType="president" />
              {/* The header names the president itself: no title strip. */}
              <div className="flex justify-end">
                <ShareSectionButton label="Scorecard summary" withStrip={false} />
              </div>
            </div>
          </header>

          <div className="grid items-stretch gap-5 md:grid-cols-3">
            <MandateColumn
              dim={breakdown?.publicMandate}
              score={s.publicMandate}
              weight={weights?.publicMandate}
            />
            <EffectivenessColumn
              dim={breakdown?.effectiveness}
              score={s.effectiveness}
              weight={weights?.effectiveness}
              isCurrent={president.isCurrent}
            />
            <LegacyColumn
              dim={breakdown?.historicalLegacy}
              score={s.historicalLegacy}
              weight={weights?.historicalLegacy}
            />
          </div>

          {/* Only the sitting president's annual report is read; a former
              president's page shows nothing here rather than an empty panel. */}
          {president.isCurrent && (
            <Holdings memberId={president.id} filer="president" variant="panel" />
          )}

          <section className="flex flex-col gap-3" aria-labelledby="also-on-record">
            <h2
              id="also-on-record"
              className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min"
            >
              Also on record, not part of the score
            </h2>
            <div className="grid gap-4 sm:grid-cols-2">
              {president.isCurrent && (
                <button
                  type="button"
                  onClick={() => setOpen(true)}
                  disabled={!trades?.total}
                  className="flex min-h-24 flex-col gap-1.5 border border-white/[0.18] bg-surface px-5 py-4 text-left disabled:cursor-default"
                >
                  <span className="text-base font-bold text-ink-hi">Stock trades</span>
                  <span className="text-sm text-ink-lo">
                    {trades === undefined
                      ? "Loading…"
                      : trades === null
                        ? "Could not load the trades."
                        : trades.total === 0
                          ? "None disclosed this term"
                          : `${trades.total.toLocaleString()} disclosed this term`}
                  </span>
                  <span className="font-mono text-xs text-ink-min">
                    OGE annual report and periodic transaction reports
                  </span>
                </button>
              )}
              {president.eoCount != null && (
                <div className="flex min-h-24 flex-col gap-1.5 border border-white/[0.18] bg-surface px-5 py-4">
                  <span className="text-base font-bold text-ink-hi">Executive orders</span>
                  <span className="text-sm text-ink-lo">
                    {president.eoCount.toLocaleString()} signed
                  </span>
                  <span className="font-mono text-xs text-ink-min">Federal Register</span>
                </div>
              )}
            </div>
          </section>
        </div>
      </ShareSubjectProvider>

      {open && (
        <ScorecardDrawer title="Stock trades" subtitle={president.name} onClose={close}>
          <StockTrades politicianId={president.id} filer="president" />
        </ScorecardDrawer>
      )}
    </SectionHeadingLevelProvider>
  );
}
