/** Words for the redistricting pages. Every figure arrives computed from
 * the backend (app/redistricting.py); these only put it into a sentence.
 * The subject is always the district, never a person. */

import type { OldDistrict, RedistrictingNote } from "@/types/redistricting";
import { formatPvi } from "@/lib/elections";
import { ordinal } from "@/lib/officeStatus";

export const REDISTRICTING_HREF = "/elections/redistricting";

/** The state page, or a district on it: `new-5` / `old-5` anchors. */
export function redistrictingHref(state: string, anchor?: string): string {
  return `${REDISTRICTING_HREF}/${state.toUpperCase()}${anchor ? `#${anchor}` : ""}`;
}

/** "5th", or "at-large" for district 0. */
export function districtOrdinal(n: number): string {
  return n === 0 ? "at-large" : ordinal(n);
}

type NoteLike = Pick<
  RedistrictingNote,
  "predecessorNumber" | "predecessorSharePct" | "pviBefore" | "pviAfter" | "unchanged"
>;

/** "Most of this district's residents (78%) were in the old 5th; its lean
 * moved from R+3 to R+9." The lean "before" is the old district most
 * residents came from. */
export function newDistrictSentence(note: NoteLike, withLean = true): string {
  // Without the lean (a results page: beside a count, a lean reads as a
  // prediction of it), the sentence is only where residents lived.
  const d = withLean ? note : { ...note, pviBefore: null, pviAfter: null };
  const old = `the old ${districtOrdinal(d.predecessorNumber)}`;
  if (d.unchanged) {
    const lean =
      d.pviAfter == null
        ? ""
        : d.pviBefore != null && d.pviBefore !== d.pviAfter
          ? ` Cook's newer release puts its lean at ${formatPvi(d.pviAfter)}, from ${formatPvi(d.pviBefore)}.`
          : ` Its lean is ${formatPvi(d.pviAfter)}.`;
    return `Unchanged: the same residents as ${old}.${lean}`;
  }
  const who =
    d.predecessorSharePct > 50
      ? `Most of this district's residents (${d.predecessorSharePct}%)`
      : `The largest group of this district's residents (${d.predecessorSharePct}%)`;
  let lean = "";
  if (d.pviBefore != null && d.pviAfter != null) {
    lean =
      d.pviBefore === d.pviAfter
        ? `; its lean is ${formatPvi(d.pviAfter)}, the same as before`
        : `; its lean moved from ${formatPvi(d.pviBefore)} to ${formatPvi(d.pviAfter)}`;
  } else if (d.pviAfter != null) {
    lean = `; its lean is ${formatPvi(d.pviAfter)}`;
  }
  return `${who} were in ${old}${lean}.`;
}

/** "a, b and c". */
function andList(parts: string[]): string {
  return parts.length < 2
    ? parts.join("")
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** "Of the old 5th's residents, 78% are in the new 5th and 15% in the new
 * 6th, and the rest in 2 other districts." */
export function oldDistrictSentence(
  o: Pick<OldDistrict, "number" | "destinations" | "hasSuccessor">
): string {
  const name = `the old ${districtOrdinal(o.number)}`;
  const dests = o.destinations;
  if (dests.length === 1 && dests[0].sharePct === 100) {
    return `All of ${name}'s residents are in the new ${districtOrdinal(dests[0].number)}.`;
  }
  const shown = dests.slice(0, 3);
  const parts = shown.map(
    (r, i) => `${r.sharePct}% ${i === 0 ? "are " : ""}in the new ${districtOrdinal(r.number)}`
  );
  const others = dests.length - shown.length;
  const rest =
    others > 0 ? `, and the rest in ${others} other district${others === 1 ? "" : "s"}` : "";
  const successor = o.hasSuccessor ? "" : " No new district draws most of its residents from it.";
  return `Of ${name}'s residents, ${andList(parts)}${rest}.${successor}`;
}
