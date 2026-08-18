"use client";

import { useCallback, useEffect, useState } from "react";
import {
  KeyVote,
  PaginatedVotes,
  VoteCounts,
  VotingRecord as VotingRecordType,
} from "@/types/senator";
import { voteSourceUrl } from "@/lib/sources";
import { fetchSenatorVotes, fetchRepVotes } from "@/lib/api";
import CollapsibleSection from "../shared/CollapsibleSection";
import MetricTooltip from "./MetricTooltip";
import { PARTY_BADGE } from "@/lib/partyStyles";

const VOTES_PER_PAGE = 15;

interface VotingRecordProps {
  senatorId: string;
  votingRecord: VotingRecordType;
  chamber?: "senate" | "house";
}

function PartyBadge({ leaning }: { leaning: string | null }) {
  if (!leaning) return null;
  const badge = PARTY_BADGE[leaning];
  if (!badge) return null;
  return (
    <span className={`text-xs px-1 py-0.5 border font-mono ${badge.className}`}>{badge.label}</span>
  );
}

function PartyAlignmentBadge({ votedWithParty }: { votedWithParty: boolean | null }) {
  if (votedWithParty === null) return null;
  return votedWithParty ? (
    <span className="text-xs px-1.5 py-0.5 border text-ink-lo border-white/[0.07] bg-white/[0.03]">
      WITH PARTY
    </span>
  ) : (
    <span className="text-xs px-1.5 py-0.5 border text-signal-magenta border-signal-magenta/40 bg-signal-magenta/10 font-bold">
      AGAINST PARTY
    </span>
  );
}

function VoteBadge({ vote }: { vote: string }) {
  const styles =
    vote === "Yea"
      ? "text-ink-hi bg-white/[0.03] border-white/15"
      : vote === "Nay"
        ? "text-signal-red bg-signal-red border-signal-red/40"
        : "text-signal-amber bg-signal-amber border-yellow-500/30";
  return (
    <span className={`font-mono text-xs tracking-widest px-2 py-1 border ${styles}`}>
      {vote.toUpperCase()}
    </span>
  );
}

function VoteCard({ vote, expandable = false }: { vote: KeyVote; expandable?: boolean }) {
  const [expanded, setExpanded] = useState(false);

  const getVoteBorder = (v: string) => {
    if (v === "Yea") return "border-l-4 border-l-white/15";
    if (v === "Nay") return "border-l-4 border-l-red-500/40";
    return "border-l-4 border-l-yellow-500/30";
  };

  const voteColor =
    vote.vote === "Yea"
      ? "text-ink-hi"
      : vote.vote === "Nay"
        ? "text-signal-red"
        : "text-signal-amber";

  const borderClass = getVoteBorder(vote.vote);
  const sourceLink = voteSourceUrl(vote.billId);

  const detailBadges = (
    <div className="flex items-center gap-2 flex-wrap">
      <VoteBadge vote={vote.vote} />
      <PartyBadge leaning={vote.partyLeaning} />
      {vote.vote !== "Not Voting" && (
        <>
          <PartyAlignmentBadge votedWithParty={vote.votedWithParty} />
          {vote.policyArea !== "PROCEDURAL" &&
            (vote.policyAreas?.length > 0
              ? vote.policyAreas
                  .filter((a) => a.area !== "PROCEDURAL")
                  .map((a) => (
                    <span
                      key={a.area}
                      className={`text-xs px-1.5 py-0.5 border ${
                        a.party === "R"
                          ? "text-signal-red border-red-400/30 bg-red-400/5"
                          : a.party === "D"
                            ? "text-blue-400/70 border-blue-400/30 bg-blue-400/5"
                            : "text-signal-amber border-signal-amber/40 bg-signal-amber/10"
                      }`}
                      title={`${a.area} — ${a.party} aligned (${Math.round(a.confidence * 100)}%)`}
                    >
                      {a.area}
                    </span>
                  ))
              : vote.policyArea &&
                vote.policyArea !== "PROCEDURAL" && (
                  <span
                    className="text-xs px-1.5 py-0.5 border text-signal-amber border-signal-amber/40 bg-signal-amber/10"
                    title={vote.policyArea}
                  >
                    {vote.policyArea}
                  </span>
                ))}
        </>
      )}
    </div>
  );

  if (!expandable) {
    return (
      <div className={`panel p-3 ${borderClass}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <VoteBadge vote={vote.vote} />
          <span className="text-ink text-sm">{vote.billName}</span>
        </div>
        <div className="flex items-center gap-2 mt-1">
          {vote.date && <span className="text-xs text-ink-min">{vote.date}</span>}
          {sourceLink && (
            <a
              href={sourceLink}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-mono tracking-wide text-ink-lo hover:text-phos transition-colors"
            >
              SOURCE ↗
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`panel ${borderClass}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${vote.billName}: ${vote.vote}. ${expanded ? "Collapse" : "Expand"} details`}
        className="w-full text-left p-3 flex items-center justify-between gap-2"
      >
        <div className="flex-1 min-w-0">
          <span className="text-ink text-sm">{vote.billName}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`font-mono text-xs tracking-widest ${voteColor}`}>
            {vote.vote.toUpperCase()}
          </span>
          <span className="text-ink-min" aria-hidden="true">
            {expanded ? "−" : "+"}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-white/[0.07] pt-3 space-y-2 text-sm">
          {detailBadges}

          <div className="flex items-center gap-2 flex-wrap text-ink-lo">
            <span>
              {vote.billId} &mdash; {vote.date}
            </span>
            {sourceLink && (
              <a
                href={sourceLink}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-ink-lo hover:text-phos transition-colors border border-white/15 px-1.5 py-0.5"
              >
                VIEW ON CONGRESS.GOV
              </a>
            )}
          </div>

          {vote.description && vote.description !== vote.billName && (
            <p className="text-ink">{vote.description}</p>
          )}

          {vote.keyVoteReasoning && (
            <div className="bg-white/[0.03] border border-white/[0.07] p-2">
              <div className="text-xs text-ink-lo mb-1">WHY THIS VOTE MATTERS</div>
              <div className="text-xs text-ink">{vote.keyVoteReasoning}</div>
            </div>
          )}

          {vote.policyArea && vote.policyArea !== "PROCEDURAL" && (
            <div className="bg-signal-amber/10 border border-signal-amber/40 p-2">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="text-xs text-ink-lo">POLICY AREAS:</span>
                {(vote.policyAreas?.length > 0
                  ? vote.policyAreas.filter((a) => a.area !== "PROCEDURAL")
                  : [
                      {
                        area: vote.policyArea,
                        confidence: 1,
                        party: vote.partyLeaning || ("bipartisan" as const),
                      },
                    ]
                ).map((a) => (
                  <span
                    key={a.area}
                    className={`text-xs px-1.5 py-0.5 border ${
                      a.party === "R"
                        ? "text-signal-red border-red-400/30 bg-red-400/5"
                        : a.party === "D"
                          ? "text-blue-400/70 border-blue-400/30 bg-blue-400/5"
                          : "text-signal-amber border-signal-amber/40 bg-signal-amber/10"
                    }`}
                    title={`Confidence: ${Math.round(a.confidence * 100)}% — ${a.party} aligned`}
                  >
                    {a.area}
                    <span className="ml-1 opacity-50">
                      {a.party === "R" ? "R" : a.party === "D" ? "D" : "~"}
                    </span>
                  </span>
                ))}
                {vote.stance && (
                  <span className="text-xs text-ink-lo ml-1">STANCE: {vote.stance}</span>
                )}
              </div>
              {vote.partyAlignmentWeight > 0 && vote.partyAlignmentWeight < 1 && (
                <div className="text-xs text-ink-min mb-1">
                  Alignment weight: {Math.round(vote.partyAlignmentWeight * 100)}% of areas lean{" "}
                  {vote.partyLeaning}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
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

function Pagination({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
}) {
  if (totalPages <= 1) return null;

  const pages: (number | "...")[] = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== "...") {
      pages.push("...");
    }
  }

  return (
    <div className="flex items-center justify-center gap-1 mt-4">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page === 1}
        aria-label="Previous page"
        className="text-xs px-2 py-1 font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed"
      >
        &lt; PREV
      </button>
      {pages.map((p, i) =>
        p === "..." ? (
          <span key={`dot-${i}`} className="text-ink-min text-xs px-1">
            ...
          </span>
        ) : (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            aria-label={`Page ${p}`}
            aria-current={p === page ? "page" : undefined}
            className={`text-xs w-7 h-7 font-mono border transition-all ${
              p === page
                ? "text-ink-hi border-white/15 bg-white/[0.03]"
                : "text-ink-min border-transparent hover:border-white/[0.07]"
            }`}
          >
            {p}
          </button>
        )
      )}
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page === totalPages}
        aria-label="Next page"
        className="text-xs px-2 py-1 font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed"
      >
        NEXT &gt;
      </button>
    </div>
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
  const [filter, setFilter] = useState<VoteFilterType>("all");
  const [data, setData] = useState<PaginatedVotes | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchVotes = useCallback(
    async (p: number, f: VoteFilterType) => {
      setLoading(true);
      setError(null);
      try {
        const fetcher = chamber === "house" ? fetchRepVotes : fetchSenatorVotes;
        const result = await fetcher(senatorId, {
          category,
          page: p,
          perPage: VOTES_PER_PAGE,
          filter: f,
        });
        setData(result);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load votes");
      } finally {
        setLoading(false);
      }
    },
    [senatorId, category, chamber]
  );

  useEffect(() => {
    if (voteCount > 0) {
      fetchVotes(1, "all");
    }
  }, [fetchVotes, voteCount]);

  const handleFilterChange = (f: VoteFilterType) => {
    setFilter(f);
    fetchVotes(1, f);
  };

  const handlePageChange = (p: number) => {
    fetchVotes(p, filter);
  };

  if (voteCount === 0) return null;

  if (!data && loading) {
    return (
      <div className="panel p-4 text-center" role="status" aria-live="polite">
        <span className="text-ink-lo text-sm animate-pulse">Loading votes...</span>
      </div>
    );
  }

  if (error) {
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

      <div className="space-y-2">
        {data.votes.map((vote) => (
          <VoteCard key={`${category}-${vote.billId}`} vote={vote} expandable />
        ))}
      </div>

      <Pagination page={data.page} totalPages={data.totalPages} onPageChange={handlePageChange} />
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
    votingSummary,
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
          <MetricTooltip text="Total roll-call votes tracked from Congress.gov and Senate.gov for this senator across recent and key votes.">
            TOTAL TRACKED
          </MetricTooltip>
        </div>
      </div>
      <div className="panel p-3">
        <div className="text-xl font-display font-semibold text-signal-cyan">
          {Math.round(partyLoyaltyPct)}%
        </div>
        <div className="text-ink-min text-xs">
          <MetricTooltip text="How often this senator votes with the majority of their party. 100% = perfect party-line voter. Calculated from all scoreable roll-call votes.">
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
          <MetricTooltip text="How often this senator votes against their own party. Higher = more willingness to break from party leadership on roll-call votes.">
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
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo mb-1">
          <span>
            <span className="text-signal-red font-display font-semibold">R</span> =
            Republican-aligned bill
          </span>
          <span>
            <span className="text-dem-blue font-display font-semibold">D</span> = Democrat-aligned
            bill
          </span>
          <span>
            <span className="text-ind-purple font-display font-semibold">BP</span> = Bipartisan bill
          </span>
          <span>
            <span className="text-signal-magenta font-bold">AGAINST PARTY</span> = voted against own
            party
          </span>
        </div>
        {keyVoteCount > 0 && (
          <div>
            <div className="text-xs text-ink-lo mb-2 font-mono tracking-widest">
              KEY VOTES — LONG-TERM SUMMARY
            </div>
            {votingSummary && (
              <div className="panel p-3 mb-3">
                <p className="text-base text-ink leading-relaxed">{votingSummary}</p>
              </div>
            )}
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
