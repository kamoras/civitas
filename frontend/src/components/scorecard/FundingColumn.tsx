"use client";

import type { Senator } from "@/types/senator";
import type { FundingFacts, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import { useIndustries } from "@/hooks/useConfig";
import { formatCurrency } from "@/lib/formatting";
import { fecCommitteeSearchUrl } from "@/lib/sources";
import ComponentBars from "./ComponentBars";
import ScoreColumn, { Block } from "./ScoreColumn";
import { percent } from "./format";

const DONOR_KIND: Record<string, string> = {
  PAC: "PAC",
  SuperPAC: "super PAC",
  "Org/Employees": "employees",
  "Party/Ideological": "party or ideological",
  Individual: "individual",
};

// The config's colours are chosen for one bar per industry; stacked into
// one bar, its two largest sources for nearly every member (#00ff41 and
// #39ff14) sit side by side and read as one. These are set apart here.
const SEGMENT_COLOR: Record<string, string> = {
  SMALL_DONORS: "#00FF41",
  LARGE_INDIVIDUAL: "#CDC7BC",
  OTHER: "#8A8378",
  UNCLASSIFIED: "#5A554D",
  CANDIDATE_FUNDS: "#B08D57",
};
const FALLBACK_COLOR = "#8A8378";

// Industries shown by name under the bar; the rest are in the drawer.
const INDUSTRIES_NAMED = 6;
const DONORS_SHOWN = 5;

function Lede({ facts }: { facts: FundingFacts }) {
  const expected = facts.smallDonorExpectedShare;
  if (facts.smallDonorShare == null) {
    return (
      <p className="text-base leading-relaxed text-ink">
        {percent(facts.pacShare)} of {formatCurrency(facts.contributions)} in contributions came
        from PACs. The campaign itemizes every gift, so its filings can&apos;t say how much came
        from donors giving $200 or less.
      </p>
    );
  }
  const small = percent(facts.smallDonorShare);
  return (
    <p className="text-base leading-relaxed text-ink">
      {percent(facts.pacShare)} of {formatCurrency(facts.contributions)} in contributions came from
      PACs. {small} came from donors giving $200 or less
      {expected != null &&
        (facts.smallDonorComparison === "house-median"
          ? `; the House median is ${percent(expected)}.`
          : `; about ${percent(expected)} is typical for a state this size.`)}
      {expected == null && "."}
    </p>
  );
}

export default function FundingColumn({
  funding,
  dimension,
  score,
  weight,
  onMore,
}: {
  funding: Senator["funding"];
  dimension: ScoreBreakdownDimension | undefined;
  score: number;
  weight?: number;
  onMore: () => void;
}) {
  const industryInfo = useIndustries();
  const facts = dimension?.facts as FundingFacts | undefined;
  // The backend's shares, largest first; only the order is decided here.
  const industries = [...funding.industryBreakdown]
    .filter((i) => i.percentage > 0)
    .sort((a, b) => b.total - a.total);
  // A member's own committee and self-funding aren't donors to them.
  const donors = funding.topDonors
    .filter((d) => d.type !== "CandidateAffiliated" && d.type !== "Self-Funded")
    .slice(0, DONORS_SHOWN);
  const colorOf = (code: string) =>
    SEGMENT_COLOR[code] ?? industryInfo[code]?.color ?? FALLBACK_COLOR;
  const nameOf = (code: string, fallback: string) =>
    industryInfo[code]?.name || fallback || code.replace(/_/g, " ");

  return (
    <ScoreColumn
      title="Funding Independence"
      shareId="funding-independence"
      weight={weight}
      score={score}
      more={{ label: "All donors and industries", onClick: onMore }}
    >
      {facts ? (
        <Lede facts={facts} />
      ) : (
        <p className="text-base text-ink-lo">No campaign finance filings on record yet.</p>
      )}

      {industries.length > 0 && (
        <Block label="Where the money comes from">
          <div
            className="flex h-3.5 gap-0.5"
            role="img"
            aria-label={industries
              .map((i) => `${nameOf(i.industry, i.name)} ${i.percentage}%`)
              .join(", ")}
          >
            {industries.map((i) => (
              <span
                key={i.industry}
                className="h-full"
                style={{ width: `${i.percentage}%`, backgroundColor: colorOf(i.industry) }}
              />
            ))}
          </div>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-ink-lo">
            {industries.slice(0, INDUSTRIES_NAMED).map((i) => (
              <li key={i.industry} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2.5 w-2.5 shrink-0"
                  style={{ backgroundColor: colorOf(i.industry) }}
                  aria-hidden="true"
                />
                {nameOf(i.industry, i.name)} {i.percentage}%
              </li>
            ))}
          </ul>
        </Block>
      )}

      {donors.length > 0 && (
        <Block label={`Top ${donors.length} donors`}>
          <ul className="flex flex-col gap-1.5">
            {donors.map((d) => (
              <li key={d.name} className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 text-ink [overflow-wrap:anywhere]">
                  {d.type === "PAC" || d.type === "SuperPAC" ? (
                    <a
                      href={fecCommitteeSearchUrl(d.name)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline decoration-white/15 underline-offset-2 hover:text-phos"
                    >
                      {d.name}
                    </a>
                  ) : (
                    d.name
                  )}{" "}
                  {DONOR_KIND[d.type] && (
                    <span className="font-mono text-xs text-ink-min">{DONOR_KIND[d.type]}</span>
                  )}
                </span>
                <span className="shrink-0 font-mono text-ink-hi">{formatCurrency(d.total)}</span>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {dimension && <ComponentBars components={dimension.components} />}
    </ScoreColumn>
  );
}
