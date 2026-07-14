"use client";

import { useEffect, useState } from "react";
import BillRow from "./BillRow";
import { fetchBillsInFlight } from "@/lib/api";
import { useConfig } from "@/hooks/useConfig";
import type { BillInFlight } from "@/types/bill";

const GROUP_PER_PAGE = 25;
// Below this a group's rows fit on one screen, so showing them up front
// costs little; above it (e.g. IN_COMMITTEE's several thousand) default to
// collapsed so the page doesn't open already showing an unscannable wall.
const AUTO_EXPAND_THRESHOLD = 200;

interface BillStageGroupProps {
  stageCode: string;
  chamber?: "senate" | "house";
  party?: "D" | "R" | "I";
  q?: string;
  forceExpanded?: boolean;
}

export default function BillStageGroup({ stageCode, chamber, party, q, forceExpanded }: BillStageGroupProps) {
  const config = useConfig();
  const stageInfo = config?.billStages?.[stageCode];
  const color = stageInfo?.color ?? "#00ff41";

  const [total, setTotal] = useState<number | null>(null);
  const [bills, setBills] = useState<BillInFlight[]>([]);
  const [page, setPage] = useState(0); // 0 = rows not fetched yet
  const [totalPages, setTotalPages] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  // Filters changed (or this group mounted) — get an accurate count for
  // the header regardless of expand state, and decide the default.
  useEffect(() => {
    let cancelled = false;
    setTotal(null);
    setBills([]);
    setPage(0);
    setExpanded(false);
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort: "recent", page: 1, perPage: 1 })
      .then((res) => {
        if (cancelled) return;
        setTotal(res.total);
        if (forceExpanded || (res.total > 0 && res.total <= AUTO_EXPAND_THRESHOLD)) {
          setExpanded(true);
        }
      })
      .catch(() => { if (!cancelled) setTotal(0); });
    return () => { cancelled = true; };
  }, [stageCode, chamber, party, q, forceExpanded]);

  // Load the first page of rows the first time this group is expanded.
  useEffect(() => {
    if (!expanded || page !== 0) return;
    let cancelled = false;
    setLoading(true);
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort: "recent", page: 1, perPage: GROUP_PER_PAGE })
      .then((res) => {
        if (cancelled) return;
        setBills(res.bills);
        setPage(1);
        setTotalPages(res.totalPages);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [expanded, page, stageCode, chamber, party, q]);

  const loadMore = () => {
    const nextPage = page + 1;
    setLoading(true);
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort: "recent", page: nextPage, perPage: GROUP_PER_PAGE })
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
        <div className="divide-y divide-matrix-green/10 border-t border-matrix-green/10">
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
      )}
    </div>
  );
}
