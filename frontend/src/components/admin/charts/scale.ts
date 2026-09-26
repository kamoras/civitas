/**
 * Axis arithmetic for the admin dashboard's charts. Kept free of React so the
 * rounding rules are testable on their own.
 */

/**
 * Round `max` up to a clean axis ceiling and return evenly spaced ticks from 0.
 *
 * Steps are 1, 2, 2.5 or 5 times a power of ten, the usual "nice numbers"
 * ladder, so the ticks read as 0 / 250 / 500 / 750 rather than 0 / 237 / 474.
 * The axis always starts at zero: every chart here plots a count or a duration,
 * and a baseline above zero would make a 5% wobble look like a collapse.
 */
export function niceTicks(max: number, targetCount = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const rough = max / targetCount;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? 10 * magnitude;
  const steps = Math.max(1, Math.ceil(max / step - 1e-9));
  return Array.from({ length: steps + 1 }, (_, i) => Number((i * step).toPrecision(12)));
}

// Steps a reader already counts time in, in seconds: 1/2/5/10/15/30 s,
// 1/2/5/10/15/30 min, 1/2/3/6/12 h, then days.
const DURATION_STEPS = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
];

/**
 * niceTicks for a y-axis in seconds. Decimal "nice" steps are wrong for
 * time: 2,000s is a round number and a meaningless duration ("33m"), so the
 * step is taken from the clock's own units instead.
 */
export function niceDurationTicks(maxSeconds: number, targetCount = 4): number[] {
  if (!Number.isFinite(maxSeconds) || maxSeconds <= 0) return [0, 60];
  const rough = maxSeconds / targetCount;
  const step = DURATION_STEPS.find((s) => s >= rough) ?? Math.ceil(rough / 86400) * 86400;
  const steps = Math.max(1, Math.ceil(maxSeconds / step - 1e-9));
  return Array.from({ length: steps + 1 }, (_, i) => i * step);
}

/**
 * Indices of the points to label on an x-axis of `count` evenly spaced points,
 * at most `maxLabels` of them, at a whole-number stride so the labels are
 * evenly spaced (a fractional stride rounds to alternating gaps of 2 and 3
 * days, which reads as missing data). Counted back from the last point, which
 * is always labelled — it is "today" / "the latest run", the one a reader
 * looks for.
 */
export function xLabelIndices(count: number, maxLabels: number): number[] {
  if (count <= 0) return [];
  if (count <= maxLabels) return Array.from({ length: count }, (_, i) => i);
  const stride = Math.ceil((count - 1) / (Math.max(2, maxLabels) - 1));
  const picked: number[] = [];
  for (let i = count - 1; i >= 0; i -= stride) picked.push(i);
  return picked.reverse();
}

/** 1,284 → "1.3K"; small values stay exact. For axis ticks and stat tiles. */
export function formatCompact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${+(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${Math.round(n / 1000)}K`;
  if (abs >= 1_000) return `${+(n / 1000).toFixed(1)}K`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Milliseconds for axes: 850 → "850ms", 1500 → "1.5s". */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${+(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
}

/** Seconds for axes: 45 → "45s", 5400 → "1.5h". Coarser than formatDuration. */
export function formatSecondsShort(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${+(s / 60).toFixed(1)}m`;
  return `${+(s / 3600).toFixed(1)}h`;
}
