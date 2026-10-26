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
import IndustryBreakdown from "@/components/checker/IndustryBreakdown";
import LobbyingMatches from "@/components/checker/LobbyingMatches";
import PlatformTracker from "@/components/checker/PlatformTracker";
import SponsoredBills from "@/components/checker/SponsoredBills";
import StockTrades from "@/components/checker/StockTrades";
import VotingRecord from "@/components/checker/VotingRecord";
import AlsoOnRecord, { type RecordView } from "./AlsoOnRecord";
import ApprovalBox from "./ApprovalBox";
import BillsBox from "./BillsBox";
import MoneyBox from "./MoneyBox";
import VotesBox from "./VotesBox";
import BoxGrid from "@/components/shared/BoxGrid";
import { CHIP } from "@/components/shared/RecordBox";
import PresidentBox from "@/components/presidentialSupport/PresidentBox";
import type { GovernmentRecord, MemberBoxes } from "@/types/politicians";
import MethodsLedger from "./MethodsLedger";
import ScorecardDrawer from "./ScorecardDrawer";
import ScorecardHeader from "./ScorecardHeader";
import WhereTheyStand from "./WhereTheyStand";
import type { PeerComparison } from "@/types/peers";
import TopDonorsTable from "./TopDonorsTable";

type DrawerView = "donors" | "votes" | "bills" | RecordView;

/** The section chips under the strips: each box's anchor and name. */
const SECTION_CHIPS: [string, string][] = [
  ["#funding-independence", "Money"],
  ["#constituent-alignment", "Votes"],
  ["#legislative-effectiveness", "Bills"],
  ["#holdings", "Holdings"],
  ["#trades", "Trades"],
  ["#government-record", "Record"],
  ["#methods", "Methods"],
];

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
  boxes = null,
  ballotDetail = null,
  governmentRecord = { totalDocs: 0, recentDocs: [] },
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
  /** Every box's title, line and figure (backend member_boxes); absent
   *  from an older backend. */
  boxes?: MemberBoxes | null;
  /** The ballot's full lines, behind the header's committee line. */
  ballotDetail?: ReactNode;
  /** Documents on public record (the profile's governmentRecord). */
  governmentRecord?: GovernmentRecord;
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
        <div className="flex flex-col gap-4 sm:gap-5">
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
            boxes={boxes}
            ballotDetail={ballotDetail}
          />

          {!former && (
            <WhereTheyStand
              memberId={member.id}
              name={member.name}
              chamber={chamber}
              initial={peers}
            />
          )}

          {/* The page's sections, one tap away: the boxes below. */}
          <nav aria-label="On this page" className="flex flex-wrap items-center gap-2">
            <span className="text-small text-ink-lo">On this page</span>
            {SECTION_CHIPS.map(([href, label]) => (
              <a key={href} href={href} className={CHIP}>
                {label}
              </a>
            ))}
          </nav>

          <BoxGrid label="The record behind it" labelId="record-behind-it">
            <MoneyBox
              funding={member.funding}
              box={boxes?.money}
              dimension={breakdown?.fundingIndependence}
              onMore={() => setView("donors")}
              donorVoteLinks={member.lobbyingMatches?.length ?? 0}
              onDonorVotes={() => setView("donorVotes")}
            />
            <VotesBox
              memberId={member.id}
              chamber={chamber}
              seat={seat}
              breaks={member.votingRecord.votedAgainstPartyCount}
              box={boxes?.votes}
              dimension={breakdown?.constituentAlignment}
              onMore={() => setView("votes")}
            />
            <BillsBox
              chamber={chamber}
              bills={member.sponsoredBills ?? []}
              box={boxes?.bills}
              dimension={breakdown?.legislativeEffectiveness}
              onMore={() => setView("bills")}
            />
            <PresidentBox memberId={member.id} name={member.name} box={boxes?.president} />
            <ApprovalBox approval={member.constituentApproval} box={boxes?.approval} />
          </BoxGrid>

          <AlsoOnRecord
            member={member}
            chamber={chamber}
            boxes={boxes}
            record={governmentRecord}
            onOpen={setView}
          />

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
