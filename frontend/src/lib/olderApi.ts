/**
 * A response as a backend older than the page sends it: every field the
 * chart readouts added, removed, at any depth. Swarm rolls the frontend and
 * the backend separately, so for a few minutes a new page reads an old API;
 * each chart's tests render this to prove the chart still draws, plain,
 * with no readout and no error (components/shared/ChartReadout.tsx).
 */
export const READOUT_FIELDS = new Set([
  // Every chart's readouts
  "marks",
  "mark",
  "stateMarks",
  // The bills funnel
  "stageFlow",
  // President and justice charts
  "medianShareShown",
  "estimateShown",
  "intervalShown",
  // Holdings and partisan depth
  "shareShown",
  "valueShown",
  "strengthShown",
  "barPercent",
  "leanShown",
  "leanSentence",
  "leanMarkerPercent",
  "leanBarLeft",
  "leanBarWidth",
  // Strips
  "axisMax",
  "axisStartShown",
  "axisEndShown",
  "expectedShown",
  "overTime",
  "medianShown",
  // The election archive's check
  "share80Shown",
  "interval80Shown",
  "share95Shown",
  "interval95Shown",
  "nominal80Shown",
  "nominal95Shown",
  "coverage80Shown",
  "coverage95Shown",
]);

export function olderApi<T>(value: T): T {
  if (Array.isArray(value)) return value.map(olderApi) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([k]) => !READOUT_FIELDS.has(k))
        .map(([k, v]) => [k, olderApi(v)])
    ) as T;
  }
  return value;
}
