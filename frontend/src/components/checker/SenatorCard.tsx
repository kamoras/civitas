import { Senator } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";
import { safeHref } from "@/lib/formatting";
import { fecCommitteeSearchUrl } from "@/lib/sources";
import CorruptionScore from "./CorruptionScore";
import IndustryBreakdown from "./IndustryBreakdown";
import VotingRecord from "./VotingRecord";
import LobbyingMatches from "./LobbyingMatches";
import PlatformTracker from "./PlatformTracker";
import PunkCommentary from "./PunkCommentary";
import SponsoredBills from "./SponsoredBills";
import CollapsibleSection from "./CollapsibleSection";
import MetricTooltip from "./MetricTooltip";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import ScoreTrendSection from "./ScoreTrendSection";
import NotablePartyBreaks from "./NotablePartyBreaks";
import Link from "next/link";

interface SenatorCardProps {
  senator: Senator;
  chamber?: "senate" | "house";
}

const PARTY_COLORS = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

const PARTY_BORDER = {
  D: "border-dem-blue/30",
  R: "border-rep-red/30",
  I: "border-ind-purple/30",
};

const PARTY_LABELS = {
  D: "DEMOCRAT",
  R: "REPUBLICAN",
  I: "INDEPENDENT",
};

function DonorsSummary({ senator }: { senator: Senator }) {
  const top = senator.funding.topDonors[0];
  if (!top) return null;
  return (
    <span>
      #1: {top.name} ({formatCurrency(top.total)})
    </span>
  );
}

function IndustrySummary({ senator }: { senator: Senator }) {
  const sorted = [...senator.funding.industryBreakdown].sort((a, b) => b.total - a.total);
  const top = sorted.slice(0, 3);
  if (top.length === 0) return null;
  return (
    <span>
      {top.map((ind) => ind.name || ind.industry.replace(/_/g, " ")).join(" · ")}
    </span>
  );
}

function ContactInfo({ senator }: { senator: Senator }) {
  const hasContact = senator.contactFormUrl || senator.officePhone || senator.officeAddress;
  if (!hasContact) return null;

  return (
    <div className="mt-3 p-3 border border-neon-cyan/15 bg-neon-cyan/5 space-y-2">
      <div className="font-pixel text-[10px] text-neon-cyan/60 tracking-wider">
        CONTACT YOUR REPRESENTATIVE
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {senator.contactFormUrl && (
          <a
            href={safeHref(senator.contactFormUrl) || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-neon-cyan/30 text-neon-cyan/80 font-pixel text-[10px] hover:bg-neon-cyan/10 hover:border-neon-cyan/50 transition-colors"
          >
            SEND A MESSAGE <span aria-hidden="true">↗</span>
          </a>
        )}
        {senator.officePhone && (
          <a
            href={`tel:${senator.officePhone.replace(/[^0-9+]/g, "")}`}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-matrix-green/20 text-matrix-green/60 font-pixel text-[10px] hover:bg-matrix-green/5 hover:border-matrix-green/30 transition-colors"
          >
            CALL: {senator.officePhone}
          </a>
        )}
      </div>
      {senator.officeAddress && (
        <div className="text-[10px] text-matrix-green/40">
          DC Office: {senator.officeAddress}
        </div>
      )}
      {!senator.contactFormUrl && !senator.officePhone && (
        <div className="text-[10px] text-matrix-green/30 italic">
          Contact form not available — visit their official website for contact options.
        </div>
      )}
    </div>
  );
}

export default function SenatorCard({ senator, chamber = "senate" }: SenatorCardProps) {
  const pacPercentRaw = (senator.funding.totalFromPACs / senator.funding.totalRaised) * 100;
  const pacPercent =
    pacPercentRaw > 0 && pacPercentRaw < 1 ? "<1" : Math.round(pacPercentRaw).toString();

  const hasPromises = (senator.campaignPromises || []).length > 0;
  const hasPartisan = senator.partisanDepth && senator.partisanDepth.totalPositions > 0;
  const hasLobbyingMatches = senator.lobbyingMatches && senator.lobbyingMatches.length > 0;

  return (
    <div id={`senator-${senator.id}`} className={`terminal-window border-t-2 ${PARTY_BORDER[senator.party]}`}>
      <TerminalTitlebar title={`${senator.id}.dat`} />

      <div className="p-4 sm:p-6 space-y-6">
        {/* ── Header ── always visible */}
        <div className="flex items-start gap-4">
          <div
            className={`w-16 h-16 border-2 ${PARTY_BORDER[senator.party]} flex items-center justify-center font-pixel text-lg ${PARTY_COLORS[senator.party]}`}
          >
            {senator.initials}
          </div>
          <div>
            <h2
              className={`text-lg sm:text-2xl md:text-3xl font-pixel ${PARTY_COLORS[senator.party]} break-words`}
            >
              {senator.name}
            </h2>
            <div className="flex flex-wrap items-center gap-2 mt-1 text-sm">
              <span className={`font-pixel text-xs ${PARTY_COLORS[senator.party]}`}>
                [{senator.party}]
              </span>
              <span className="text-matrix-green/40">{PARTY_LABELS[senator.party]}</span>
              <span className="text-matrix-green/20">|</span>
              <span className="text-matrix-green/40">{senator.yearsInOffice} YRS IN OFFICE</span>
            </div>
            {senator.sponsorshipDescription && (
              <div className="text-[10px] text-matrix-green/60 mt-1 font-mono uppercase tracking-wider">
                <MetricTooltip text="Derived from cosponsorship patterns using PageRank (influence) and SVD (ideology). Shows how this senator is positioned relative to peers.">
                  {senator.sponsorshipDescription}
                </MetricTooltip>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-3 mt-1.5">
              <a
                href={`https://www.fec.gov/data/candidates/?search=${encodeURIComponent(senator.name)}&office=S`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
              >
                [FEC FILINGS]
              </a>
              <a
                href={`https://www.congress.gov/member/${senator.name.toLowerCase().replace(/\s+/g, "-")}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
              >
                [CONGRESS.GOV]
              </a>
              {senator.websiteUrl && (
                <a
                  href={safeHref(senator.websiteUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
                >
                  [OFFICIAL SITE]
                </a>
              )}
            </div>
            <ContactInfo senator={senator} />
          </div>
        </div>

        {/* ── Quick Stats ── always visible */}
        <div>
          <div className="flex flex-wrap gap-2 text-center">
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2 flex-1 basis-[calc(33%-0.5rem)] sm:basis-auto sm:min-w-[5rem]">
              {senator.partisanDepth && senator.partisanDepth.totalPositions > 0 ? (
                <>
                  <div className={`text-sm sm:text-lg font-pixel whitespace-nowrap ${
                    senator.partisanDepth.depth === "deep" ? "text-neon-pink"
                    : senator.partisanDepth.depth === "cross-cutting" ? "text-neon-cyan"
                    : senator.partisanDepth.depth === "moderate" ? "text-yellow-500"
                    : "text-matrix-green"
                  }`}>
                    {senator.partisanDepth.depth === "deep" ? "DEEP"
                    : senator.partisanDepth.depth === "cross-cutting" ? "XCUT"
                    : senator.partisanDepth.depth === "moderate" ? "MOD"
                    : "CTR"}
                  </div>
                  <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="How strongly this senator's votes align with their party. Derived from roll-call votes, not stated positions. DEEP = strong loyalist, MOD = moderate, CTR = centrist, XCUT = frequently crosses party lines.">PARTISAN</MetricTooltip></div>
                </>
              ) : (
                <>
                  <div className="text-sm sm:text-lg font-pixel text-matrix-green/30">&mdash;</div>
                  <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="How strongly this senator's votes align with their party. Derived from roll-call votes, not stated positions. DEEP = strong loyalist, MOD = moderate, CTR = centrist, XCUT = frequently crosses party lines.">PARTISAN</MetricTooltip></div>
                </>
              )}
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2 flex-1 basis-[calc(33%-0.5rem)] sm:basis-auto sm:min-w-[5rem]">
              {senator.leadershipScore != null ? (
                <>
                  <div className={`text-sm sm:text-lg font-pixel whitespace-nowrap ${
                    senator.leadershipScore > 0.75 ? "text-neon-yellow"
                    : senator.leadershipScore < 0.25 ? "text-matrix-green/40"
                    : "text-matrix-green"
                  }`}>
                    {Math.round(senator.leadershipScore * 100)}
                  </div>
                  <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Legislative influence score (0–100) based on PageRank of cosponsorship networks. Higher = more senators cosponsor this senator's bills.">LEADER</MetricTooltip></div>
                </>
              ) : (
                <>
                  <div className="text-sm sm:text-lg font-pixel text-matrix-green/30">&mdash;</div>
                  <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Legislative influence score (0–100) based on PageRank of cosponsorship networks. Higher = more senators cosponsor this senator's bills.">LEADER</MetricTooltip></div>
                </>
              )}
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2 flex-1 basis-[calc(33%-0.5rem)] sm:basis-auto sm:min-w-[5rem]">
              <div className="text-sm sm:text-lg font-pixel text-neon-cyan whitespace-nowrap">
                {formatCurrency(senator.funding.totalRaised)}
              </div>
              <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Total campaign contributions received this cycle from all sources (individuals, PACs, and self-funding). Source: FEC filings.">RAISED</MetricTooltip></div>
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2 flex-1 basis-[calc(33%-0.5rem)] sm:basis-auto sm:min-w-[5rem]">
              <div className="text-sm sm:text-lg font-pixel text-neon-pink whitespace-nowrap">
                {formatCurrency(senator.funding.totalFromPACs)}
              </div>
              <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Money received from Political Action Committees — organizations that pool contributions from members to donate to campaigns. Includes corporate, labor, and ideological PACs.">PACs</MetricTooltip></div>
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2 flex-1 basis-[calc(33%-0.5rem)] sm:basis-auto sm:min-w-[5rem]">
              <div className="text-sm sm:text-lg font-pixel text-red-500 whitespace-nowrap">{pacPercent}%</div>
              <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="What percentage of total funds come from PACs rather than individual donors. Higher % = more reliance on organized interest group money.">PAC %</MetricTooltip></div>
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2 flex-1 basis-[calc(33%-0.5rem)] sm:basis-auto sm:min-w-[5rem]">
              <div className="text-sm sm:text-lg font-pixel text-matrix-green whitespace-nowrap">
                {senator.funding.smallDonorPercentage}%
              </div>
              <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Percentage of funds from individual donations under $200. Higher = more grassroots support from everyday people vs. large donors.">SMALL $</MetricTooltip></div>
            </div>
          </div>
          <div className="text-[10px] text-matrix-green/50 mt-2 text-right">
            Source: fec.gov/data &amp; opensecrets.org/members-of-congress
          </div>
        </div>

        {/* ── Compare link ── */}
        <div className="flex justify-end">
          <Link
            href={`/compare?leftId=${senator.id}&chamber=${chamber}`}
            className="font-pixel text-[10px] text-neon-cyan/50 hover:text-neon-cyan border border-neon-cyan/20 px-2 py-0.5 transition-colors"
          >
            COMPARE →
          </Link>
        </div>

        {/* ── Representation Score ── always visible */}
        <CorruptionScore
          breakdown={senator.representationScore}
          promises={senator.campaignPromises}
          votingRecord={senator.votingRecord}
          funding={senator.funding}
        />

        {/* ── Score Trend ── shows historical sparkline if snapshots exist */}
        <ScoreTrendSection entityId={senator.id} entityType={chamber} />

        {/* ── Notable Party Breaks ── inline party-defection votes */}
        <NotablePartyBreaks
          entityId={senator.id}
          entityType={chamber}
          votedAgainstPartyCount={senator.votingRecord.votedAgainstPartyCount}
        />

        {/* ── Collapsible detail sections ── */}

        <CollapsibleSection
          title="TOP CORPORATE &amp; PAC DONORS"
          summary={<DonorsSummary senator={senator} />}
          source="fec.gov/data &amp; opensecrets.org"
        >
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-matrix-green/40 text-xs border-b border-matrix-green/20">
                  <th scope="col" className="text-left py-2 pr-4">RANK</th>
                  <th scope="col" className="text-left py-2 pr-4">DONOR</th>
                  <th scope="col" className="text-right py-2 pr-4">AMOUNT</th>
                  <th scope="col" className="text-right py-2">TYPE</th>
                </tr>
              </thead>
              <tbody>
                {senator.funding.topDonors.slice(0, 10).map((donor, i) => (
                  <tr
                    key={donor.name}
                    className={`border-b border-matrix-green/5 ${
                      i % 2 === 0 ? "bg-matrix-dark-green/10" : ""
                    }`}
                  >
                    <td className="py-2 pr-4 text-matrix-green/40">#{i + 1}</td>
                    <td className="py-2 pr-4">
                      <div className="text-matrix-green/80">
                        {(donor.type === "PAC" || donor.type === "SuperPAC") ? (
                          <a
                            href={fecCommitteeSearchUrl(donor.name)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-neon-cyan underline underline-offset-2 decoration-matrix-green/30 hover:decoration-neon-cyan/50 transition-colors"
                          >
                            {donor.name}
                          </a>
                        ) : (
                          donor.name
                        )}
                      </div>
                      {donor.pacSponsor &&
                        donor.pacSponsor.toLowerCase() !== donor.name.toLowerCase() &&
                        !["unclear", "unknown", "n/a", "none", ""].includes(donor.pacSponsor.toLowerCase().trim()) &&
                        donor.pacSponsor.length > 2 && (
                        <div className="text-[10px] text-neon-pink/60 mt-0.5">
                          BEHIND THE PAC: {donor.pacSponsor}
                        </div>
                      )}
                      {donor.pacAnalysis &&
                        !donor.pacAnalysis.match(/has received funding from|a political PAC|opposes the removal|which is (?:not )?(?:aligned with|related to) (?:his|her|their) (?:platform|stance|stated)/i) && (
                        <div className="text-[10px] text-matrix-green/40 mt-0.5">
                          {donor.pacAnalysis}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-right text-neon-cyan">
                      {formatCurrency(donor.total)}
                    </td>
                    <td className="py-2 text-right">
                      <div className="text-matrix-green/40 text-xs">
                        {donor.type === "CandidateAffiliated" ? "Own Committee" : donor.type === "Self-Funded" ? "Self-Funded" : donor.type}
                      </div>
                      {donor.pacIndustry && donor.pacIndustry !== "OTHER" && (
                        <div className="text-[10px] text-neon-cyan/50 mt-0.5">
                          {donor.pacIndustry.replace("_", " ")}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CollapsibleSection>

        <CollapsibleSection
          title="FOLLOW THE MONEY"
          summary={<IndustrySummary senator={senator} />}
          source="opensecrets.org/industries"
        >
          <IndustryBreakdown industries={senator.funding.industryBreakdown} donors={senator.funding.topDonors} />
        </CollapsibleSection>

        <VotingRecord senatorId={senator.id} votingRecord={senator.votingRecord} chamber={chamber} />

        {senator.sponsoredBills && senator.sponsoredBills.length > 0 && (
          <SponsoredBills bills={senator.sponsoredBills} />
        )}

        {(hasPromises || hasPartisan) && (
          <PlatformTracker
            promises={senator.campaignPromises || []}
            platformSummary={senator.platformSummary || ""}
            partisanDepth={senator.partisanDepth}
            senatorParty={senator.party}
          />
        )}

        {hasLobbyingMatches && (
          <CollapsibleSection
            title="DONOR-VOTE CONNECTIONS"
            titleColor="text-neon-pink neon-pink"
            summary={(() => {
              const aligned = senator.lobbyingMatches.filter(m => m.senatorVoteAligned === true).length;
              const withAlignment = senator.lobbyingMatches.filter(m => m.senatorVoteAligned !== null).length;
              const base = `${senator.lobbyingMatches.length} donor-vote overlap${senator.lobbyingMatches.length !== 1 ? "s" : ""}`;
              return withAlignment > 0 ? `${base} · ${aligned} voted same direction as donor interest` : base;
            })()}
            source="fec.gov/data"
          >
            <LobbyingMatches matches={senator.lobbyingMatches} />
          </CollapsibleSection>
        )}

        <PunkCommentary senator={senator} />
      </div>
    </div>
  );
}
