"use client";

import { useCallback, useEffect } from "react";
import {
  KeyVote,
  PaginatedVotes,
  VoteCounts,
  VotingRecord as VotingRecordType,
} from "@/types/senator";
import { billPageHref } from "@/lib/congress";
import { fetchSenatorVotes, fetchRepVotes } from "@/lib/api";
import CollapsibleSection from "../shared/CollapsibleSection";
import MetricTooltip from "./MetricTooltip";
import Link from "next/link";
import { shortDate, voteTitle } from "@/components/scorecard/format";
import Pagination from "@/components/shared/Pagination";
import { useLatestRequest } from "@/hooks/useLatestRequest";

const VOTES_PER_PAGE = 15;

interface VotingRecordProps {
  senatorId: string;
  votingRecord: VotingRecordType;
  chamber?: "senate" | "house";
}

function VoteBadge({ vote }: { vote: string }) {
  const styles =
    vote === "Yea"
      ? "text-ink-hi bg-white/[0.03] border-white/15"
      : vote === "Nay"
        ? "text-signal-red bg-signal-red/10 border-signal-red/40"
        : "text-signal-amber bg-signal-amber/10 border-signal-amber/40";
  return (
    <span className={`font-mono text-xs tracking-widest px-2 py-1 border ${styles}`}>
      {vote.toUpperCase()}
    </span>
  );
}

/** One vote, on one line: what was voted on (linked to the bill's page,
 *  where its text, sponsor and every vote on it are), the question, the
 *  date, the member's vote, and whether it went against the party. It used
 *  to expand into the pipeline's own working — content-lean badges, policy
 *  areas, "stance", the internal vote id — which meant nothing to a reader. */
function VoteRow({ vote }: { vote: KeyVote }) {
  const rc = vote.rollCall;
  const title = voteTitle(rc?.title, rc?.billLabel, vote.billName);
  // The roll call's bill, or, for a vote stored before its roll call was
  // recorded, the vote's own bill id. A nomination has no bill page.
  const href = rc ? billPageHref(rc.billId, rc.congress) : billPageHref(vote.billId);
  return (
    <li className="flex items-start justify-between gap-3 border-b border-white/[0.07] py-2.5">
      <div className="min-w-0">
        {href ? (
          <Link
            href={href}
            className="line-clamp-2 text-sm text-ink underline decoration-white/20 underline-offset-2 hover:text-phos"
            title={rc?.title || vote.billName}
          >
            {title}
          </Link>
        ) : (
          <span className="line-clamp-2 text-sm text-ink">{title}</span>
        )}
        <p className="mt-0.5 text-xs text-ink-min">
          {[rc?.question, rc ? shortDate(rc.date) : vote.date].filter(Boolean).join(" · ")}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {vote.votedWithParty === false && (
          <span className="border border-signal-magenta/40 bg-signal-magenta/10 px-1.5 py-0.5 font-mono text-xs text-signal-magenta">
            AGAINST PARTY
          </span>
        )}
        <VoteBadge vote={vote.vote} />
      </div>
    </li>
  );
}

function VoteFilter({
  label,
  active,
  count,
  onClick,
}: {
  label: string;
  active: boolean;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`text-xs px-2 py-1 border font-mono transition-all ${
        active
          ? "text-ink-hi border-white/15 bg-white/[0.03]"
          : "text-ink-min border-white/[0.07] hover:border-white/15"
      }`}
    >
      {label} ({count})
    </button>
  );
}

type VoteFilterType = "all" | "yea" | "nay" | "against-party";

function PaginatedVoteList({
  senatorId,
  category,
  voteCount,
  chamber = "senate",
}: {
  senatorId: string;
  category: "recent" | "key";
  voteCount: number;
  chamber?: "senate" | "house";
}) {
  // `filter` is the one last asked for, `shownFilter` the one the votes on
  // screen were fetched with.
  const {
    data,
    loading,
    error,
    requested: filter,
    shown: shownFilter,
    request,
  } = useLatestRequest<PaginatedVotes, VoteFilterType>("all", "Failed to load votes");

  const fetchVotes = useCallback(
    (p: number, f: VoteFilterType) => {
      const fetcher = chamber === "house" ? fetchRepVotes : fetchSenatorVotes;
      request(f, () =>
        fetcher(senatorId, { category, page: p, perPage: VOTES_PER_PAGE, filter: f })
      );
    },
    [request, senatorId, category, chamber]
  );

  useEffect(() => {
    if (voteCount > 0) {
      fetchVotes(1, "all");
    }
  }, [fetchVotes, voteCount]);

  const handleFilterChange = (f: VoteFilterType) => {
    fetchVotes(1, f);
  };

  const handlePageChange = (p: number) => {
    fetchVotes(p, shownFilter);
  };

  if (voteCount === 0) return null;

  if (!data && loading) {
    return (
      <div className="panel p-4 text-center" role="status" aria-live="polite">
        <span className="text-ink-lo text-sm animate-pulse">Loading votes...</span>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="panel p-4 text-center" role="alert">
        <span className="text-signal-red text-sm">{error}</span>
      </div>
    );
  }

  if (!data) return null;

  const counts: VoteCounts = data.counts;

  return (
    <div className={loading ? "opacity-60 transition-opacity" : ""}>
      {voteCount > VOTES_PER_PAGE && (
        <div className="flex items-center gap-1.5 mb-3 flex-wrap">
          <VoteFilter
            label="ALL"
            active={filter === "all"}
            count={counts.all}
            onClick={() => handleFilterChange("all")}
          />
          <VoteFilter
            label="YEA"
            active={filter === "yea"}
            count={counts.yea}
            onClick={() => handleFilterChange("yea")}
          />
          <VoteFilter
            label="NAY"
            active={filter === "nay"}
            count={counts.nay}
            onClick={() => handleFilterChange("nay")}
          />
          {counts.againstParty > 0 && (
            <VoteFilter
              label="AGAINST PARTY"
              active={filter === "against-party"}
              count={counts.againstParty}
              onClick={() => handleFilterChange("against-party")}
            />
          )}
          <span className="text-xs text-ink-min ml-auto">
            {data.total} votes &middot; page {data.page}/{data.totalPages}
          </span>
        </div>
      )}

      {error && (
        // A failed filter or page change keeps the last votes that loaded.
        <p className="text-signal-red text-sm mb-2" role="alert">
          {error}
        </p>
      )}

      <ul>
        {data.votes.map((vote, i) => (
          <VoteRow key={`${category}-${vote.billId}-${vote.rollCall?.number ?? i}`} vote={vote} />
        ))}
      </ul>

      <Pagination
        numbered
        page={data.page}
        totalPages={data.totalPages}
        onPageChange={handlePageChange}
        // The pages of a list a filter change is replacing.
        disabled={loading && filter !== shownFilter}
      />
    </div>
  );
}

export default function VotingRecord({
  senatorId,
  votingRecord,
  chamber = "senate",
}: VotingRecordProps) {
  const {
    totalVotes,
    partyLoyaltyPct,
    recentVoteCount,
    keyVoteCount,
    votedWithPartyCount = 0,
    votedAgainstPartyCount = 0,
  } = votingRecord;

  const partyIndependencePct = 100 - Math.round(partyLoyaltyPct);
  const partyTotal = votedWithPartyCount + votedAgainstPartyCount;

  const statBoxes = (
    <div className="grid grid-cols-3 gap-2 mb-2 text-center text-sm">
      <div className="panel p-3">
        <div className="text-xl font-display font-semibold text-ink-hi">
          {totalVotes.toLocaleString()}
        </div>
        <div className="text-ink-min text-xs">
          <MetricTooltip text="Total roll-call votes tracked from Congress.gov and Senate.gov for this member across recent and key votes.">
            TOTAL TRACKED
          </MetricTooltip>
        </div>
      </div>
      <div className="panel p-3">
        <div className="text-xl font-display font-semibold text-signal-cyan">
          {Math.round(partyLoyaltyPct)}%
        </div>
        <div className="text-ink-min text-xs">
          <MetricTooltip text="How often this member votes with the majority of the member's party. 100% = perfect party-line voter. Calculated from all scoreable roll-call votes.">
            PARTY LOYALTY
          </MetricTooltip>
        </div>
        <div className="text-xs text-ink-lo">votes with party line</div>
      </div>
      <div className="panel p-3">
        <div className="text-xl font-display font-semibold text-signal-amber">
          {partyIndependencePct}%
        </div>
        <div className="text-ink-min text-xs">
          <MetricTooltip text="How often this member votes against the member's own party. Higher = more willingness to break from party leadership on roll-call votes.">
            INDEPENDENT
          </MetricTooltip>
        </div>
        <div className="text-xs text-ink-lo">
          {votedAgainstPartyCount} of {partyTotal} broke party line
        </div>
      </div>
    </div>
  );

  return (
    <CollapsibleSection
      title="VOTING RECORD"
      summary={`${totalVotes} votes · ${Math.round(partyLoyaltyPct)}% party loyalty`}
      source="congress.gov &amp; senate.gov"
      alwaysVisible={statBoxes}
    >
      <div className="space-y-6 mt-4">
        <p className="text-xs text-ink-lo">
          Each vote links to its bill.{" "}
          <span className="font-mono text-signal-magenta">AGAINST PARTY</span> marks a vote with the
          other side on a roll call the parties split on.
        </p>
        {keyVoteCount > 0 && (
          <div>
            <div className="text-xs text-ink-lo mb-2 font-mono tracking-widest">
              KEY VOTES — LONG-TERM SUMMARY
            </div>
            <PaginatedVoteList
              senatorId={senatorId}
              category="key"
              voteCount={keyVoteCount}
              chamber={chamber}
            />
          </div>
        )}

        {recentVoteCount > 0 && (
          <div>
            <div className="text-xs text-ink-lo mb-2 font-mono tracking-widest">
              RECENT VOTES ({recentVoteCount})
            </div>
            <PaginatedVoteList
              senatorId={senatorId}
              category="recent"
              voteCount={recentVoteCount}
              chamber={chamber}
            />
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
}
