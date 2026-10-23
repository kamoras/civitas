"use client";

import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Holding, HoldingCategory, Holdings as HoldingsData } from "@/types/senator";
import { fetchPresidentHoldings, fetchRepHoldings, fetchSenatorHoldings } from "@/lib/api";
import { formatCurrency } from "@/lib/formatting";
import { chartMark } from "@/lib/chartColors";
import CollapsibleSection from "../shared/CollapsibleSection";
import Pagination from "../shared/Pagination";
import MetricTooltip from "./MetricTooltip";
import ScannedFiling from "./ScannedFiling";
import { asOfPhrase, formatBracket, OWNER_LABEL } from "@/lib/disclosures";
import { useLatestRequest } from "@/hooks/useLatestRequest";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import ChartReadout from "@/components/shared/ChartReadout";
import { MARK_ATTR } from "@/types/readout";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import { onThisPage } from "@/lib/onThisPage";
import { TYPE } from "@/lib/typeStyles";
import MemberBox from "@/components/scorecard/MemberBox";
import type { MemberBox as MemberBoxData } from "@/types/politicians";

const HOLDINGS_PER_PAGE = 15;
// The scorecard's panel sets the list beside the pie: a page about as tall
// as the pie and its legend, not a column running far below them.
const PANEL_HOLDINGS_PER_PAGE = 5;

const FETCHER = {
  senate: fetchSenatorHoldings,
  house: fetchRepHoldings,
  president: fetchPresidentHoldings,
} as const;

const SOURCE_LABEL = {
  senate: "efdsearch.senate.gov",
  house: "disclosures-clerk.house.gov",
  president: "oge.gov",
} as const;

const MEMBER_ABOUT_TEXT =
  "Every asset listed on this member's most recent financial disclosure report: held by the member, their spouse, or a dependent child at the end of the year for an annual report, or, for a newly seated member's new-filer report, on the date a senator's report states (a representative's states none, so it is named by its filing date). The Ethics in Government Act requires values to be reported in ranges (for example $15,001 – $50,000), so no net-worth figure is computed; a value a member chose to state exactly is shown as that figure. Slices are sized by the midpoint of each range (the minimum, for the open-ended top range); the ranges themselves are what the member disclosed. The asset type is the one the member chose when filing. Shown for context, not compared with other members.";

const PRESIDENT_ABOUT_TEXT =
  "Every asset with a stated value on the president's latest annual financial disclosure report (OGE Form 278e): the business entities of Schedule 1, the spouse's assets, and the investment accounts. Values are reported in ranges, never as exact amounts, so no net-worth figure is computed. Slices are sized by the midpoint of each range (the minimum, for the open-ended top range, which on this report is \"Over $50,000,000\"). The form has no asset-type column: a business entity's category comes from the underlying assets it states (real estate, a bank account, cryptocurrency), a fund is one the form marks as an excepted investment fund, and every other security is 'type not stated' rather than guessed from its name. Shown for context.";

const ABOUT_TEXT = {
  senate: MEMBER_ABOUT_TEXT,
  house: MEMBER_ABOUT_TEXT,
  president: PRESIDENT_ABOUT_TEXT,
} as const;

function formatHoldingValue(h: Holding, when: string): string {
  if (h.valueLow === null || h.valueHigh === null) return h.valueText || "Not stated";
  if (h.valueHigh === 0) return `None ${when}`;
  return formatBracket(h.valueLow, h.valueHigh, h.valueOpenEnded);
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
    slices.find((c) => c.category === active) ??
    slices.find((c) => c.category === selected) ??
    null;
  // Each slice names its type, share and disclosed sum (the API's words);
  // Enter or a click lists that type's assets, as the legend does.
  const marks = slices.flatMap((c) => (c.mark ? [c.mark] : []));

  let cursor = 0;
  return (
    <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
      <ChartReadout
        label="Holdings by asset type"
        marks={marks}
        labelAt="mark"
        ring={false}
        onActive={onHover}
        onOpen={onSelect}
      >
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width={SIZE} height={SIZE}>
          {slices.length === 1 ? (
            <circle
              cx={CENTER}
              cy={CENTER}
              r={(OUTER_R + INNER_R) / 2}
              fill="none"
              stroke={chartMark(slices[0].color)}
              strokeWidth={OUTER_R - INNER_R}
              {...{ [MARK_ATTR]: slices[0].category }}
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
                  fill={chartMark(c.color)}
                  // A 2px surface-coloured seam separates adjacent slices so
                  // identity never rests on hue alone at the boundary.
                  stroke="var(--surface)"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  opacity={dimmed ? 0.35 : 1}
                  {...{ [MARK_ATTR]: c.category }}
                  className="transition-opacity"
                />
              );
            })
          )}
        </svg>
      </ChartReadout>
      <div
        className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none px-12"
        aria-hidden="true"
      >
        {focus ? (
          <>
            <span className={`${TYPE.figure} text-ink-hi`}>{focus.shareShown ?? ""}</span>
            <span className="text-ink text-small leading-tight">{focus.label}</span>
            <span className="text-ink-min text-small font-mono mt-0.5">
              {focus.valueShown ??
                formatBracket(focus.valueLow, focus.valueHigh, focus.openEnded, formatCurrency)}
            </span>
          </>
        ) : (
          <>
            <span className={`${TYPE.figure} text-ink-hi`}>{holdingsCount}</span>
            <span className="text-ink-lo text-small">asset{holdingsCount !== 1 ? "s" : ""}</span>
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
function legendDetail(c: HoldingCategory, when: string): string {
  const parts = [`${c.count} asset${c.count !== 1 ? "s" : ""}`];
  // An older API sends no value text: the bracket in the form's own terms.
  parts.push(
    c.weight > 0
      ? (c.valueShown ?? formatBracket(c.valueLow, c.valueHigh, c.openEnded, formatCurrency))
      : "not charted"
  );
  if (c.zeroValueCount > 0) parts.push(`${c.zeroValueCount} none ${when}`);
  if (c.unvaluedCount > 0) parts.push(`${c.unvaluedCount} no value stated`);
  return parts.join(" · ");
}

function CategoryLegend({
  categories,
  selected,
  onHover,
  onSelect,
  when,
}: {
  categories: HoldingCategory[];
  when: string;
  selected: string | null;
  onHover: (key: string | null) => void;
  onSelect: (key: string) => void;
}) {
  return (
    <ul
      className="flex-1 min-w-0 space-y-1"
      aria-label="Holdings by asset type: select one to list only those assets"
    >
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
                isSelected
                  ? "border-white/25 bg-white/[0.05]"
                  : "border-transparent hover:bg-white/[0.03]"
              }`}
            >
              <span
                className="w-3 h-3"
                style={{ backgroundColor: chartMark(c.color) }}
                aria-hidden="true"
              />
              <span className="min-w-0">
                <span className="text-ink text-small">{c.label}</span>
                {/* Its own line, never truncated: the disclosed range is the
                    figure to quote, the share is only how the chart is drawn. */}
                <span className="block text-ink-min text-small">{legendDetail(c, when)}</span>
              </span>
              <span className="text-ink-hi text-small font-mono">
                {(c.weight > 0 && c.shareShown) || "—"}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function HoldingRow({ holding, when }: { holding: Holding; when: string }) {
  return (
    <div className="panel p-3">
      {/* anywhere, not break-word: a filer's asset or account name can be
          one long unspaced token, and only `anywhere` lets it shrink the
          row instead of widening it. */}
      <div className="text-ink text-small [overflow-wrap:anywhere]">{holding.assetName}</div>
      <div className="flex items-center gap-2 flex-wrap text-small text-ink-min mt-1">
        <span className="text-ink-lo font-mono">{formatHoldingValue(holding, when)}</span>
        <span>{holding.categoryLabel}</span>
        <span>{OWNER_LABEL[holding.owner]}</span>
        {holding.account && (
          <span className="min-w-0 [overflow-wrap:anywhere]">in {holding.account}</span>
        )}
      </div>
    </div>
  );
}

/** Behind a holdings box's More: every asset type (select one to list
 *  only those), the disclosed total, the later filing, and every holding,
 *  largest first, a page at a time. */
function HoldingsMore({
  data,
  filer,
  when,
  category,
  loading,
  filterPending,
  error,
  onSelect,
  onHover,
  onPage,
  onAll,
}: {
  data: HoldingsData;
  filer: "senate" | "house" | "president";
  when: string;
  category: string | null;
  loading: boolean;
  filterPending: boolean;
  error: string | null;
  onSelect: (key: string) => void;
  onHover: (key: string | null) => void;
  onPage: (page: number) => void;
  onAll: () => void;
}) {
  const reportLabel = data.reportLabel || "latest annual report";
  const selectedLabel = data.categories.find((c) => c.category === category)?.label;
  if (!data.parsed) {
    return (
      <p className="text-small text-ink-lo">
        {data.unreadableReason === "scanned"
          ? `The ${reportLabel} is a scanned paper filing, so its assets can't be read reliably enough to chart.`
          : `The ${reportLabel} isn't in a layout that can be read automatically, so its assets aren't charted here.`}
      </p>
    );
  }
  return (
    <div className={`flex flex-col gap-3 ${loading ? "opacity-60 transition-opacity" : ""}`}>
      <CategoryLegend
        categories={data.categories}
        when={when}
        selected={category}
        onHover={onHover}
        onSelect={onSelect}
      />
      <p className="text-small leading-6 text-ink-min">
        Disclosed value{" "}
        {data.totalShown ??
          formatBracket(data.totalLow, data.totalHigh, data.totalOpenEnded, formatCurrency)}{" "}
        across {data.holdingsCount} asset{data.holdingsCount !== 1 ? "s" : ""}
        {data.unvaluedCount > 0 &&
          ` (${data.unvaluedCount} with no stated value, not charted)`} · {reportLabel}
        {data.filedDate && data.asOfDate && `, filed ${data.filedDate}`} ·{" "}
        <MetricTooltip text={ABOUT_TEXT[filer]}>About this data</MetricTooltip>
      </p>
      {data.laterFilingUrl && (
        <p className="text-small text-ink-lo">
          Also filed, on or after this report&apos;s filing date: the{" "}
          {data.laterFilingLabel ?? "later filing"}. It states no date its holdings describe that
          can be read here, so it can&apos;t be placed against this one, and this section stays with
          the {reportLabel}.{" "}
          <a
            href={data.laterFilingUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-phos transition-colors"
          >
            View that filing ↗
          </a>
        </p>
      )}
      {error && (
        <p className="text-signal-red text-small" role="alert">
          {error}
        </p>
      )}
      {data.holdingsCount > 0 && (
        <div className="space-y-3">
          <p className="text-small text-ink-min" aria-live="polite">
            {selectedLabel
              ? `${selectedLabel}: ${data.total} holding${data.total !== 1 ? "s" : ""}, largest first · `
              : `All ${data.total} holdings, largest first`}
            {selectedLabel && (
              <button
                type="button"
                onClick={onAll}
                {...{ [SHARE_EXCLUDE_ATTR]: "" }}
                className="min-h-6 text-ink-lo hover:text-phos underline"
              >
                show all
              </button>
            )}
          </p>
          <div className="space-y-2">
            {data.holdings.map((h, i) => (
              <HoldingRow key={`${h.assetName}-${data.page}-${i}`} holding={h} when={when} />
            ))}
          </div>
          <Pagination
            page={data.page}
            totalPages={data.totalPages}
            disabled={filterPending}
            onPageChange={onPage}
          />
        </div>
      )}
      <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
        <ShareSectionButton label="Holdings" anchor={null} />
      </div>
    </div>
  );
}

/** The categories as one stacked bar, each slice as wide as its share of
 *  the stated value: a holdings box's figure (the panel draws a donut). Each
 *  slice names its type, share and disclosed sum on hover, tap and the
 *  keyboard (the API's marks); Enter lists that type's assets. */
function HoldingsBar({
  categories,
  onSelect,
}: {
  categories: HoldingCategory[];
  onSelect: (key: string) => void;
}) {
  const slices = categories.filter((c) => c.weight > 0);
  const marks = slices.flatMap((c) => (c.mark ? [c.mark] : []));
  return (
    <ChartReadout
      label="Holdings by asset type"
      marks={marks}
      labelAt="mark"
      ring={false}
      onOpen={onSelect}
      className="h-3.5"
    >
      <div className="flex h-3.5 gap-0.5">
        {slices.map((c) => (
          <span
            key={c.category}
            className="h-full"
            style={{ width: `${c.share * 100}%`, backgroundColor: chartMark(c.color) }}
            {...{ [MARK_ATTR]: c.category }}
          />
        ))}
      </div>
    </ChartReadout>
  );
}

interface HoldingsProps {
  memberId: string;
  /** Whose report: a senator's, a representative's or the sitting president's. */
  filer?: "senate" | "house" | "president";
  /** "panel": its own titled box with the chart and the list side by side
   *  and always open — how the member scorecard features it. "section"
   *  (default): a collapsible section like the others. "box": one record
   *  box (a member's profile), its headline the backend's. */
  variant?: "section" | "panel" | "box";
  /** The box's kicker, headline and line (member_boxes); null when no
   *  report has been read. Only for the "box" variant. */
  box?: MemberBoxData | null;
}

export default function Holdings({
  memberId,
  filer = "senate",
  variant = "section",
  box,
}: HoldingsProps) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const listId = useId();
  // `requested` is the category most recently asked for — what a click
  // toggles against, so a second click on a row whose request is still in
  // flight clears it rather than asking for it again.
  const { data, loading, error, requested, request } = useLatestRequest<
    HoldingsData,
    string | null
  >(null, "Failed to load holdings");

  const load = useCallback(
    (page: number, cat: string | null) =>
      request(cat, () =>
        FETCHER[filer](memberId, {
          page,
          perPage: variant === "panel" ? PANEL_HOLDINGS_PER_PAGE : HOLDINGS_PER_PAGE,
          category: cat,
        })
      ),
    [request, memberId, filer, variant]
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

  if (variant === "box") {
    // The box is the backend's from the first paint (no shift when the
    // report arrives); only the bar and the lists wait for it. None when the
    // backend says no report has been read.
    if (box === null || (box === undefined && !data?.available)) return null;
    const when = asOfPhrase(data?.asOfDate ?? null);
    const top = (data?.categories ?? []).filter((c) => c.weight > 0).slice(0, 3);
    return (
      <MemberBox
        id="holdings"
        label="Holdings"
        box={box}
        fallback={{
          kicker: "Holdings",
          title: data?.reportLabel || "Latest annual report",
        }}
        more={{
          label: "every asset type and holding",
          children: data ? (
            <HoldingsMore
              data={data}
              filer={filer}
              when={when}
              category={category}
              loading={loading}
              filterPending={filterPending}
              error={error}
              onSelect={selectCategory}
              onHover={setHovered}
              onPage={(p) => load(p, category)}
              onAll={() => load(1, null)}
            />
          ) : (
            <p className="text-small text-ink-lo">Loading the report...</p>
          ),
        }}
      >
        {data?.parsed && top.length > 0 && (
          <div className="flex flex-col gap-2">
            <HoldingsBar categories={data.categories} onSelect={selectCategory} />
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-lo">
              {top.map((c) => (
                <li key={c.category} className="flex items-center gap-1.5">
                  <span
                    className="inline-block h-2.5 w-2.5 shrink-0"
                    style={{ backgroundColor: chartMark(c.color) }}
                    aria-hidden="true"
                  />
                  {c.label} {c.shareShown}
                </li>
              ))}
            </ul>
          </div>
        )}
        {data?.sourceUrl && (
          <a
            href={data.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-flex min-h-6 items-center text-small text-ink-lo underline underline-offset-2 hover:text-phos"
          >
            View filing ↗
          </a>
        )}
      </MemberBox>
    );
  }

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
        <span className="text-signal-red text-small">{error}</span>
      </div>
    );
  }

  if (!data) return null;

  const reportLabel = data.reportLabel || "latest annual report";
  const when = asOfPhrase(data.asOfDate);
  const sourceLink = (
    <a
      href={data.sourceUrl}
      target="_blank"
      rel="noopener noreferrer"
      // nowrap: a link split over two lines has one bounding box spanning
      // both, which axe read as covering the [?] before it (target-size).
      className="whitespace-nowrap text-ink-lo hover:text-phos transition-colors"
    >
      View filing ↗
    </a>
  );

  const about = <MetricTooltip text={ABOUT_TEXT[filer]}>About this data</MetricTooltip>;
  // Inside the panel the panel is the box; a section draws its own.
  const frame = variant === "panel" ? "" : "panel p-4";
  const hasSlices = data.categories.some((c) => c.weight > 0);
  let chart: ReactNode;
  if (!data.parsed && data.unreadableReason === "scanned" && data.scan) {
    chart = (
      <div className={frame}>
        <ScannedFiling scan={data.scan} sourceLink={sourceLink} />
      </div>
    );
  } else if (!data.parsed) {
    chart = (
      <p className={`${frame} text-small text-ink-lo`}>
        {data.unreadableReason === "scanned"
          ? `The ${reportLabel} is a scanned paper filing, so its assets can't be read reliably enough to chart.`
          : `The ${reportLabel} isn't in a layout that can be read automatically, so its assets aren't charted here.`}{" "}
        {sourceLink}
      </p>
    );
  } else if (!hasSlices) {
    chart = (
      <p className={`${frame} text-small text-ink-lo`}>
        {data.holdingsCount === 0
          ? `The ${reportLabel} lists no assets.`
          : `The ${reportLabel} lists ${data.holdingsCount} asset${data.holdingsCount !== 1 ? "s" : ""}, ${
              data.unvaluedCount === data.holdingsCount
                ? "none with a value stated"
                : data.unvaluedCount === 0
                  ? `each reported as none ${when}`
                  : `none with a value above zero ${when}`
            }.`}{" "}
        {about} · {sourceLink}
      </p>
    );
  } else {
    chart = (
      <div
        className={`${frame} ${loading ? "opacity-60 transition-opacity" : ""}`}
        aria-busy={loading}
      >
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
            when={when}
            selected={category}
            onHover={setHovered}
            onSelect={selectCategory}
          />
        </div>
        {/* leading-6: the [?] is a 24px box on a 16px line, and on a
            wrapped line it overlapped "View filing" (axe target-size). */}
        <p className="mt-3 text-small leading-6 text-ink-min">
          Disclosed value{" "}
          {formatBracket(data.totalLow, data.totalHigh, data.totalOpenEnded, formatCurrency)} across{" "}
          {data.holdingsCount} asset{data.holdingsCount !== 1 ? "s" : ""}
          {data.unvaluedCount > 0 &&
            ` (${data.unvaluedCount} with no stated value, not charted)`} · {reportLabel}
          {/* An undated report's label already names its filing date. */}
          {data.filedDate && data.asOfDate && `, filed ${data.filedDate}`} · {about} · {sourceLink}
        </p>
      </div>
    );
  }

  const selectedLabel = data.categories.find((c) => c.category === category)?.label;
  const hasList = data.parsed && data.holdingsCount > 0;

  const notes = (
    <>
      {data.laterFilingUrl && (
        <p className="text-small text-ink-lo mt-2">
          Also filed, on or after this report&apos;s filing date: the{" "}
          {data.laterFilingLabel ?? "later filing"}. It states no date its holdings describe that
          can be read here, so it can&apos;t be placed against this one, and this section stays with
          the {reportLabel}.{" "}
          <a
            href={data.laterFilingUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-phos transition-colors"
          >
            View that filing ↗
          </a>
        </p>
      )}
      {/* Here, not in the list body: a failed filter or page change
              must show even with the list collapsed. The data shown is
              still the last that loaded. */}
      {error && (
        <p className="text-signal-red text-small mt-2" role="alert">
          {error}
        </p>
      )}
    </>
  );
  const list = (
    <>
      {hasList && (
        <div className="space-y-3 mt-4">
          <p className="text-small text-ink-min" aria-live="polite">
            {selectedLabel
              ? `${selectedLabel}: ${data.total} holding${data.total !== 1 ? "s" : ""}, largest first · `
              : `All ${data.total} holdings, largest first`}
            {selectedLabel && (
              <button
                type="button"
                onClick={() => load(1, null)}
                // A control, not content: left out of a shared image. (The
                // pager below stays in: without it a picture of page 1 would
                // read as the whole list.)
                {...{ [SHARE_EXCLUDE_ATTR]: "" }}
                className="text-ink-lo hover:text-phos underline"
              >
                show all
              </button>
            )}
          </p>
          <div className={`space-y-2 ${loading ? "opacity-60 transition-opacity" : ""}`}>
            {data.holdings.map((h, i) => (
              <HoldingRow key={`${h.assetName}-${data.page}-${i}`} holding={h} when={when} />
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
    </>
  );

  if (variant === "panel") {
    return (
      <section
        id="holdings"
        {...{ [SHARE_SECTION_ATTR]: "holdings" }}
        {...onThisPage("Holdings")}
        {...shareMetaAttrs({
          source: `${data.reportLabel || "Latest annual report"}, financial disclosure (${SOURCE_LABEL[filer]})`,
          asOf: data.asOfDate ?? data.filedDate,
          scaleMax: null,
        })}
        className="border border-white/25 bg-surface font-sans"
      >
        <header className="flex flex-wrap items-end justify-between gap-3 border-b border-white/25 bg-surface-raised px-5 py-4">
          <div className="min-w-0">
            <h2 className={TYPE.section}>Holdings</h2>
            <p className="mt-1 font-mono text-small text-ink-min">
              {reportLabel}
              {data.filedDate && data.asOfDate && ` · filed ${data.filedDate}`} · not part of the
              score
            </p>
          </div>
          <span className="flex items-center gap-3 font-mono text-small">
            {sourceLink}
            {/* anchor={null}: this panel renders only after a client
                fetch, so a cold load of /politicians/x#holdings has nothing
                to scroll to — the link is the scorecard page itself. */}
            <ShareSectionButton label="Holdings" anchor={null} />
          </span>
        </header>
        {/* minmax(0,1fr) on phones too: a bare `grid` gives one `auto`
            column as wide as its longest unbreakable string (an account
            name, a URL listed as an asset), which pushed the whole panel's
            contents past its border and off a phone's screen. */}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6 px-5 py-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            {chart}
            {notes}
          </div>
          {hasList && (
            <div className="min-w-0 lg:-mt-4">
              {/* On a phone the list (five assets a page, with a pager) was
                  half a screen of a page about ten screens long: it opens on
                  request there. From lg up it fills the column beside the
                  chart. Picking a slice opens it (selectCategory). */}
              <button
                type="button"
                aria-expanded={listOpen}
                aria-controls={listId}
                onClick={() => setListOpen((o) => !o)}
                {...{ [SHARE_EXCLUDE_ATTR]: "" }}
                className="inline-flex min-h-11 items-center font-mono text-small text-ink-lo hover:text-phos lg:hidden"
              >
                [{listOpen ? "Hide" : "Show"} all {data.holdingsCount} holdings]
              </button>
              <div id={listId} className={listOpen ? "" : "hidden lg:block"}>
                {list}
              </div>
            </div>
          )}
        </div>
      </section>
    );
  }

  return (
    <CollapsibleSection
      title="Investments & assets"
      titleColor="text-signal-amber"
      summary={
        data.parsed
          ? `${data.holdingsCount} asset${data.holdingsCount !== 1 ? "s" : ""}`
          : data.unreadableReason === "scanned"
            ? "paper filing"
            : "not machine-readable"
      }
      source={SOURCE_LABEL[filer]}
      alwaysVisible={
        <>
          {chart}
          {notes}
        </>
      }
      // Nothing to list for an unreadable or empty report — a toggle there
      // would open onto nothing.
      expandable={hasList}
      open={listOpen}
      onOpenChange={setListOpen}
    >
      {list}
    </CollapsibleSection>
  );
}
