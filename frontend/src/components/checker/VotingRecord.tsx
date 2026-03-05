"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyVote, PaginatedVotes, VoteCounts, VotingRecord as VotingRecordType } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";
import { voteSourceUrl } from "@/lib/sources";
import { fetchSenatorVotes } from "@/lib/api";
import CollapsibleSection from "./CollapsibleSection";
import MetricTooltip from "./MetricTooltip";

const VOTES_PER_PAGE = 15;

interface VotingRecordProps {
  senatorId: string;
  votingRecord: VotingRecordType;
}

const PARTY_BADGE: Record<string, { label: string; className: string }> = {
  R: { label: "R", className: "text-rep-red border-rep-red/30 bg-rep-red/10" },
  D: { label: "D", className: "text-dem-blue border-dem-blue/30 bg-dem-blue/10" },
  bipartisan: { label: "BP", className: "text-ind-purple border-ind-purple/30 bg-ind-purple/10" },
};

function PartyBadge({ leaning }: { leaning: string | null }) {
  if (!leaning) return null;
  const badge = PARTY_BADGE[leaning];
  if (!badge) return null;
  return (
    <span className={`text-[10px] px-1 py-0.5 border font-pixel ${badge.className}`}>
      {badge.label}
    </span>
  );
}

function PartyAlignmentBadge({ votedWithParty }: { votedWithParty: boolean | null }) {
  if (votedWithParty === null) return null;
  return votedWithParty ? (
    <span className="text-[10px] px-1.5 py-0.5 border text-matrix-green/50 border-matrix-green/20 bg-matrix-green/5">
      WITH PARTY
    </span>
  ) : (
    <span className="text-[10px] px-1.5 py-0.5 border text-neon-pink border-neon-pink/40 bg-neon-pink/10 font-bold">
      AGAINST PARTY
    </span>
  );
}

function VoteBadge({ vote }: { vote: string }) {
  const styles =
    vote === "Yea"
      ? "text-matrix-green bg-matrix-green/10 border-matrix-green/30"
      : vote === "Nay"
        ? "text-red-500 bg-red-500/10 border-red-500/30"
        : "text-yellow-500 bg-yellow-500/10 border-yellow-500/30";
  return (
    <span className={`font-pixel text-xs px-2 py-1 border ${styles}`}>
      {vote.toUpperCase()}
    </span>
  );
}

function VoteCard({
  vote,
  expandable = false,
}: {
  vote: KeyVote;
  expandable?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  const getVoteBorder = (v: string) => {
    if (v === "Yea") return "border-l-4 border-l-matrix-green/40";
    if (v === "Nay") return "border-l-4 border-l-red-500/40";
    return "border-l-4 border-l-yellow-500/30";
  };

  const voteColor =
    vote.vote === "Yea"
      ? "text-matrix-green"
      : vote.vote === "Nay"
        ? "text-red-500"
        : "text-yellow-500";

  const borderClass = getVoteBorder(vote.vote);
  const sourceLink = voteSourceUrl(vote.billId);

  const detailBadges = (
    <div className="flex items-center gap-2 flex-wrap">
      <VoteBadge vote={vote.vote} />
      <PartyBadge leaning={vote.partyLeaning} />
      {vote.vote !== "Not Voting" && (
        <>
          <PartyAlignmentBadge votedWithParty={vote.votedWithParty} />
          {vote.policyArea !== "PROCEDURAL" && (vote.policyAreas?.length > 0 ? (
            vote.policyAreas.filter(a => a.area !== "PROCEDURAL").map((a) => (
              <span
                key={a.area}
                className={`text-[10px] px-1.5 py-0.5 border ${
                  a.party === "R"
                    ? "text-red-400/70 border-red-400/30 bg-red-400/5"
                    : a.party === "D"
                    ? "text-blue-400/70 border-blue-400/30 bg-blue-400/5"
                    : "text-neon-yellow/70 border-neon-yellow/30 bg-neon-yellow/5"
                }`}
                title={`${a.area} — ${a.party} aligned (${Math.round(a.confidence * 100)}%)`}
              >
                {a.area}
              </span>
            ))
          ) : vote.policyArea && vote.policyArea !== "PROCEDURAL" && (
            <span
              className="text-[10px] px-1.5 py-0.5 border text-neon-yellow/70 border-neon-yellow/30 bg-neon-yellow/5"
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
      <div className={`terminal-window p-3 ${borderClass}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <VoteBadge vote={vote.vote} />
          <span className="text-matrix-green/80 text-sm">{vote.billName}</span>
        </div>
        <div className="flex items-center gap-2 mt-1">
          {vote.date && (
            <span className="text-[10px] text-matrix-green/30">{vote.date}</span>
          )}
          {sourceLink && (
            <a
              href={sourceLink}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] text-neon-cyan/40 hover:text-neon-cyan transition-colors"
            >
              [SOURCE]
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`terminal-window ${borderClass}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${vote.billName}: ${vote.vote}. ${expanded ? "Collapse" : "Expand"} details`}
        className="w-full text-left p-3 flex items-center justify-between gap-2"
      >
        <div className="flex-1 min-w-0">
          <span className="text-matrix-green/80 text-sm">{vote.billName}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`font-pixel text-xs ${voteColor}`}>
            {vote.vote.toUpperCase()}
          </span>
          <span className="text-matrix-green/40" aria-hidden="true">
            {expanded ? "[-]" : "[+]"}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-matrix-green/10 pt-3 space-y-2 text-sm">
          {detailBadges}

          <div className="flex items-center gap-2 flex-wrap text-matrix-green/50">
            <span>{vote.billId} &mdash; {vote.date}</span>
            {sourceLink && (
              <a
                href={sourceLink}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-neon-cyan/50 hover:text-neon-cyan transition-colors border border-neon-cyan/20 px-1.5 py-0.5"
              >
                VIEW ON CONGRESS.GOV
              </a>
            )}
          </div>

          {vote.description && vote.description !== vote.billName && (
            <p className="text-matrix-green/70">{vote.description}</p>
          )}

          {vote.keyVoteReasoning && (
            <div className="bg-matrix-green/5 border border-matrix-green/20 p-2">
              <div className="text-xs text-matrix-green/60 mb-1">WHY THIS VOTE MATTERS</div>
              <div className="text-xs text-matrix-green/80">{vote.keyVoteReasoning}</div>
            </div>
          )}

          {vote.policyArea && vote.policyArea !== "PROCEDURAL" && (
            <div className="bg-neon-yellow/5 border border-neon-yellow/20 p-2">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="text-xs text-neon-yellow/60">
                  POLICY AREAS:
                </span>
                {(vote.policyAreas?.length > 0
                  ? vote.policyAreas.filter(a => a.area !== "PROCEDURAL")
                  : [{ area: vote.policyArea, confidence: 1, party: vote.partyLeaning || "bipartisan" as const }]
                ).map((a) => (
                  <span
                    key={a.area}
                    className={`text-[10px] px-1.5 py-0.5 border ${
                      a.party === "R"
                        ? "text-red-400/70 border-red-400/30 bg-red-400/5"
                        : a.party === "D"
                        ? "text-blue-400/70 border-blue-400/30 bg-blue-400/5"
                        : "text-neon-yellow/70 border-neon-yellow/30 bg-neon-yellow/5"
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
                  <span className="text-[10px] text-neon-yellow/40 ml-1">
                    STANCE: {vote.stance}
                  </span>
                )}
              </div>
              {vote.partyAlignmentWeight > 0 && vote.partyAlignmentWeight < 1 && (
                <div className="text-[10px] text-matrix-green/40 mb-1">
                  Alignment weight: {Math.round(vote.partyAlignmentWeight * 100)}% of areas lean {vote.partyLeaning}
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
      className={`text-[10px] px-2 py-1 border font-terminal transition-all ${
        active
          ? "text-matrix-green border-matrix-green/40 bg-matrix-green/10"
          : "text-matrix-green/40 border-matrix-green/15 hover:border-matrix-green/30"
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
        className="text-xs px-2 py-1 font-terminal text-matrix-green/60 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed"
      >
        &lt; PREV
      </button>
      {pages.map((p, i) =>
        p === "..." ? (
          <span key={`dot-${i}`} className="text-matrix-green/30 text-xs px-1">...</span>
        ) : (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            className={`text-xs w-7 h-7 font-terminal border transition-all ${
              p === page
                ? "text-matrix-green border-matrix-green/40 bg-matrix-green/10"
                : "text-matrix-green/40 border-transparent hover:border-matrix-green/20"
            }`}
          >
            {p}
          </button>
        ),
      )}
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page === totalPages}
        className="text-xs px-2 py-1 font-terminal text-matrix-green/60 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed"
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
}: {
  senatorId: string;
  category: "recent" | "key";
  voteCount: number;
}) {
  const [filter, setFilter] = useState<VoteFilterType>("all");
  const [data, setData] = useState<PaginatedVotes | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchVotes = useCallback(async (p: number, f: VoteFilterType) => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchSenatorVotes(senatorId, {
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
  }, [senatorId, category]);

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
      <div className="terminal-window p-4 text-center">
        <span className="text-matrix-green/50 text-sm animate-pulse">Loading votes...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="terminal-window p-4 text-center">
        <span className="text-red-400 text-sm">{error}</span>
      </div>
    );
  }

  if (!data) return null;

  const counts: VoteCounts = data.counts;

  return (
    <div className={loading ? "opacity-60 transition-opacity" : ""}>
      {voteCount > VOTES_PER_PAGE && (
        <div className="flex items-center gap-1.5 mb-3 flex-wrap">
          <VoteFilter label="ALL" active={filter === "all"} count={counts.all} onClick={() => handleFilterChange("all")} />
          <VoteFilter label="YEA" active={filter === "yea"} count={counts.yea} onClick={() => handleFilterChange("yea")} />
          <VoteFilter label="NAY" active={filter === "nay"} count={counts.nay} onClick={() => handleFilterChange("nay")} />
          {counts.againstParty > 0 && (
            <VoteFilter label="AGAINST PARTY" active={filter === "against-party"} count={counts.againstParty} onClick={() => handleFilterChange("against-party")} />
          )}
          <span className="text-[10px] text-matrix-green/30 ml-auto">
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


export default function VotingRecord({ senatorId, votingRecord }: VotingRecordProps) {
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
      <div className="terminal-window p-3">
        <div className="text-xl font-pixel text-matrix-green">
          {totalVotes.toLocaleString()}
        </div>
        <div className="text-matrix-green/40 text-xs"><MetricTooltip text="Total roll-call votes tracked from Congress.gov and Senate.gov for this senator across recent and key votes.">TOTAL TRACKED</MetricTooltip></div>
      </div>
      <div className="terminal-window p-3">
        <div className="text-xl font-pixel text-neon-cyan">{Math.round(partyLoyaltyPct)}%</div>
        <div className="text-matrix-green/40 text-xs"><MetricTooltip text="How often this senator votes with the majority of their party. 100% = perfect party-line voter. Calculated from all scoreable roll-call votes.">PARTY LOYALTY</MetricTooltip></div>
        <div className="text-[10px] text-matrix-green/50">votes with party line</div>
      </div>
      <div className="terminal-window p-3">
        <div className="text-xl font-pixel text-neon-yellow">{partyIndependencePct}%</div>
        <div className="text-matrix-green/40 text-xs"><MetricTooltip text="How often this senator votes against their own party. Higher = more willingness to break from party leadership on roll-call votes.">INDEPENDENT</MetricTooltip></div>
        <div className="text-[10px] text-matrix-green/50">
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
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-matrix-green/50 mb-1">
          <span><span className="text-rep-red font-pixel">R</span> = Republican-aligned bill</span>
          <span><span className="text-dem-blue font-pixel">D</span> = Democrat-aligned bill</span>
          <span><span className="text-ind-purple font-pixel">BP</span> = Bipartisan bill</span>
          <span><span className="text-neon-pink font-bold">AGAINST PARTY</span> = voted against own party</span>
        </div>
        {keyVoteCount > 0 && (
          <div>
            <div className="text-xs text-neon-cyan/60 mb-2 font-pixel">
              {">"} KEY VOTES — LONG-TERM SUMMARY
            </div>
            {votingSummary && (
              <div className="terminal-window p-3 mb-3">
                <p className="text-sm text-matrix-green/80 leading-relaxed">{votingSummary}</p>
              </div>
            )}
            <PaginatedVoteList senatorId={senatorId} category="key" voteCount={keyVoteCount} />
          </div>
        )}

        {recentVoteCount > 0 && (
          <div>
            <div className="text-xs text-neon-cyan/60 mb-2 font-pixel">
              {">"} RECENT VOTES ({recentVoteCount})
            </div>
            <PaginatedVoteList senatorId={senatorId} category="recent" voteCount={recentVoteCount} />
          </div>
        )}
      </div>
    </CollapsibleSection>
  );
}
