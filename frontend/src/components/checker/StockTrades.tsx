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

function formatAmountRange(trade: StockTrade): string {
  const fmt = (n: number) => `$${n.toLocaleString()}`;
  // The top bracket on these forms discloses a floor and no ceiling, so
  // there is no upper figure to show — see StockTrade.amountOpenEnded.
  return trade.amountOpenEnded
    ? `${fmt(trade.amountLow)}+`
    : `${fmt(trade.amountLow)} – ${fmt(trade.amountHigh)}`;
}

function TransactionBadge({ type }: { type: StockTrade["transactionType"] }) {
  const styles =
    type === "purchase"
      ? "text-ink-hi bg-white/[0.03] border-white/15"
      : type === "exchange"
        ? "text-signal-amber bg-signal-amber/10 border-signal-amber/40"
        : "text-signal-red bg-signal-red/10 border-signal-red/40";
  return (
    <span className={`font-mono text-xs tracking-widest px-2 py-1 border ${styles}`}>
      {TXN_TYPE_LABEL[type]}
    </span>
  );
}

function TimelinessBadge({ late, daysToDisclose }: { late: boolean; daysToDisclose: number }) {
  return (
    <span
      className={`text-xs px-1.5 py-0.5 border font-mono ${
        late
          ? "text-signal-magenta border-signal-magenta/40 bg-signal-magenta/10 font-bold"
          : "text-ink-lo border-white/[0.07] bg-white/[0.03]"
      }`}
      title={`Disclosed ${daysToDisclose} day${daysToDisclose !== 1 ? "s" : ""} after the transaction — the STOCK Act requires disclosure within 45 days.`}
    >
      {late ? "LATE DISCLOSURE" : "ON TIME"}
    </span>
  );
}

function TradeRow({ trade }: { trade: StockTrade }) {
  return (
    <div className="panel p-3">
      <div className="flex items-center gap-2 flex-wrap mb-1">
        <TransactionBadge type={trade.transactionType} />
        <span className="text-ink text-sm">
          {trade.ticker ? `${trade.ticker} — ${trade.assetName}` : trade.assetName}
        </span>
        {trade.parseConfidence === "ocr" && (
          <span
            className="text-xs px-1 py-0.5 border text-signal-amber border-signal-amber/40"
            title="Extracted via OCR from a scanned filing — verify against the source before relying on exact figures."
          >
            LOW CONFIDENCE
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap text-xs text-ink-min">
        <span>{OWNER_LABEL[trade.owner]}</span>
        <span
          title={
            trade.amountOpenEnded
              ? "The filing used the form's open-ended top bracket — it discloses a minimum and no maximum."
              : undefined
          }
        >
          {formatAmountRange(trade)}
        </span>
        {trade.industry !== "UNCLASSIFIED" && <span>{trade.industry}</span>}
        <span>{trade.transactionDate}</span>
        <TimelinessBadge late={trade.late} daysToDisclose={trade.daysToDisclose} />
        <a
          href={trade.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-ink-lo hover:text-phos transition-colors"
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
        className="text-xs px-2 py-1 font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed"
      >
        &lt; PREV
      </button>
      <span className="text-xs text-ink-min">
        page {page}/{totalPages}
      </span>
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

  const fetchPage = useCallback(
    async (p: number) => {
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
    },
    [politicianId, filer]
  );

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  // No upfront count is embedded on the politician payload (unlike lobbying
  // matches) — trades are fetched separately to keep that payload lean, so
  // the section only renders once we know there's something to show.
  if (!loading && (!data || data.total === 0) && !error) return null;

  if (loading && !data) {
    return (
      <div className="panel p-4 text-center" role="status" aria-live="polite">
        <span className="text-ink-lo text-sm animate-pulse">Loading stock trades...</span>
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

  return (
    <CollapsibleSection
      title={filer === "president" ? "STOCK & CRYPTO TRADES" : "STOCK TRADES"}
      titleColor="text-signal-amber"
      summary={`${data.total} trade${data.total !== 1 ? "s" : ""}${data.lateCount > 0 ? ` · ${data.lateCount} late` : ""}`}
      source={SOURCE_LABEL[filer]}
    >
      <div className="space-y-3 mt-4">
        <p className="text-xs text-ink-min">
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
