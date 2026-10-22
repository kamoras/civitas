import { SourceLine } from "@/components/shared/Type";
import type { AmendmentFields } from "@/types/congress";

/** Whose record states the purpose: the Senate's roll-call XML, or
 * Congress.gov's record of a House amendment (fetch/amendment_purpose.py). */
const SOURCE = {
  senate: "Senate roll-call record",
  house: "Congress.gov amendment record",
} as const;

/**
 * What the amendment a vote was on would do, quoted from the record. A vote
 * on an amendment is otherwise titled only by its sponsor and number
 * ("Smith Amdt. No. 6805"). Nothing for a vote not on an amendment, or one
 * whose purpose isn't read yet; a record that states no purpose says so.
 */
export default function AmendmentPurpose({
  vote,
  chamber,
  compact = false,
}: {
  vote: AmendmentFields;
  chamber: "senate" | "house";
  /** A vote list's row: one line, smaller. */
  compact?: boolean;
}) {
  if (!vote.amendment || vote.amendmentPurpose == null) return null;
  const label = <span className="mr-2 font-mono text-small text-ink-min">Official purpose</span>;
  if (!vote.amendmentPurpose)
    return (
      <p className={compact ? "text-small text-ink-lo" : "text-body text-ink-lo"}>
        {label}
        No statement of purpose on file for {vote.amendment}.
      </p>
    );
  if (compact)
    return (
      <p className="text-small leading-snug text-ink">
        {label}
        <q>{vote.amendmentPurpose}</q>
      </p>
    );
  return (
    <figure className="max-w-3xl border-l-2 border-signal-cyan/60 pl-3">
      <figcaption className="font-mono text-small text-ink-min">
        Official purpose of {vote.amendment}
      </figcaption>
      <blockquote className="mt-1 text-dek leading-snug text-ink-hi">
        {vote.amendmentPurpose}
      </blockquote>
      <SourceLine className="mt-1">Quoted from the {SOURCE[chamber]}</SourceLine>
    </figure>
  );
}
