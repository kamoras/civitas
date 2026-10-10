import type { PoliticianBranch } from "@/types/politicians";

/**
 * Wording for a politician who is no longer serving.
 *
 * Always framed by the person ("Former Senator"), never the seat. A
 * departure leaves a Senate or House seat empty only until a successor is
 * sworn in, and the departed member's page stays up through the retirement
 * grace period, well past that: "Seat Vacant" sat on the directory card of
 * a senator whose appointed successor was listed right beside it. The
 * presidency and the Court pass on the same day anyway.
 */
export interface OfficeStatusNotice {
  /** Short uppercase-able label — banner heading and directory-card badge. */
  label: string;
  /** Sentence explaining the status, shown under the banner heading. */
  detail: string;
}

export interface OfficeStatusInput {
  branch: PoliticianBranch;
  name: string;
  vacancyReason?: string | null;
  leftOfficeDate?: string | null;
  /** President only. */
  number?: number;
  termStart?: string;
  termEnd?: string | null;
}

function ordinal(n: number): string {
  if (n % 100 >= 11 && n % 100 <= 13) return `${n}th`;
  return `${n}${{ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th"}`;
}

/** "2009-01-20" → "2009"; passes anything else through unchanged. */
function yearOf(date?: string | null): string {
  if (!date) return "";
  return /^\d{4}/.test(date) ? date.slice(0, 4) : date;
}

const RECORD_NOTE = "The scores and data below reflect their record in office.";

export function formerOfficeNotice(input: OfficeStatusInput): OfficeStatusNotice {
  const { branch, name } = input;

  if (branch === "president") {
    const office = input.number != null ? `the ${ordinal(input.number)} President` : "President";
    const start = yearOf(input.termStart);
    const end = yearOf(input.termEnd);
    const span = start ? ` from ${start} to ${end || "the end of their term"}` : "";
    return {
      label: "Former President",
      detail: `${name} served as ${office}${span} and is no longer in office. ${RECORD_NOTE}`,
    };
  }

  if (branch === "scotus") {
    return {
      label: "Former Justice",
      detail: `${name} no longer sits on the Supreme Court. ${RECORD_NOTE}`,
    };
  }

  const reason = input.vacancyReason ? ` (${input.vacancyReason})` : "";
  const asOf = input.leftOfficeDate ? ` as of ${input.leftOfficeDate}` : "";
  return {
    label: branch === "senate" ? "Former Senator" : "Former Representative",
    detail: `${name} is no longer serving${reason}${asOf}. ${RECORD_NOTE}`,
  };
}

/** Compact badge for the directory grid, e.g. "FORMER SENATOR · RESIGNED". */
export function formerOfficeBadge(input: OfficeStatusInput): string {
  const { label } = formerOfficeNotice(input);
  const suffix =
    input.branch === "senate" || input.branch === "house"
      ? input.vacancyReason
        ? ` · ${input.vacancyReason.toUpperCase()}`
        : ""
      : "";
  return `${label.toUpperCase()}${suffix}`;
}
