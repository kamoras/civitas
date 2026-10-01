"use client";

import { useCallback, useEffect } from "react";
import { PaginatedStockTrades, StockTrade } from "@/types/senator";
import { fetchPresidentStockTrades, fetchRepStockTrades, fetchSenatorStockTrades } from "@/lib/api";
import CollapsibleSection from "../shared/CollapsibleSection";
import Pagination from "../shared/Pagination";
import MetricTooltip from "./MetricTooltip";
import { formatBracket, OWNER_LABEL } from "@/lib/disclosures";
import { useLatestRequest } from "@/hooks/useLatestRequest";

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

function formatAmountRange(trade: StockTrade): string {
  // The top bracket on these forms discloses a floor and no ceiling — see
  // StockTrade.amountOpenEnded and formatBracket.
  return formatBracket(trade.amountLow, trade.amountHigh, trade.amountOpenEnded);
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
      title={`Disclosed ${daysToDisclose} day${daysToDisclose !== 1 ? "s" : ""} after the transaction: the STOCK Act requires disclosure within 45 days.`}
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
          {trade.ticker ? `${trade.ticker}: ${trade.assetName}` : trade.assetName}
        </span>
        {trade.parseConfidence === "ocr" && (
          <MetricTooltip text="Read by OCR from a scanned filing. The amount is one of the form's own ranges, but a digit of the date may be misread, so no timeliness is shown. A president's scanned periodic reports are replaced by the annual report, which lists the year's transactions as text, once it is filed.">
            <span className="text-xs px-1 py-0.5 border text-signal-amber border-signal-amber/40">
              READ FROM A SCAN
            </span>
          </MetricTooltip>
        )}
        {trade.reportKind === "annual" && (
          <MetricTooltip text="From the annual report (OGE Form 278e), which lists every transaction of the year. It does not say when each was first reported, so no timeliness is shown.">
            <span className="text-xs px-1 py-0.5 border text-ink-lo border-white/15">
              ANNUAL REPORT
            </span>
          </MetricTooltip>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap text-xs text-ink-min">
        <span>{OWNER_LABEL[trade.owner]}</span>
        <span
          title={
            trade.amountOpenEnded
              ? "The filing used the form's open-ended top bracket: it discloses a minimum and no maximum."
              : undefined
          }
        >
          {formatAmountRange(trade)}
        </span>
        {trade.industry !== "UNCLASSIFIED" && <span>{trade.industry}</span>}
        <span>
          {trade.transactionDate ??
            `date not legible in the scan${trade.disclosureDate ? ` · filed ${trade.disclosureDate}` : ""}`}
        </span>
        {trade.late !== null && trade.daysToDisclose !== null && (
          <TimelinessBadge late={trade.late} daysToDisclose={trade.daysToDisclose} />
        )}
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

const FETCHER = {
  senate: fetchSenatorStockTrades,
  house: fetchRepStockTrades,
  president: fetchPresidentStockTrades,
} as const;

const SOURCE_LABEL = {
  senate: "efdsearch.senate.gov",
  house: "disclosures-clerk.house.gov",
  president: "oge.gov (OGE Forms 278-T and 278e)",
} as const;

const ABOUT_DATA = {
  congress:
    "Disclosed under the STOCK Act (2012), which requires members of Congress to report stock transactions within 45 days. Informational only, not part of the overall score, since disclosure completeness varies widely per member.",
  president:
    "Every securities and virtual-currency purchase, sale, or exchange over $1,000 the president disclosed. For a year the annual report (OGE Form 278e) covers, its list of the year's transactions is the record; after it, the periodic reports (OGE Form 278-T) the STOCK Act requires within 45 days, which the White House files as scans. Amounts are the value ranges the forms report: they carry no cost basis or share count, so no profit or gain figure is shown or derived. Informational only, not part of the overall score.",
} as const;

export default function StockTrades({ politicianId, filer = "senate" }: StockTradesProps) {
  const { data, loading, error, request } = useLatestRequest<PaginatedStockTrades, null>(
    null,
    "Failed to load stock trades"
  );

  const fetchPage = useCallback(
    (p: number) =>
      request(null, () => FETCHER[filer](politicianId, { page: p, perPage: TRADES_PER_PAGE })),
    [request, politicianId, filer]
  );

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  // No upfront count is embedded on the politician payload (unlike lobbying
  // matches) — trades are fetched separately to keep that payload lean, so
  // the section only renders once we know there's something to show.
  if (!loading && (!data || data.total === 0) && !error) return null;

  // Nothing until the first response: most members disclose no trades, and
  // a loading panel that then vanishes shifts the whole scorecard below it.
  if (loading && !data) return null;

  if (error && !data) {
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
        {error && (
          // A failed page change keeps the last page that loaded on screen.
          <p className="text-signal-red text-sm" role="alert">
            {error}
          </p>
        )}
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
