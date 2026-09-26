"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Holding, HoldingCategory, Holdings as HoldingsData } from "@/types/senator";
import { fetchRepHoldings, fetchSenatorHoldings } from "@/lib/api";
import { formatCurrency } from "@/lib/formatting";
import CollapsibleSection from "../shared/CollapsibleSection";
import Pagination from "../shared/Pagination";
import MetricTooltip from "./MetricTooltip";
import { formatBracket, OWNER_LABEL } from "@/lib/disclosures";

const HOLDINGS_PER_PAGE = 15;

const FETCHER = {
  senate: fetchSenatorHoldings,
  house: fetchRepHoldings,
} as const;

const SOURCE_LABEL = {
  senate: "efdsearch.senate.gov",
  house: "disclosures-clerk.house.gov",
} as const;

const ABOUT_TEXT =
  "Every asset listed on this member's most recent annual financial disclosure report — held by the member, their spouse, or a dependent child at the end of the year. The Ethics in Government Act requires values to be reported in ranges (for example $15,001 – $50,000), never as exact amounts, so no net-worth figure is computed. Slices are sized by the midpoint of each range (the minimum, for the open-ended top range); the ranges themselves are what the member disclosed. The asset type is the one the member chose when filing. Informational only — not part of the overall score.";

function formatHoldingValue(h: Holding): string {
  if (h.valueLow === null || h.valueHigh === null) return h.valueText || "Not stated";
  if (h.valueHigh === 0) return "None at year end";
  return formatBracket(h.valueLow, h.valueHigh, h.valueOpenEnded);
}

/** A summed range, compact. If any holding in the sum used the open-ended
 * top bracket, the sum has no ceiling at all — its upper figure includes a
 * placeholder — so only the floor is shown, as "$X+" (the rule every
 * disclosure figure follows; see formatBracket). */
function formatRangeCompact(low: number, high: number, openEnded: boolean): string {
  return formatBracket(low, high, openEnded, formatCurrency);
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
  // Only categories with a stated value draw a slice; the legend lists all.
  const slices = categories.filter((c) => c.weight > 0);
  // Hovering a legend row that has no slice falls back to the selection,
  // rather than clearing the selected slice's highlight.
  const focus =
    slices.find((c) => c.category === active) ?? slices.find((c) => c.category === selected) ?? null;
  const label = slices.map((c) => `${c.label} ${formatShare(c.share)}`).join(", ");

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
        {slices.length === 1 ? (
          <circle
            cx={CENTER}
            cy={CENTER}
            r={(OUTER_R + INNER_R) / 2}
            fill="none"
            stroke={slices[0].color}
            strokeWidth={OUTER_R - INNER_R}
            onMouseEnter={() => onHover(slices[0].category)}
            onClick={() => onSelect(slices[0].category)}
            className="cursor-pointer"
          />
        ) : (
          slices.map((c) => {
            const start = cursor;
            cursor += c.share;
            // Dim the rest only when the highlighted category is one of the
            // drawn slices; a legend row with no stated value has no slice
            // to stand out.
            const dimmed = focus !== null && focus.category !== c.category;
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

/** A category's legend line: its disclosed range, and how many of its
 * assets that range doesn't describe — reported as "None" at year end
 * (sold or closed: a stated value of zero) or with no value stated at all
 * ("Undetermined"). The two are different disclosures and are named apart. */
function legendDetail(c: HoldingCategory): string {
  const parts = [`${c.count} asset${c.count !== 1 ? "s" : ""}`];
  parts.push(c.weight > 0 ? formatRangeCompact(c.valueLow, c.valueHigh, c.openEnded) : "not charted");
  if (c.zeroValueCount > 0) parts.push(`${c.zeroValueCount} none at year end`);
  if (c.unvaluedCount > 0) parts.push(`${c.unvaluedCount} no value stated`);
  return parts.join(" · ");
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
                <span className="block text-ink-min text-xs">{legendDetail(c)}</span>
              </span>
              <span className="text-ink-hi text-sm font-mono">{c.weight > 0 ? formatShare(c.share) : "—"}</span>
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
  const [hovered, setHovered] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  // Only the newest request may land: a slow page-1 response arriving after
  // a later category click would otherwise overwrite the filtered list.
  const requestSeq = useRef(0);

  // The category most recently asked for — what a click toggles against,
  // so a second click on a row whose request is still in flight clears it
  // rather than asking for it again. Reset to the shown category if that
  // request fails.
  const [requested, setRequested] = useState<string | null>(null);
  // The category of the data on screen, for that failure path (which can't
  // see a render's `data`).
  const shown = useRef<string | null>(null);

  const load = useCallback(
    async (page: number, cat: string | null) => {
      const seq = ++requestSeq.current;
      setRequested(cat);
      setLoading(true);
      setError(null);
      try {
        const result = await FETCHER[chamber](memberId, { page, perPage: HOLDINGS_PER_PAGE, category: cat });
        if (seq === requestSeq.current) {
          setData(result);
          shown.current = result.categoryFilter;
        }
      } catch (e) {
        if (seq === requestSeq.current) {
          setError(e instanceof Error ? e.message : "Failed to load holdings");
          setRequested(shown.current);
        }
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    [memberId, chamber]
  );

  useEffect(() => {
    load(1, null);
  }, [load]);

  // The selected category is whatever the shown data was fetched with —
  // never a separately-held guess, so a failed or superseded request can't
  // leave the legend, the header and the list describing different things.
  const category = data?.categoryFilter ?? null;

  const filterPending = loading && requested !== category;

  const selectCategory = (key: string) => {
    const next = requested === key ? null : key;
    // Choosing a slice is asking to see those assets — reveal the list.
    if (next) setListOpen(true);
    load(1, next);
  };

  // Hidden until there is a report to show — the same rule StockTrades
  // follows. A report that lists nothing still renders (below), since
  // "disclosed no assets" is information.
  if (!loading && !error && (!data || !data.available)) return null;

  // Nothing until the first response: most members have no report, and a
  // loading panel that then vanishes shifts the whole scorecard below it.
  if (loading && !data) return null;

  if (error && !data) {
    return (
      <div className="panel p-4 text-center" role="alert">
        <span className="text-signal-red text-sm">{error}</span>
      </div>
    );
  }

  if (!data) return null;

  const reportLabel = data.reportLabel || "latest annual report";
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

  const about = <MetricTooltip text={ABOUT_TEXT}>ABOUT THIS DATA</MetricTooltip>;
  const hasSlices = data.categories.some((c) => c.weight > 0);
  let chart: ReactNode;
  if (!data.parsed) {
    chart = (
      <p className="panel p-4 text-sm text-ink-lo">
        {data.unreadableReason === "scanned"
          ? `The ${reportLabel} is a scanned paper filing, so its assets can't be read reliably enough to chart.`
          : `The ${reportLabel} isn't in a layout that can be read automatically, so its assets aren't charted here.`}{" "}
        {sourceLink}
      </p>
    );
  } else if (!hasSlices) {
    chart = (
      <p className="panel p-4 text-sm text-ink-lo">
        {data.holdingsCount === 0
          ? `The ${reportLabel} lists no assets.`
          : `The ${reportLabel} lists ${data.holdingsCount} asset${data.holdingsCount !== 1 ? "s" : ""}, ${
              data.unvaluedCount === data.holdingsCount
                ? "none with a value stated"
                : data.unvaluedCount === 0
                  ? "each reported as none at year end"
                  : "none with a value above zero at year end"
            }.`}{" "}
        {about} · {sourceLink}
      </p>
    );
  } else {
    chart = (
      <div className={`panel p-4 ${loading ? "opacity-60 transition-opacity" : ""}`} aria-busy={loading}>
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
          {about} · {sourceLink}
        </p>
      </div>
    );
  }

  const selectedLabel = data.categories.find((c) => c.category === category)?.label;
  const hasList = data.parsed && data.holdingsCount > 0;

  return (
    <CollapsibleSection
      title="INVESTMENTS & ASSETS"
      titleColor="text-signal-amber"
      summary={
        data.parsed
          ? `${data.holdingsCount} asset${data.holdingsCount !== 1 ? "s" : ""}`
          : data.unreadableReason === "scanned"
            ? "paper filing"
            : "not machine-readable"
      }
      source={SOURCE_LABEL[chamber]}
      alwaysVisible={
        <>
          {chart}
          {data.laterPaperUrl && (
            <p className="text-xs text-ink-lo mt-2">
              Also filed after this report, on paper: the {data.laterPaperLabel ?? "paper filing"}. A paper
              filing is scanned page images and states no year that can be read here, so the breakdown
              above stays with the {reportLabel}.{" "}
              <a
                href={data.laterPaperUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-phos transition-colors"
              >
                VIEW PAPER FILING ↗
              </a>
            </p>
          )}
          {/* Here, not in the list body: a failed filter or page change
              must show even with the list collapsed. The data shown is
              still the last that loaded. */}
          {error && (
            <p className="text-signal-red text-sm mt-2" role="alert">
              {error}
            </p>
          )}
        </>
      }
      // Nothing to list for an unreadable or empty report — a toggle there
      // would open onto nothing.
      expandable={hasList}
      open={listOpen}
      onOpenChange={setListOpen}
    >
      {hasList && (
        <div className="space-y-3 mt-4">
          <p className="text-xs text-ink-min" aria-live="polite">
            {selectedLabel
              ? `${selectedLabel}: ${data.total} holding${data.total !== 1 ? "s" : ""}, largest first · `
              : `All ${data.total} holdings, largest first`}
            {selectedLabel && (
              <button
                type="button"
                onClick={() => load(1, null)}
                className="text-ink-lo hover:text-phos underline"
              >
                show all
              </button>
            )}
          </p>
          <div className={`space-y-2 ${loading ? "opacity-60 transition-opacity" : ""}`}>
            {data.holdings.map((h, i) => (
              <HoldingRow key={`${h.assetName}-${data.page}-${i}`} holding={h} />
            ))}
          </div>
          <Pagination
            page={data.page}
            totalPages={data.totalPages}
            // While a different category is loading, this pager describes a
            // list that's about to be replaced: page numbers from it would
            // be applied to the wrong list, so it waits.
            disabled={filterPending}
            onPageChange={(p) => load(p, category)}
          />
        </div>
      )}
    </CollapsibleSection>
  );
}
