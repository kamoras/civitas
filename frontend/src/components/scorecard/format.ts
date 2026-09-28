/** A 0–1 share as a whole percent, "<1%" for a share that rounds to zero
 *  but isn't: "0%" of a member's money from PACs would be a false claim. */
export function percent(share: number): string {
  if (share > 0 && share < 0.005) return "<1%";
  return `${Math.round(share * 100)}%`;
}

/** "1.1%": a rate small enough that whole percents would hide it. */
export function percentOneDecimal(share: number): string {
  return `${(share * 100).toFixed(1)}%`;
}

const PARTY_MEMBERS: Record<string, string> = { R: "Republicans", D: "Democrats" };

/** "Republicans" for "R": how a sentence names a member's party. */
export function partyMembers(party: string): string {
  return PARTY_MEMBERS[party] ?? "members of the same party";
}

/** "one bill" / "65 bills" — whole words for none and one, so a sentence
 *  never reads "0 bills" or "1 bills". */
export function count(n: number, one: string, many: string): string {
  if (n === 0) return `no ${many}`;
  if (n === 1) return `one ${one}`;
  return `${n.toLocaleString()} ${many}`;
}

/** "Jul 22, 2026" for an ISO date; anything else is shown as given. */
export function shortDate(iso: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** A roll call's title, unless the chamber's title is only the bill's
 *  number as its XML spells it ("H R 8800"): then the site's own label for
 *  that bill ("H.R. 8800"). */
export function voteTitle(
  title: string | null | undefined,
  billLabel: string | null | undefined,
  fallback: string
): string {
  if (title && !/^[A-Z][A-Z .]*\d+$/.test(title.trim())) return title;
  return billLabel || title || fallback;
}

/** "47th", "1st", "22nd", "113th". */
export function ordinal(n: number): string {
  const mod100 = n % 100;
  const suffix =
    mod100 >= 11 && mod100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th");
  return `${n}${suffix}`;
}
