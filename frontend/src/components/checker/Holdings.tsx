"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Holding, HoldingCategory, Holdings as HoldingsData } from "@/types/senator";
import { fetchRepHoldings, fetchSenatorHoldings } from "@/lib/api";
import { formatCurrency } from "@/lib/formatting";
import CollapsibleSection from "../shared/CollapsibleSection";
import Pagination from "../shared/Pagination";
import MetricTooltip from "./MetricTooltip";

const HOLDINGS_PER_PAGE = 15;

const FETCHER = {
  senate: fetchSenatorHoldings,
  house: fetchRepHoldings,
} as const;

const SOURCE_LABEL = {
  senate: "efdsearch.senate.gov",
  house: "disclosures-clerk.house.gov",
} as const;

const OWNER_LABEL: Record<Holding["owner"], string> = {
  self: "SELF",
  spouse: "SPOUSE",
  joint: "JOINT",
  dependent: "DEPENDENT",
};

const ABOUT_TEXT =
  "Every asset listed on this member's most recent annual financial disclosure report — held by the member, their spouse, or a dependent child at the end of the year. The Ethics in Government Act requires values to be reported in ranges (for example $15,001 – $50,000), never as exact amounts, so no net-worth figure is computed. Slices are sized by the midpoint of each range (the minimum, for the open-ended top range); the ranges themselves are what the member disclosed. The asset type is the one the member chose when filing. Informational only — not part of the overall score.";

function formatHoldingValue(h: Holding): string {
  const fmt = (n: number) => `$${n.toLocaleString()}`;
  if (h.valueLow === null || h.valueHigh === null) return h.valueText || "Not stated";
  if (h.valueOpenEnded) return `${fmt(h.valueLow)}+`;
  if (h.valueHigh === 0) return "None at year end";
  return `${fmt(h.valueLow)} – ${fmt(h.valueHigh)}`;
}

function formatRangeCompact(low: number, high: number, openEnded: boolean): string {
  return `${formatCurrency(low)} – ${formatCurrency(high)}${openEnded ? "+" : ""}`;
}

function formatShare(share: number): string {
  if (share > 0 && share < 0.01) return "<1%";
  return `${Math.round(share * 100)}%`;
}

// Donut geometry, in SVG user units.
const SIZE = 200;
const CENTER = SIZE / 2;
const OUTER_R = 96;
const INNER_R = 62;

function arcPath(start: number, end: number): string {
  // Angles in turns (0..1), clockwise from 12 o'clock.
  const point = (t: number, r: number) => {
    const a = 2 * Math.PI * t - Math.PI / 2;
    return `${CENTER + r * Math.cos(a)} ${CENTER + r * Math.sin(a)}`;
  };
  const large = end - start > 0.5 ? 1 : 0;
  return [
    `M ${point(start, OUTER_R)}`,
    `A ${OUTER_R} ${OUTER_R} 0 ${large} 1 ${point(end, OUTER_R)}`,
    `L ${point(end, INNER_R)}`,
    `A ${INNER_R} ${INNER_R} 0 ${large} 0 ${point(start, INNER_R)}`,
    "Z",
  ].join(" ");
}

function HoldingsDonut({
  categories,
  active,
  selected,
  onHover,
  onSelect,
  holdingsCount,
}: {
  categories: HoldingCategory[];
  active: string | null;
  selected: string | null;
  onHover: (key: string | null) => void;
  onSelect: (key: string) => void;
  holdingsCount: number;
}) {
  const focus = categories.find((c) => c.category === (active ?? selected)) ?? null;
  const label = categories.map((c) => `${c.label} ${formatShare(c.share)}`).join(", ");

  let cursor = 0;
  return (
    <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width={SIZE}
        height={SIZE}
        role="img"
        aria-label={`Holdings by asset type: ${label}`}
        onMouseLeave={() => onHover(null)}
      >
        {categories.length === 1 ? (
          <circle
            cx={CENTER}
            cy={CENTER}
            r={(OUTER_R + INNER_R) / 2}
            fill="none"
            stroke={categories[0].color}
            strokeWidth={OUTER_R - INNER_R}
            onMouseEnter={() => onHover(categories[0].category)}
            onClick={() => onSelect(categories[0].category)}
            className="cursor-pointer"
          />
        ) : (
          categories.map((c) => {
            const start = cursor;
            cursor += c.share;
            const dimmed = (active ?? selected) !== null && (active ?? selected) !== c.category;
            return (
              <path
                key={c.category}
                d={arcPath(start, cursor)}
                fill={c.color}
                // A 2px surface-coloured seam separates adjacent slices so
                // identity never rests on hue alone at the boundary.
                stroke="var(--surface)"
                strokeWidth={2}
                strokeLinejoin="round"
                opacity={dimmed ? 0.35 : 1}
                onMouseEnter={() => onHover(c.category)}
                onClick={() => onSelect(c.category)}
                className="cursor-pointer transition-opacity"
              />
            );
          })
        )}
      </svg>
      <div
        className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none px-12"
        aria-hidden="true"
      >
        {focus ? (
          <>
            <span className="text-ink-hi text-xl font-mono">{formatShare(focus.share)}</span>
            <span className="text-ink text-xs leading-tight">{focus.label}</span>
            <span className="text-ink-min text-[11px] font-mono mt-0.5">
              {formatRangeCompact(focus.valueLow, focus.valueHigh, focus.openEnded)}
            </span>
          </>
        ) : (
          <>
            <span className="text-ink-hi text-xl font-mono">{holdingsCount}</span>
            <span className="text-ink-lo text-xs">asset{holdingsCount !== 1 ? "s" : ""}</span>
          </>
        )}
      </div>
    </div>
  );
}

function CategoryLegend({
  categories,
  selected,
  onHover,
  onSelect,
}: {
  categories: HoldingCategory[];
  selected: string | null;
  onHover: (key: string | null) => void;
  onSelect: (key: string) => void;
}) {
  return (
    <ul className="flex-1 min-w-0 space-y-1" aria-label="Holdings by asset type — select one to list only those assets">
      {categories.map((c) => {
        const isSelected = selected === c.category;
        return (
          <li key={c.category}>
            <button
              type="button"
              aria-pressed={isSelected}
              onClick={() => onSelect(c.category)}
              onMouseEnter={() => onHover(c.category)}
              onMouseLeave={() => onHover(null)}
              onFocus={() => onHover(c.category)}
              onBlur={() => onHover(null)}
              className={`w-full grid grid-cols-[12px_1fr_auto] items-center gap-x-2 px-2 py-1 text-left border transition-colors ${
                isSelected ? "border-white/25 bg-white/[0.05]" : "border-transparent hover:bg-white/[0.03]"
              }`}
            >
              <span className="w-3 h-3" style={{ backgroundColor: c.color }} aria-hidden="true" />
              <span className="min-w-0">
                <span className="text-ink text-sm">{c.label}</span>
                {/* Its own line, never truncated: the disclosed range is the
                    figure to quote, the share is only how the chart is drawn. */}
                <span className="block text-ink-min text-xs">
                  {c.count} asset{c.count !== 1 ? "s" : ""} ·{" "}
                  {formatRangeCompact(c.valueLow, c.valueHigh, c.openEnded)}
                </span>
              </span>
              <span className="text-ink-hi text-sm font-mono">{formatShare(c.share)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function HoldingRow({ holding }: { holding: Holding }) {
  return (
    <div className="panel p-3">
      <div className="text-ink text-sm break-words">
        {holding.assetName}
      </div>
      <div className="flex items-center gap-2 flex-wrap text-xs text-ink-min mt-1">
        <span className="text-ink-lo font-mono">{formatHoldingValue(holding)}</span>
        <span>{holding.categoryLabel}</span>
        <span>{OWNER_LABEL[holding.owner]}</span>
        {holding.account && <span className="truncate max-w-full">in {holding.account}</span>}
      </div>
    </div>
  );
}

interface HoldingsProps {
  memberId: string;
  chamber?: "senate" | "house";
}

export default function Holdings({ memberId, chamber = "senate" }: HoldingsProps) {
  const [data, setData] = useState<HoldingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  // Only the newest request may land: a slow page-1 response arriving after
  // a later category click would otherwise overwrite the filtered list.
  const requestSeq = useRef(0);

  const load = useCallback(
    async (page: number, cat: string | null) => {
      const seq = ++requestSeq.current;
      setLoading(true);
      setError(null);
      try {
        const result = await FETCHER[chamber](memberId, { page, perPage: HOLDINGS_PER_PAGE, category: cat });
        if (seq === requestSeq.current) setData(result);
      } catch (e) {
        if (seq === requestSeq.current) setError(e instanceof Error ? e.message : "Failed to load holdings");
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    [memberId, chamber]
  );

  useEffect(() => {
    load(1, null);
  }, [load]);

  const selectCategory = (key: string) => {
    const next = category === key ? null : key;
    setCategory(next);
    // Choosing a slice is asking to see those assets — reveal the list.
    if (next) setListOpen(true);
    load(1, next);
  };

  // Hidden until there is a report to show — the same rule StockTrades
  // follows. A report that lists nothing still renders (below), since
  // "disclosed no assets" is information.
  if (!loading && !error && (!data || !data.available)) return null;

  if (loading && !data) {
    return (
      <div className="panel p-4 text-center" role="status" aria-live="polite">
        <span className="text-ink-lo text-sm animate-pulse">Loading holdings...</span>
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

  const reportLabel = data.reportYear ? `${data.reportYear} annual report` : "latest annual report";
  const sourceLink = (
    <a
      href={data.sourceUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="text-ink-lo hover:text-phos transition-colors"
    >
      VIEW FILING ↗
    </a>
  );

  let chart: ReactNode;
  if (!data.parsed) {
    chart = (
      <p className="panel p-4 text-sm text-ink-lo">
        The {reportLabel} was filed on paper as scanned pages, so its assets can&apos;t be read reliably
        enough to chart. {sourceLink}
      </p>
    );
  } else if (data.categories.length === 0) {
    chart = (
      <p className="panel p-4 text-sm text-ink-lo">
        {data.holdingsCount === 0
          ? `The ${reportLabel} lists no assets.`
          : `The ${reportLabel} lists ${data.holdingsCount} asset${data.holdingsCount !== 1 ? "s" : ""}, none with a stated value.`}{" "}
        {sourceLink}
      </p>
    );
  } else {
    chart = (
      <div className="panel p-4">
        <div className="flex flex-col sm:flex-row items-center sm:items-start gap-4">
          <HoldingsDonut
            categories={data.categories}
            active={hovered}
            selected={category}
            onHover={setHovered}
            onSelect={selectCategory}
            holdingsCount={data.holdingsCount}
          />
          <CategoryLegend
            categories={data.categories}
            selected={category}
            onHover={setHovered}
            onSelect={selectCategory}
          />
        </div>
        <p className="text-xs text-ink-min mt-3">
          Disclosed value {formatRangeCompact(data.totalLow, data.totalHigh, data.totalOpenEnded)} across{" "}
          {data.holdingsCount} asset{data.holdingsCount !== 1 ? "s" : ""}
          {data.unvaluedCount > 0 && ` (${data.unvaluedCount} with no stated value, not charted)`} ·{" "}
          {reportLabel}
          {data.filedDate && `, filed ${data.filedDate}`} ·{" "}
          <MetricTooltip text={ABOUT_TEXT}>ABOUT THIS DATA</MetricTooltip> · {sourceLink}
        </p>
      </div>
    );
  }

  const selectedLabel = data.categories.find((c) => c.category === category)?.label;

  return (
    <CollapsibleSection
      title="INVESTMENTS & ASSETS"
      titleColor="text-signal-amber"
      summary={
        data.parsed
          ? `${data.holdingsCount} asset${data.holdingsCount !== 1 ? "s" : ""}`
          : "paper filing"
      }
      source={SOURCE_LABEL[chamber]}
      alwaysVisible={chart}
      open={listOpen}
      onOpenChange={setListOpen}
    >
      {data.parsed && data.holdingsCount > 0 && (
        <div className="space-y-3 mt-4">
          <p className="text-xs text-ink-min" aria-live="polite">
            {selectedLabel
              ? `${selectedLabel}: ${data.total} holding${data.total !== 1 ? "s" : ""}, largest first · `
              : `All ${data.total} holdings, largest first`}
            {selectedLabel && (
              <button
                type="button"
                onClick={() => selectCategory(category as string)}
                className="text-ink-lo hover:text-phos underline"
              >
                show all
              </button>
            )}
          </p>
          {error && (
            <p className="text-signal-red text-sm" role="alert">
              {error}
            </p>
          )}
          <div className={`space-y-2 ${loading ? "opacity-60 transition-opacity" : ""}`}>
            {data.holdings.map((h, i) => (
              <HoldingRow key={`${h.assetName}-${data.page}-${i}`} holding={h} />
            ))}
          </div>
          <Pagination page={data.page} totalPages={data.totalPages} onPageChange={(p) => load(p, category)} />
        </div>
      )}
    </CollapsibleSection>
  );
}
