"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import type { President } from "@/types/president";
import type { PresidentPeers } from "@/types/presidentPeers";
import type { PresidentCharts } from "@/types/presidentCharts";
import { fetchPresidentStockTrades } from "@/lib/api";
import { SectionHeadingLevelProvider } from "@/components/shared/CollapsibleSection";
import Holdings from "@/components/checker/Holdings";
import StockTrades from "@/components/checker/StockTrades";
import PresidentStanding from "./PresidentStanding";
import PresidentChartsSection from "./PresidentCharts";
import ScorecardDrawer from "./ScorecardDrawer";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, type ShareSubject } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { ordinal } from "./format";
import { presidentParty } from "@/lib/presidentParty";

const LINK = "font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos";

/**
 * A president's page, at a glance: who, the historians' own rank (theirs,
 * named and dated), then where the presidency stands among the others on
 * each figure it has (PresidentStanding), then what else is on record. No
 * score: Civitas's 0-100 scores left the site in 2026-10. Every number is
 * the API's (GET /api/presidents/{id}/peers).
 */
export default function PresidentScorecard({
  president,
  peers,
  charts = null,
  titleAs: Title = "h1",
  scoredOn = null,
}: {
  president: President;
  peers: PresidentPeers | null;
  /** The term drawn against the other presidencies (GET /charts). */
  charts?: PresidentCharts | null;
  titleAs?: "h1" | "h2";
  /** The latest pipeline run: what a shared image's figures are as of. */
  scoredOn?: { date: string } | null;
}) {
  const [trades, setTrades] = useState<{ total: number } | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const party = presidentParty(president.party);
  const legacy = peers?.historians;
  const survey = `C-SPAN Presidential Historians Survey${legacy?.surveyYear ? `, ${legacy.surveyYear}` : ""}`;
  const termEnd = president.termEnd ? president.termEnd.slice(0, 4) : "present";
  // What every section's share image says it is from: the president, and
  // the historians' rank beside them, named as theirs.
  const shareSubject: ShareSubject = {
    title: president.name,
    subtitle: `${ordinal(president.number)} President · ${party.label} · ${president.termStart.slice(0, 4)} to ${termEnd}`,
    badge:
      legacy?.rank == null
        ? undefined
        : {
            label: "Historians' rank (C-SPAN)",
            value: `#${legacy.rank} of ${legacy.rated}`,
            colorClass: "text-ink-hi",
          },
    url: absoluteUrl(`/politicians/${encodeURIComponent(president.id)}`),
    // The sources the header links (left out of the picture, so named here).
    source: `American Presidency Project approval polls; BLS jobs; BEA GDP; Federal Register; ${survey}`,
    asOf: scoredOn?.date ?? null,
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
            className={`grid grid-cols-[minmax(0,1fr)] gap-6 border border-white/25 border-t-[3px] bg-surface px-5 py-6 font-sans sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] ${party.border}`}
          >
            {/* Portrait above the name below sm: beside it, the name had
                about 146px at 320px and long surnames split mid-word. */}
            <div className="flex min-w-0 flex-col gap-5 sm:flex-row">
              <div
                className={`flex h-24 w-20 shrink-0 flex-col items-center justify-center border-2 sm:h-28 sm:w-24 ${party.border} ${party.text}`}
                aria-hidden="true"
              >
                <span className="font-display text-4xl font-semibold leading-none">
                  {president.number}
                </span>
                <span className="mt-1 text-xs">President</span>
              </div>
              <div className="flex min-w-0 flex-col gap-2">
                <Title className="break-words text-3xl font-semibold leading-tight text-ink-hi sm:text-4xl">
                  {president.name}
                </Title>
                <p className="text-sm text-ink-lo">
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
                  {legacy?.surveyUrl && (
                    <a href={legacy.surveyUrl} className={LINK}>
                      C-SPAN survey
                    </a>
                  )}
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
              <p className="text-[13px] font-semibold text-ink-lo">Historians&apos; rank</p>
              {legacy?.rank != null ? (
                <>
                  <p className="font-display text-5xl font-semibold leading-none text-ink-hi">
                    #{legacy.rank} <span className="text-2xl text-ink-lo">of {legacy.rated}</span>
                  </p>
                  <p className="text-sm leading-relaxed text-ink-lo">
                    Their ranking, not Civitas&apos;s: the {survey}, by total points (
                    {legacy.points} points).
                  </p>
                </>
              ) : (
                legacy && (
                  <p className="text-sm leading-relaxed text-ink-lo">
                    Not rated. C-SPAN&apos;s Presidential Historians Survey rates only completed
                    terms, and the newest edition published does not rate this presidency.
                  </p>
                )
              )}
              {/* The header names the president itself: no title strip. */}
              <div className="flex justify-end">
                <ShareSectionButton label="Summary" withStrip={false} />
              </div>
            </div>
          </header>

          {peers ? (
            <PresidentStanding name={president.name} peers={peers} />
          ) : (
            <p className="text-sm text-ink-lo">
              The comparison with other presidents could not be loaded.
            </p>
          )}

          {charts && <PresidentChartsSection data={charts} />}

          {/* Only the sitting president's annual report is read; a former
              president's page shows nothing here rather than an empty panel. */}
          {president.isCurrent && (
            <Holdings memberId={president.id} filer="president" variant="panel" />
          )}

          <section className="flex flex-col gap-3" aria-labelledby="also-on-record">
            <h2 id="also-on-record" className="text-[13px] font-semibold text-ink-lo">
              Also on record
            </h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {president.isCurrent && (
                <button
                  type="button"
                  onClick={() => setOpen(true)}
                  disabled={!trades?.total}
                  className="flex min-h-24 flex-col gap-1.5 border border-white/[0.18] bg-surface px-5 py-4 text-left disabled:cursor-default"
                >
                  <span className="text-base font-semibold text-ink-hi">Stock trades</span>
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
              {peers?.recentApproval != null && (
                <div className="flex min-h-24 flex-col gap-1.5 border border-white/[0.18] bg-surface px-5 py-4">
                  <span className="text-base font-semibold text-ink-hi">
                    Approval, last 90 days
                  </span>
                  <span className="text-sm text-ink-lo">{peers.recentApproval.toFixed(1)}%</span>
                  <span className="font-mono text-xs text-ink-min">
                    American Presidency Project
                  </span>
                </div>
              )}
              {peers?.otherTerms.map((t) => (
                <Link
                  key={t.id}
                  href={`/politicians/${t.id}`}
                  className="flex min-h-24 flex-col gap-1.5 border border-white/[0.18] bg-surface px-5 py-4 hover:border-ink-min"
                >
                  <span className="text-base font-semibold text-ink-hi">
                    The {ordinal(t.number)}-president term
                  </span>
                  <span className="text-sm text-ink-lo">Historians: {t.points} points</span>
                  <span className="font-mono text-xs text-ink-min">C-SPAN survey</span>
                </Link>
              ))}
              {president.eoCount != null && (
                <div className="flex min-h-24 flex-col gap-1.5 border border-white/[0.18] bg-surface px-5 py-4">
                  <span className="text-base font-semibold text-ink-hi">Executive orders</span>
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
