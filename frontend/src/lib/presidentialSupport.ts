/**
 * Wording for "votes with the president's stated position". The numbers come
 * from the backend; this only puts them into sentences.
 *
 * Neutral by design: voting with or against the president is neither good nor
 * bad, so nothing here praises, warns or ranks. Templated text names the
 * member and never uses a pronoun.
 */
import type {
  ChamberSupport,
  ChamberSupportMember,
  SupportNumbers,
} from "@/types/presidentialSupport";

export const PRESIDENT_SUPPORT_PATH = "/congress/president";

export function presidentSupportHref(chamber: "senate" | "house"): string {
  return `${PRESIDENT_SUPPORT_PATH}/${chamber}`;
}

/** 0.8361 -> "84%". */
export function pct(x: number | null | undefined): string {
  return x == null ? "n/a" : `${Math.round(x * 100)}%`;
}

/** 0.043 -> "+4 points", -0.12 -> "-12 points" (a gap between two rates). */
export function points(x: number | null | undefined): string {
  if (x == null) return "n/a";
  const n = Math.round(x * 100);
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${Math.abs(n)} point${Math.abs(n) === 1 ? "" : "s"}`;
}

/** Cook PVI as written: 5 -> "R+5", -12 -> "D+12", 0 -> "EVEN". */
export function leanLabel(lean: number | null | undefined): string {
  if (lean == null) return "n/a";
  return lean > 0 ? `R+${lean}` : lean < 0 ? `D+${-lean}` : "EVEN";
}

/** "Voted with the president's stated position on 120 of 122 counted votes (98%)." */
export function recordSentence(name: string, s: SupportNumbers): string {
  return `${name} voted with the president's stated position on ${s.withPresident} of ${s.votes} counted vote${
    s.votes === 1 ? "" : "s"
  } (${pct(s.rate)}).`;
}

/** How the record compares with members of the same party in seats that
 * lean the same way, and whether the gap is bigger than the vote count can
 * tell apart from chance. */
export function expectationSentence(s: SupportNumbers): string {
  if (s.expected == null || s.comparedWithExpected == null) {
    return "There are too few members of this party in the chamber to measure what a seat like this one usually does.";
  }
  const base = `Members of the same party in seats that lean the same way vote with it ${pct(s.expected)} of the time.`;
  if (s.comparedWithExpected === "within") {
    return `${base} The gap, ${points(s.difference)}, is small enough that ${s.votes} votes can't tell it apart from chance: about as expected.`;
  }
  return `${base} That is ${points(s.difference)}, more than chance explains: ${
    s.comparedWithExpected === "more" ? "more often" : "less often"
  } than expected.`;
}

export function chamberCounts(view: ChamberSupport): Record<"more" | "less" | "within", number> {
  const out = { more: 0, less: 0, within: 0 };
  for (const m of view.members) if (m.comparedWithExpected) out[m.comparedWithExpected] += 1;
  return out;
}

export function chamberSentence(view: ChamberSupport): string {
  const c = chamberCounts(view);
  const body = view.chamber === "senate" ? "senators" : "House members";
  return `Of ${c.more + c.less + c.within} ${body}, ${c.more} voted with the president's stated position more often than members of the same party in similar seats, and ${c.less} less often. For the other ${c.within}, the gap is within what their votes can tell apart from chance.`;
}

/** The member a reader names: by name, state code or name, or "TX-7". */
export function matchesMember(m: ChamberSupportMember, query: string, stateName?: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return false;
  const seat = m.district != null ? `${m.state}-${m.district}`.toLowerCase() : "";
  return (
    m.name.toLowerCase().includes(q) ||
    m.state.toLowerCase() === q ||
    (stateName ?? "").toLowerCase() === q ||
    (seat !== "" && seat === q.replace(/\s+/g, ""))
  );
}

/** Furthest from expected on one side, largest gap first: only members whose
 * interval excludes the expected rate. */
export function furthest(
  view: ChamberSupport,
  side: "more" | "less",
  n = 5
): ChamberSupportMember[] {
  return view.members
    .filter((m) => m.comparedWithExpected === side && m.difference != null)
    .sort((a, b) =>
      side === "more" ? b.difference! - a.difference! : a.difference! - b.difference!
    )
    .slice(0, n);
}

export function seatLabel(m: { state: string; district: number | null }): string {
  if (m.district == null) return m.state;
  return m.district === 0 ? `${m.state}-AL` : `${m.state}-${m.district}`;
}
