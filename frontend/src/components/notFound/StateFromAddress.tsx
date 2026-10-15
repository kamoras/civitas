"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";
import { stateBallotHref } from "@/lib/elections";

/** The state an address names, by code or by name ("tx", "Texas",
 *  "new-york", "new%20york"); null for anything else. Exported for tests. */
export function stateFromSegment(segment: string): string | null {
  let raw = segment;
  try {
    raw = decodeURIComponent(segment);
  } catch {
    // A malformed escape: read it as typed.
  }
  const key = raw
    .replace(/[-_\s]+/g, " ")
    .trim()
    .toLowerCase();
  if (STATE_CODES.includes(key.toUpperCase())) return key.toUpperCase();
  return STATE_CODES.find((code) => STATE_NAMES[code]?.toLowerCase() === key) ?? null;
}

/**
 * On a state page's 404: when the address still names a state (a full
 * name, a lower-case code, or a state whose redistricting page this isn't
 * because it didn't redraw), a link to that state's ballot page, which
 * every state has, one tap away. The address is read in the browser
 * because a not-found page gets no route params; nothing about the reader
 * is asked or sent (AGENTS.md section 8).
 */
export default function StateFromAddress() {
  const pathname = usePathname();
  const code = stateFromSegment(pathname.split("/")[3] ?? "");
  // Never this page itself (a state whose ballot could not be found).
  if (!code || stateBallotHref(code) === pathname) return null;
  return (
    <Link
      href={stateBallotHref(code)}
      className="mt-5 flex min-h-11 items-center justify-between gap-3 border border-white/15 px-3 py-2 text-left font-display text-[15px] text-ink-hi transition-colors hover:border-phos/60 hover:text-phos"
    >
      {STATE_NAMES[code] ?? code}&apos;s ballot: its federal races and ballot measures
      <span aria-hidden="true" className="font-mono text-ink-min">
        &rarr;
      </span>
    </Link>
  );
}
