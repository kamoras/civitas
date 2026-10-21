"use client";

import { useCallback, useState, type ReactNode } from "react";
import type { Senator } from "@/types/senator";
import type { Committee } from "@/types/politicians";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";
import { PARTY_LABELS } from "@/lib/partyStyles";
import { absoluteUrl } from "@/lib/site";
import { houseSeatLabel } from "@/lib/elections";
import type { ShareSubject } from "@/lib/shareImage";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SectionHeadingLevelProvider } from "@/components/shared/CollapsibleSection";
import Holdings from "@/components/checker/Holdings";
import IndustryBreakdown from "@/components/checker/IndustryBreakdown";
import LobbyingMatches from "@/components/checker/LobbyingMatches";
import PlatformTracker from "@/components/checker/PlatformTracker";
import SponsoredBills from "@/components/checker/SponsoredBills";
import StockTrades from "@/components/checker/StockTrades";
import VotingRecord from "@/components/checker/VotingRecord";
import AlignmentColumn from "./AlignmentColumn";
import AlsoOnRecord, { type RecordView } from "./AlsoOnRecord";
import EffectivenessColumn from "./EffectivenessColumn";
import FundingColumn from "./FundingColumn";
import MethodsLedger from "./MethodsLedger";
import ScorecardDrawer from "./ScorecardDrawer";
import ScorecardHeader from "./ScorecardHeader";
import WhereTheyStand from "./WhereTheyStand";
import type { PeerComparison } from "@/types/peers";
import TopDonorsTable from "./TopDonorsTable";

type DrawerView = "donors" | "votes" | "bills" | RecordView;

const DRAWER_TITLE: Record<DrawerView, string> = {
  donors: "Donors and industries",
  votes: "Every recorded vote",
  bills: "Sponsored bills",
  trades: "Stock trades",
  donorVotes: "Donor and vote links",
  positions: "Positions by policy area",
};

/**
 * A senator's or representative's page: who they are and how to reach them,
 * then where they stand among peers measure by measure (no composite score
 * or rank: redesign 2026-10), then the record behind it, money, votes and
 * bills side by side,
 * each led by the breakdown's one-sentence finding and then the evidence
 * behind its number (no expanding to find it; only the method, the
 * components, weights and statistics, sits behind "How this is computed"),
 * then their holdings, then what is on record but not scored. Every full list opens in
 * a drawer over the page. On a phone the columns stack into cards.
 *
 * Every number stated comes from the API: the stored scores, the score
 * breakdown's components and `facts` (the scorer's own figures), and the
 * member's records. The page only lays them out.
 */
export default function MemberScorecard({
  member,
  chamber,
  breakdown,
  thumbnailUrl,
  stateName,
  district,
  leadershipTitle,
  committees,
  titleAs = "h1",
  former = false,
  scoredOn = null,
  peers = null,
  votesNote = null,
}: {
  member: Senator;
  chamber: "senate" | "house";
  breakdown: RepresentationScoreBreakdown | null;
  thumbnailUrl?: string | null;
  stateName?: string | null;
  district?: number | null;
  leadershipTitle?: string | null;
  committees?: Committee[];
  titleAs?: "h1" | "h2";
  /** No longer serving: the header leaves out how to reach them. */
  former?: boolean;
  /** Where the member stands among the caucus, read by the server page. */
  peers?: PeerComparison | null;
  /** The latest scoring run, for the methods section and its citation. */
  scoredOn?: { date: string; algorithmVersion: string | null } | null;
  /** Votes with the president's stated positions in one line (rendered by
   *  the server page): in the Votes column, not above the member's name. */
  votesNote?: ReactNode;
}) {
  const [view, setView] = useState<DrawerView | null>(null);
  const close = useCallback(() => setView(null), []);
  const seat =
    chamber === "house" && district != null
      ? `seats that lean like ${houseSeatLabel(member.state, district)}`
      : `states that lean like ${stateName ?? member.state}`;

  // What every section's share image says it is from: the member. No
  // headline score: the composite is not shown (redesign 2026-10).
  const shareSubject: ShareSubject = {
    title: member.name,
    subtitle: [
      chamber === "senate" ? "Senator" : "Representative",
      chamber === "house" && district != null
        ? houseSeatLabel(member.state, district)
        : (stateName ?? member.state),
      PARTY_LABELS[member.party] ?? member.party,
    ].join(" · "),
    url: absoluteUrl(`/politicians/${encodeURIComponent(member.id)}`),
    // Every part's source, as the methods section lists them (MethodsLedger).
    source: `FEC filings; roll calls as ${chamber === "senate" ? "Senate.gov" : "the House Clerk"} records them; Voteview; Cook PVI; Congress.gov bill status`,
    asOf: scoredOn?.date ?? null,
  };

  const drawerBody: Record<DrawerView, () => ReactNode> = {
    donors: () => (
      <div className="flex flex-col gap-8">
        <TopDonorsTable donors={member.funding.topDonors} />
        <IndustryBreakdown
          industries={member.funding.industryBreakdown}
          donors={member.funding.topDonors}
        />
      </div>
    ),
    votes: () => (
      <VotingRecord senatorId={member.id} votingRecord={member.votingRecord} chamber={chamber} />
    ),
    bills: () => <SponsoredBills bills={member.sponsoredBills} />,
    trades: () => <StockTrades politicianId={member.id} filer={chamber} />,
    donorVotes: () => <LobbyingMatches matches={member.lobbyingMatches} />,
    positions: () =>
      member.partisanDepth ? (
        <PlatformTracker partisanDepth={member.partisanDepth} senatorParty={member.party} />
      ) : null,
  };

  return (
    <SectionHeadingLevelProvider value="h3">
      <ShareSubjectProvider subject={shareSubject}>
        <div className="flex flex-col gap-6">
          <ScorecardHeader
            member={member}
            chamber={chamber}
            thumbnailUrl={thumbnailUrl}
            stateName={stateName}
            district={district}
            leadershipTitle={leadershipTitle}
            committees={committees}
            titleAs={titleAs}
            former={former}
          />

          {!former && (
            <WhereTheyStand
              memberId={member.id}
              name={member.name}
              chamber={chamber}
              initial={peers}
            />
          )}

          <h2 className="border-t-[3px] border-ink-hi pt-3 font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]">
            The record behind it
          </h2>

          <div className="grid grid-cols-[minmax(0,1fr)] items-stretch gap-5 lg:grid-cols-3">
            <FundingColumn
              funding={member.funding}
              dimension={breakdown?.fundingIndependence}
              onMore={() => setView("donors")}
              donorVoteLinks={member.lobbyingMatches?.length ?? 0}
              onDonorVotes={() => setView("donorVotes")}
            />
            <AlignmentColumn
              memberId={member.id}
              chamber={chamber}
              name={member.name}
              seat={seat}
              breaks={member.votingRecord.votedAgainstPartyCount}
              approval={member.constituentApproval}
              dimension={breakdown?.constituentAlignment}
              onMore={() => setView("votes")}
              note={votesNote}
            />
            <EffectivenessColumn
              chamber={chamber}
              bills={member.sponsoredBills ?? []}
              dimension={breakdown?.legislativeEffectiveness}
              onMore={() => setView("bills")}
            />
          </div>

          <Holdings memberId={member.id} filer={chamber} variant="panel" />

          <AlsoOnRecord member={member} chamber={chamber} onOpen={setView} />

          <MethodsLedger
            member={member}
            chamber={chamber}
            breakdown={breakdown}
            scoredOn={scoredOn}
          />
        </div>
      </ShareSubjectProvider>

      {view && (
        <ScorecardDrawer title={DRAWER_TITLE[view]} subtitle={member.name} onClose={close}>
          {drawerBody[view]()}
        </ScorecardDrawer>
      )}
    </SectionHeadingLevelProvider>
  );
}
