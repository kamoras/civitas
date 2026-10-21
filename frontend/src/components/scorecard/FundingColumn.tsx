"use client";

import type { Senator } from "@/types/senator";
import type { FundingFacts, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import { useIndustries } from "@/hooks/useConfig";
import { formatCurrency } from "@/lib/formatting";
import { chartMark } from "@/lib/chartColors";
import { fecCommitteeSearchUrl } from "@/lib/sources";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";
import ScoreColumn, { Block } from "./ScoreColumn";
import { percent } from "./format";

const DONOR_KIND: Record<string, string> = {
  PAC: "PAC",
  SuperPAC: "super PAC",
  "Org/Employees": "employees",
  // Plainer than the classifier's label: a party committee or a
  // political group (NRSC, a super PAC's affiliate, an issue group).
  "Party/Ideological": "party or political group",
  Individual: "individual",
};

// The config's colours are chosen for one bar per industry; stacked into
// one bar, its two largest sources for nearly every member (#00ff41 and
// #39ff14) sit side by side and read as one. These are set apart here.
const SEGMENT_COLOR: Record<string, string> = {
  SMALL_DONORS: "var(--phos)",
  LARGE_INDIVIDUAL: "var(--ink-lo)",
  OTHER: "var(--chart-muted)",
  UNCLASSIFIED: "var(--chart-faint)",
  CANDIDATE_FUNDS: "var(--chart-gold)",
};
const FALLBACK_COLOR = "var(--chart-muted)";

// Industries shown by name under the bar; the rest are in the drawer.
const INDUSTRIES_NAMED = 6;
const DONORS_SHOWN = 5;

const TOOLTIP =
  "Where the money for the member's most recent completed election came from, from FEC filings: the share given by PACs and the share given in gifts of $200 or less, against what is typical (for a senator, a state that size; for a representative, the House median), then the industries behind the money and the top donors. Shares are of contributions, not total receipts.";

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The figures behind the finding. After the finding (which states the
 *  small-gift share, or the PAC share when the filings can't say), only
 *  what it left out: the PAC share of the total. */
function Lede({ facts, afterFinding }: { facts: FundingFacts; afterFinding: boolean }) {
  const expected = facts.smallDonorExpectedShare;
  // The backend's words for each share (shown_share), so this line reads
  // the same as the finding and the peer strip.
  // Each leads a sentence ("Under 1% of ...").
  const pac = capitalize(facts.shown?.pacShare ?? percent(facts.pacShare));
  if (afterFinding) {
    if (facts.smallDonorShare == null) return null;
    return (
      <p className="text-base leading-relaxed text-ink">
        {pac} of {formatCurrency(facts.contributions)} in contributions came from PACs.
      </p>
    );
  }
  if (facts.smallDonorShare == null) {
    return (
      <p className="text-base leading-relaxed text-ink">
        {pac} of {formatCurrency(facts.contributions)} in contributions came from PACs. The campaign
        itemizes every gift, so its filings can&apos;t say how much came from donors giving $200 or
        less.
      </p>
    );
  }
  const small = capitalize(facts.shown?.smallDonorShare ?? percent(facts.smallDonorShare));
  const typical =
    expected == null ? null : (facts.shown?.smallDonorExpectedShare ?? percent(expected));
  return (
    <p className="text-base leading-relaxed text-ink">
      {pac} of {formatCurrency(facts.contributions)} in contributions came from PACs. {small} came
      from donors giving $200 or less
      {typical != null &&
        (facts.smallDonorComparison === "house-median"
          ? `; the House median is ${typical}.`
          : `; about ${typical} is typical for a state this size.`)}
      {typical == null && "."}
    </p>
  );
}

export default function FundingColumn({
  funding,
  dimension,
  onMore,
  donorVoteLinks = 0,
  onDonorVotes,
}: {
  funding: Senator["funding"];
  dimension: ScoreBreakdownDimension | undefined;
  onMore: () => void;
  /** Stored donor-vote overlaps; with `onDonorVotes`, a link to them here,
   *  beside the money, rather than only in the tiles at the foot of the
   *  page (about seven screens down on a phone). */
  donorVoteLinks?: number;
  onDonorVotes?: () => void;
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
    SEGMENT_COLOR[code] ??
    (industryInfo[code]?.color ? chartMark(industryInfo[code].color) : FALLBACK_COLOR);
  const nameOf = (code: string, fallback: string) =>
    industryInfo[code]?.name || fallback || code.replace(/_/g, " ");

  return (
    <ScoreColumn
      title="Money"
      shareId="funding-independence"
      tooltip={TOOLTIP}
      finding={dimension?.finding}
      more={{ label: "All donors and industries", onClick: onMore }}
    >
      {facts ? (
        <Lede facts={facts} afterFinding={!!dimension?.finding} />
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

      {onDonorVotes && donorVoteLinks > 0 && (
        <button
          type="button"
          onClick={onDonorVotes}
          {...{ [SHARE_EXCLUDE_ATTR]: "" }}
          className="min-h-[44px] self-start text-left text-sm text-ink underline underline-offset-2 hover:text-phos"
        >
          See how this money lines up with votes ({donorVoteLinks} link
          {donorVoteLinks !== 1 ? "s" : ""}) &rarr;
        </button>
      )}
    </ScoreColumn>
  );
}
