"use client";

import { useEffect, useState } from "react";
import BillRow from "./BillRow";
import { fetchBillsInFlight } from "@/lib/api";
import { useConfig } from "@/hooks/useConfig";
import type { BillInFlight } from "@/types/bill";

const GROUP_PER_PAGE = 25;

type RowSort = "recent" | "stale";

interface BillStageGroupProps {
  stageCode: string;
  /** This stage's filtered total, from the parent's single stage-counts
   * fetch (stageCounts already reflects chamber/party/q server-side).
   * When provided the group renders its header with zero requests of its
   * own; when omitted it falls back to probing for a count itself. */
  count?: number;
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
  forceExpanded?: boolean;
}

export default function BillStageGroup({ stageCode, count, chamber, party, q, forceExpanded }: BillStageGroupProps) {
  const config = useConfig();
  const stageInfo = config?.billStages?.[stageCode];
  const color = stageInfo?.color ?? "#00ff41";

  const [probedTotal, setProbedTotal] = useState<number | null>(null);
  const [rowsTotal, setRowsTotal] = useState<number | null>(null);
  const [bills, setBills] = useState<BillInFlight[]>([]);
  const [page, setPage] = useState(0); // 0 = rows not fetched yet
  const [totalPages, setTotalPages] = useState(0);
  const [expanded, setExpanded] = useState(!!forceExpanded);
  const [loading, setLoading] = useState(false);
  const [sort, setSort] = useState<RowSort>("recent");

  // The parent's count wins; a rows fetch refines it (same stage-filtered
  // total, fresher); the probe is the no-count fallback.
  const total = count ?? rowsTotal ?? probedTotal;

  // Filters changed (or this group mounted) — reset loaded rows. A
  // forceExpanded group starts expanded and fetches rows immediately
  // (that response carries the header total too).
  useEffect(() => {
    setBills([]);
    setPage(0);
    setRowsTotal(null);
    setExpanded(!!forceExpanded);
  }, [stageCode, chamber, party, q, forceExpanded]);

  // Fallback header count, only when the parent supplied none and rows
  // aren't being fetched anyway (e.g. the parent's counts fetch failed).
  useEffect(() => {
    if (count !== undefined || forceExpanded) return;
    let cancelled = false;
    setProbedTotal(null);
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort: "recent", page: 1, perPage: 1 })
      .then((res) => { if (!cancelled) setProbedTotal(res.total); })
      .catch(() => { if (!cancelled) setProbedTotal(0); });
    return () => { cancelled = true; };
  }, [count, forceExpanded, stageCode, chamber, party, q]);

  // Sort choice changed — throw away whatever's loaded and refetch page 1.
  useEffect(() => {
    setBills([]);
    setPage(0);
  }, [sort]);

  // Load the first page of rows the first time this group is expanded
  // (or after a sort change reset page back to 0).
  useEffect(() => {
    if (!expanded || page !== 0) return;
    let cancelled = false;
    setLoading(true);
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort, page: 1, perPage: GROUP_PER_PAGE })
      .then((res) => {
        if (cancelled) return;
        setBills(res.bills);
        setPage(1);
        setTotalPages(res.totalPages);
        setRowsTotal(res.total);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [expanded, page, stageCode, chamber, party, q, sort]);

  const loadMore = () => {
    const nextPage = page + 1;
    setLoading(true);
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort, page: nextPage, perPage: GROUP_PER_PAGE })
      .then((res) => {
        setBills((prev) => [...prev, ...res.bills]);
        setPage(nextPage);
        setTotalPages(res.totalPages);
      })
      .finally(() => setLoading(false));
  };

  if (total === 0) return null;

  return (
    <div className="border border-matrix-green/10">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors text-left"
        aria-expanded={expanded}
      >
        <span className="font-mono text-[10px] w-3 text-matrix-green/40">{expanded ? "▼" : "▶"}</span>
        <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: color }} />
        <span className="font-mono text-xs uppercase tracking-wider text-matrix-green/80 flex-1">
          {stageInfo?.name ?? stageCode}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-matrix-green/40">
          {total === null ? "…" : total.toLocaleString()}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-matrix-green/10">
          <div className="flex items-center justify-end gap-1 px-3 py-1.5 border-b border-matrix-green/10">
            <span className="font-mono text-[9px] text-matrix-green/25 uppercase tracking-widest mr-1">Sort</span>
            {([
              { value: "recent" as const, label: "Newest" },
              { value: "stale" as const, label: "Stuck longest" },
            ]).map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setSort(opt.value)}
                aria-pressed={sort === opt.value}
                className={`font-mono text-[9px] px-2 py-0.5 border transition-colors uppercase tracking-wider ${
                  sort === opt.value
                    ? "border-neon-cyan/50 text-neon-cyan bg-neon-cyan/10"
                    : "border-matrix-green/15 text-matrix-green/35 hover:text-matrix-green/60"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div className="divide-y divide-matrix-green/10">
            {bills.map((bill) => (
              <BillRow key={`${bill.chamber}-${bill.billId}-${bill.sponsorId}`} bill={bill} />
            ))}
            {loading && (
              <div className="text-center py-4 font-mono text-[10px] text-matrix-green/30 tracking-widest animate-pulse">
                LOADING...
              </div>
            )}
            {!loading && page > 0 && page < totalPages && (
              <div className="flex justify-center py-3">
                <button
                  onClick={loadMore}
                  className="font-mono text-[10px] tracking-widest px-3 py-1.5 border border-matrix-green/20 text-matrix-green/60 hover:text-matrix-green hover:border-matrix-green/40 transition-colors"
                >
                  LOAD MORE ({((total ?? 0) - bills.length).toLocaleString()} REMAINING)
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
