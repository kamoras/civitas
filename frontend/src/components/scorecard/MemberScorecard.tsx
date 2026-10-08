"use client";

import { useCallback, useState, type ReactNode } from "react";
import type { Senator } from "@/types/senator";
import type { Committee } from "@/types/politicians";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";
import { useConfig } from "@/hooks/useConfig";
import { displayScore } from "@/lib/formatting";
import { PARTY_LABELS } from "@/lib/partyStyles";
import { getScoreColor } from "@/lib/representation";
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
import ScorecardDrawer from "./ScorecardDrawer";
import ScorecardHeader from "./ScorecardHeader";
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
 * A senator's or representative's scorecard, at a glance: who they are and
 * their Representation Score, then the three scored dimensions side by side,
 * each showing what drives its number (no expanding to find it), then their
 * holdings, then what is on record but not scored. Every full list opens in
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
  rank,
  titleAs = "h1",
}: {
  member: Senator;
  chamber: "senate" | "house";
  breakdown: RepresentationScoreBreakdown | null;
  thumbnailUrl?: string | null;
  stateName?: string | null;
  district?: number | null;
  leadershipTitle?: string | null;
  committees?: Committee[];
  rank?: { rank: number; of: number } | null;
  titleAs?: "h1" | "h2";
}) {
  const config = useConfig();
  const weights = config?.scoreWeights;
  const [view, setView] = useState<DrawerView | null>(null);
  const close = useCallback(() => setView(null), []);
  const scores = member.representationScore;
  const seat =
    chamber === "house" && district != null
      ? `seats that lean like ${houseSeatLabel(member.state, district)}`
      : `states that lean like ${stateName ?? member.state}`;

  // What every section's share image says it is from: the member, and the
  // headline score beside them.
  const overall = displayScore(scores.overall);
  const shareSubject: ShareSubject = {
    title: member.name,
    subtitle: [
      chamber === "senate" ? "Senator" : "Representative",
      chamber === "house" && district != null
        ? houseSeatLabel(member.state, district)
        : (stateName ?? member.state),
      PARTY_LABELS[member.party] ?? member.party,
    ].join(" · "),
    badge: {
      label: "Representation score",
      value: String(overall),
      colorClass: getScoreColor(overall),
    },
    url: absoluteUrl(`/politicians/${encodeURIComponent(member.id)}`),
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
            rank={rank}
            titleAs={titleAs}
          />

          <div className="grid items-stretch gap-5 lg:grid-cols-3">
            <FundingColumn
              funding={member.funding}
              dimension={breakdown?.fundingIndependence}
              score={scores.fundingIndependence}
              weight={weights?.fundingIndependence}
              onMore={() => setView("donors")}
            />
            <AlignmentColumn
              memberId={member.id}
              chamber={chamber}
              name={member.name}
              seat={seat}
              breaks={member.votingRecord.votedAgainstPartyCount}
              approval={member.constituentApproval}
              dimension={breakdown?.constituentAlignment}
              score={scores.constituentAlignment}
              weight={weights?.constituentAlignment}
              onMore={() => setView("votes")}
            />
            <EffectivenessColumn
              chamber={chamber}
              bills={member.sponsoredBills ?? []}
              dimension={breakdown?.legislativeEffectiveness}
              score={scores.legislativeEffectiveness}
              weight={weights?.legislativeEffectiveness}
              onMore={() => setView("bills")}
            />
          </div>

          <Holdings memberId={member.id} filer={chamber} variant="panel" />

          <AlsoOnRecord member={member} chamber={chamber} onOpen={setView} />
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
