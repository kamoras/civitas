"use client";

import { useCallback, useEffect, useState } from "react";
import { PaginatedStockTrades, StockTrade } from "@/types/senator";
import { fetchPresidentStockTrades, fetchRepStockTrades, fetchSenatorStockTrades } from "@/lib/api";
import CollapsibleSection from "../shared/CollapsibleSection";
import MetricTooltip from "./MetricTooltip";

const TRADES_PER_PAGE = 15;

interface StockTradesProps {
  politicianId: string;
  /** "president" reads OGE Form 278-T filings instead of a congressional
   * PTR. Same disclosed fields, same 45-day deadline, same parser — only
   * the form and the source agency differ, so the whole component is
   * shared rather than duplicated. */
  filer?: "senate" | "house" | "president";
}

const TXN_TYPE_LABEL: Record<StockTrade["transactionType"], string> = {
  purchase: "BUY",
  sale_full: "SELL",
  sale_partial: "SELL (PARTIAL)",
  exchange: "EXCHANGE",
};

const OWNER_LABEL: Record<StockTrade["owner"], string> = {
  self: "SELF",
  spouse: "SPOUSE",
  joint: "JOINT",
  dependent: "DEPENDENT",
};

function formatAmountRange(low: number, high: number): string {
  const fmt = (n: number) => `$${n.toLocaleString()}`;
  return `${fmt(low)} – ${fmt(high)}`;
}

function TransactionBadge({ type }: { type: StockTrade["transactionType"] }) {
  const styles =
    type === "purchase"
      ? "text-matrix-green bg-matrix-green/10 border-matrix-green/30"
      : type === "exchange"
        ? "text-yellow-500 bg-yellow-500/10 border-yellow-500/30"
        : "text-red-500 bg-red-500/10 border-red-500/30";
  return (
    <span className={`font-mono text-xs tracking-widest px-2 py-1 border ${styles}`}>
      {TXN_TYPE_LABEL[type]}
    </span>
  );
}

function TimelinessBadge({ late, daysToDisclose }: { late: boolean; daysToDisclose: number }) {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 border font-mono ${
        late
          ? "text-neon-pink border-neon-pink/40 bg-neon-pink/10 font-bold"
          : "text-matrix-green/50 border-matrix-green/20 bg-matrix-green/5"
      }`}
      title={`Disclosed ${daysToDisclose} day${daysToDisclose !== 1 ? "s" : ""} after the transaction — the STOCK Act requires disclosure within 45 days.`}
    >
      {late ? "LATE DISCLOSURE" : "ON TIME"}
    </span>
  );
}

function TradeRow({ trade }: { trade: StockTrade }) {
  return (
    <div className="terminal-window p-3">
      <div className="flex items-center gap-2 flex-wrap mb-1">
        <TransactionBadge type={trade.transactionType} />
        <span className="text-matrix-green/80 text-sm">
          {trade.ticker ? `${trade.ticker} — ${trade.assetName}` : trade.assetName}
        </span>
        {trade.parseConfidence === "ocr" && (
          <span
            className="text-[10px] px-1 py-0.5 border text-yellow-500/70 border-yellow-500/30"
            title="Extracted via OCR from a scanned filing — verify against the source before relying on exact figures."
          >
            LOW CONFIDENCE
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap text-[10px] text-matrix-green/40">
        <span>{OWNER_LABEL[trade.owner]}</span>
        <span>{formatAmountRange(trade.amountLow, trade.amountHigh)}</span>
        {trade.industry !== "UNCLASSIFIED" && <span>{trade.industry}</span>}
        <span>{trade.transactionDate}</span>
        <TimelinessBadge late={trade.late} daysToDisclose={trade.daysToDisclose} />
        <a
          href={trade.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-neon-cyan/40 hover:text-neon-cyan transition-colors"
        >
          SOURCE ↗
        </a>
      </div>
    </div>
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
  return (
    <div className="flex items-center justify-center gap-2 mt-4">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page === 1}
        aria-label="Previous page"
        className="text-xs px-2 py-1 font-terminal text-matrix-green/60 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed"
      >
        &lt; PREV
      </button>
      <span className="text-xs text-matrix-green/40">
        page {page}/{totalPages}
      </span>
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page === totalPages}
        aria-label="Next page"
        className="text-xs px-2 py-1 font-terminal text-matrix-green/60 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed"
      >
        NEXT &gt;
      </button>
    </div>
  );
}

const FETCHER = {
  senate: fetchSenatorStockTrades,
  house: fetchRepStockTrades,
  president: fetchPresidentStockTrades,
} as const;

const SOURCE_LABEL = {
  senate: "efdsearch.senate.gov",
  house: "disclosures-clerk.house.gov",
  president: "oge.gov (OGE Form 278-T)",
} as const;

const ABOUT_DATA = {
  congress:
    "Disclosed under the STOCK Act (2012), which requires members of Congress to report stock transactions within 45 days. Informational only — not part of the overall score, since disclosure completeness varies widely per member.",
  president:
    "Every securities and virtual-currency purchase, sale, or exchange over $1,000 the president disclosed on OGE Form 278-T, which the STOCK Act requires within 45 days of the transaction. Amounts are the value ranges the form reports — it carries no cost basis or share count, so no profit or gain figure is shown or derived. Informational only, not part of the overall score.",
} as const;

export default function StockTrades({ politicianId, filer = "senate" }: StockTradesProps) {
  const [data, setData] = useState<PaginatedStockTrades | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(async (p: number) => {
    setLoading(true);
    setError(null);
    try {
      const result = await FETCHER[filer](politicianId, { page: p, perPage: TRADES_PER_PAGE });
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load stock trades");
    } finally {
      setLoading(false);
    }
  }, [politicianId, filer]);

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  // No upfront count is embedded on the politician payload (unlike lobbying
  // matches) — trades are fetched separately to keep that payload lean, so
  // the section only renders once we know there's something to show.
  if (!loading && (!data || data.total === 0) && !error) return null;

  if (loading && !data) {
    return (
      <div className="terminal-window p-4 text-center" role="status" aria-live="polite">
        <span className="text-matrix-green/50 text-sm animate-pulse">Loading stock trades...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="terminal-window p-4 text-center" role="alert">
        <span className="text-red-400 text-sm">{error}</span>
      </div>
    );
  }

  if (!data) return null;

  return (
    <CollapsibleSection
      title={filer === "president" ? "STOCK & CRYPTO TRADES" : "STOCK TRADES"}
      titleColor="text-neon-yellow neon-yellow"
      summary={`${data.total} trade${data.total !== 1 ? "s" : ""}${data.lateCount > 0 ? ` · ${data.lateCount} late` : ""}`}
      source={SOURCE_LABEL[filer]}
    >
      <div className="space-y-3 mt-4">
        <p className="text-[10px] text-matrix-green/40">
          <MetricTooltip text={filer === "president" ? ABOUT_DATA.president : ABOUT_DATA.congress}>
            ABOUT THIS DATA
          </MetricTooltip>
        </p>
        <div className={`space-y-2 ${loading ? "opacity-60 transition-opacity" : ""}`}>
          {data.trades.map((trade, i) => (
            <TradeRow key={`${trade.sourceUrl}-${i}`} trade={trade} />
          ))}
        </div>
        <Pagination page={data.page} totalPages={data.totalPages} onPageChange={fetchPage} />
      </div>
    </CollapsibleSection>
  );
}
