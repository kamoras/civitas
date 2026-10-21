import { billPageHref, rollCallHref } from "@/lib/congress";
import type { NominationDetail, NominationKind, NominationStatus } from "@/types/nomination";
import type { VoteRollCall } from "@/types/senator";

/** A nomination's page: citations repeat in every Congress, so the address
 *  names both. */
export function nominationHref(n: { congress: number; citation: string }): string {
  return `/congress/nominations/${n.congress}/${n.citation}`;
}

export const NOMINATIONS_HREF = "/congress/nominations";

/** A citation as an address may carry it: "PN999-1". */
export const CITATION = /^PN[1-9]\d{0,4}(-[1-9]\d{0,2})?$/;

export const STATUS_LABEL: Record<NominationStatus, string> = {
  pending: "Pending",
  confirmed: "Confirmed",
  returned: "Returned",
  withdrawn: "Withdrawn",
};

/** What each status means, in Congress.gov's terms. */
export const STATUS_NOTE: Record<NominationStatus, string> = {
  pending: "Received, and not yet confirmed, withdrawn or returned.",
  confirmed: "The Senate voted to confirm, by roll call or by voice.",
  returned:
    "Sent back to the president without a vote, under the Senate's rule for nominations left at a long recess or the end of a session.",
  withdrawn: "The president withdrew the nomination.",
};

export const STATUS_STYLE: Record<NominationStatus, string> = {
  pending: "text-signal-amber border-signal-amber/40 bg-signal-amber/10",
  confirmed: "text-phos border-phos/40 bg-phos/10",
  returned: "text-ink-lo border-white/20 bg-white/[0.03]",
  withdrawn: "text-signal-red border-signal-red/40 bg-signal-red/10",
};

export const KIND_LABEL: Record<NominationKind, string> = {
  civilian: "Civilian",
  judicial: "Judges",
  military: "Military",
};

export const KIND_NOTE: Record<NominationKind, string> = {
  civilian: "Cabinet, agency, ambassador and board posts.",
  judicial: "Federal judges: Supreme Court, appeals, district and other courts.",
  military: "Promotions and appointments of officers, often hundreds to a list.",
};

export function days(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${Math.round(n)} day${Math.round(n) === 1 ? "" : "s"}`;
}

/** Where a member's vote row's title goes: the bill's page; for a vote on
 *  one nomination, its record; for an en bloc vote, the roll call's page,
 *  which lists each. Null when none applies (a procedural vote with no bill,
 *  or a nomination not read yet); for a vote stored before its roll call,
 *  its bill id's. */
export function voteSubjectHref(
  rc: VoteRollCall | null | undefined,
  billId?: string | null
): string | null {
  const nv = rc?.nominationVote;
  if (rc && nv) {
    if (nv.citationCount > 1) return rollCallHref(rc);
    return nv.nominations[0] ? nominationHref(nv.nominations[0]) : null;
  }
  return rc ? billPageHref(rc.billId, rc.congress) : billPageHref(billId);
}

/** The line a nomination vote's row adds: which question it was (cloture is
 *  not confirmation), on which nomination, and — on the vote to confirm
 *  only — where the nomination stands. An en bloc vote counts every
 *  nomination it names. Null for any other vote. */
export function voteNominationLine(rc: VoteRollCall | null | undefined): string | null {
  const nv = rc?.nominationVote;
  if (!nv) return null;
  if (nv.citationCount > 1) return `${nv.label} · ${nv.citationCount} nominations, en bloc`;
  const n = nv.nominations[0];
  if (!n) return nv.label;
  const parts = [nv.label, `Nomination ${n.citation}`, n.organization];
  if (nv.kind === "confirm") parts.push(STATUS_LABEL[n.status]);
  return parts.filter(Boolean).join(" · ");
}

/** A nomination's page heading: the nominee and the office, or how many
 *  nominees a list names. The page's h1, title and share card. */
export function nominationHeading(n: NominationDetail): string {
  const first = n.nominees[0];
  if (n.nomineeCount > 1)
    return `${n.nomineeCount.toLocaleString()} nominees: ${[n.positionTitle, n.organization].filter(Boolean).join(", ")}`;
  const name = first?.names[0];
  const who = name
    ? [name.firstName, name.middleName, name.lastName].filter(Boolean).join(" ")
    : "";
  const office = n.positionTitle || n.organization;
  return who ? `${who}: ${office}` : office || n.description || `Nomination ${n.citation}`;
}
