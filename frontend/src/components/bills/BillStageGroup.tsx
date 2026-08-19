"use client";

import { useEffect, useState } from "react";
import BillRow from "./BillRow";
import { fetchBillsInFlight } from "@/lib/api";
import { useConfig } from "@/hooks/useConfig";
import type { BillInFlight } from "@/types/bill";
import { BOXED_CONTROL } from "@/lib/controlStyles";

const EMPTY_BILLS: BillInFlight[] = [];
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

export default function BillStageGroup({
  stageCode,
  count,
  chamber,
  party,
  q,
  forceExpanded,
}: BillStageGroupProps) {
  const config = useConfig();
  const stageInfo = config?.billStages?.[stageCode];
  const color = stageInfo?.color ?? "#00ff41";

  /* Everything below hangs off one signature. Previously each of these lived
     in its own piece of state, kept in step by four effects — two of which
     existed only to reset the other state when a prop changed, and two of
     which announced "loading" synchronously before their request went out.
     That is four extra render passes per filter change, and four chances for
     the pieces to disagree.

     Now the rows, the page depth and the totals are one object stamped with
     the request signature they belong to. A signature mismatch IS the reset,
     evaluated during render, and "loading" is derived from it rather than
     stored. Nothing is written outside a settled promise. */
  const [sort, setSort] = useState<RowSort>("recent");

  const sig = `${stageCode}:${chamber ?? ""}:${party ?? ""}:${q ?? ""}:${sort}`;

  const [rows, setRows] = useState<{
    sig: string;
    bills: BillInFlight[];
    page: number;
    totalPages: number;
    total: number;
  } | null>(null);

  const [probe, setProbe] = useState<{ sig: string; total: number } | null>(null);

  // A group opened by the parent starts open; one the reader opened stays open
  // until they close it, but a filter change closes it again — which is what
  // the old reset effect did by calling setExpanded in an effect body.
  const [openedSig, setOpenedSig] = useState<string | null>(null);
  const expanded = forceExpanded === true || openedSig === sig;

  const current = rows?.sig === sig ? rows : null;
  const bills = current?.bills ?? EMPTY_BILLS;
  const page = current?.page ?? 0;
  const totalPages = current?.totalPages ?? 0;
  const rowsTotal = current?.total ?? null;
  const probedTotal = probe?.sig === sig ? probe.total : null;
  const loading = expanded && current === null;

  // The parent's count wins; a rows fetch refines it (same stage-filtered
  // total, fresher); the probe is the no-count fallback.
  const total = count ?? rowsTotal ?? probedTotal;

  // Fallback header count, only when the parent supplied none and rows aren't
  // being fetched anyway (e.g. the parent's counts fetch failed).
  useEffect(() => {
    if (count !== undefined || forceExpanded) return;
    if (probe?.sig === sig) return;
    let cancelled = false;
    fetchBillsInFlight({ stage: stageCode, chamber, party, q, sort: "recent", page: 1, perPage: 1 })
      .then((res) => {
        if (!cancelled) setProbe({ sig, total: res.total });
      })
      .catch(() => {
        if (!cancelled) setProbe({ sig, total: 0 });
      });
    return () => {
      cancelled = true;
    };
  }, [count, forceExpanded, stageCode, chamber, party, q, sig, probe]);

  // First page of rows, the first time this group is expanded under this
  // signature.
  useEffect(() => {
    if (!expanded || rows?.sig === sig) return;
    let cancelled = false;
    fetchBillsInFlight({
      stage: stageCode,
      chamber,
      party,
      q,
      sort,
      page: 1,
      perPage: GROUP_PER_PAGE,
    })
      .then((res) => {
        if (cancelled) return;
        setRows({ sig, bills: res.bills, page: 1, totalPages: res.totalPages, total: res.total });
      })
      .catch(() => {
        if (cancelled) return;
        setRows({ sig, bills: [], page: 1, totalPages: 0, total: 0 });
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, sig, rows, stageCode, chamber, party, q, sort]);

  const [loadingMore, setLoadingMore] = useState(false);

  const loadMore = () => {
    const nextPage = page + 1;
    // An explicit click, not an effect — setting state in an event handler is
    // exactly where it belongs.
    setLoadingMore(true);
    fetchBillsInFlight({
      stage: stageCode,
      chamber,
      party,
      q,
      sort,
      page: nextPage,
      perPage: GROUP_PER_PAGE,
    })
      .then((res) => {
        setRows((prev) =>
          prev?.sig === sig
            ? {
                ...prev,
                bills: [...prev.bills, ...res.bills],
                page: nextPage,
                totalPages: res.totalPages,
              }
            : prev
        );
      })
      .finally(() => setLoadingMore(false));
  };

  if (total === 0) return null;

  return (
    <div className="border border-white/[0.07]">
      <button
        type="button"
        onClick={() => setOpenedSig((open) => (open === sig ? null : sig))}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors text-left"
        aria-expanded={expanded}
      >
        <span className="font-mono text-xs w-3 text-ink-min">{expanded ? "▼" : "▶"}</span>
        <span className="w-2 h-2 shrink-0" style={{ backgroundColor: color }} />
        <span className="font-mono text-xs uppercase tracking-wider text-ink flex-1">
          {stageInfo?.name ?? stageCode}
        </span>
        <span className="font-mono text-xs tabular-nums text-ink-min">
          {total === null ? "…" : total.toLocaleString()}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-white/[0.07]">
          <div className="flex items-center justify-end gap-1 px-3 py-1.5 border-b border-white/[0.07]">
            <span className="font-mono text-xs text-ink-min uppercase tracking-widest mr-1">
              Sort
            </span>
            {[
              { value: "recent" as const, label: "Newest" },
              { value: "stale" as const, label: "Stuck longest" },
            ].map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setSort(opt.value)}
                aria-pressed={sort === opt.value}
                className={`font-mono text-xs px-2 py-0.5 border transition-colors uppercase tracking-wider ${
                  sort === opt.value
                    ? BOXED_CONTROL.selected
                    : BOXED_CONTROL.unselected
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div className="divide-y divide-white/[0.07]">
            {bills.map((bill) => (
              <BillRow key={`${bill.chamber}-${bill.billId}-${bill.sponsorId}`} bill={bill} />
            ))}
            {(loading || loadingMore) && (
              <div className="py-4 text-center font-mono text-xs tracking-widest text-ink-min">
                {loading ? "LOADING…" : "LOADING MORE…"}
              </div>
            )}
            {!loading && !loadingMore && page > 0 && page < totalPages && (
              <div className="flex justify-center py-3">
                <button
                  onClick={loadMore}
                  className="border border-white/[0.07] px-3 py-1.5 font-mono text-xs tracking-widest text-ink-lo transition-colors hover:border-white/15 hover:text-phos"
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
