import Link from "next/link";
import AgencyLink from "@/components/agencies/AgencyLink";
import { shortDate } from "@/lib/congress";
import { STATUS_LABEL, STATUS_STYLE, nominationHref } from "@/lib/nominations";
import type { NominationStatus, NominationSummary } from "@/types/nomination";

export function StatusBadge({ status }: { status: NominationStatus }) {
  return (
    <span className={`inline-block border px-2 py-0.5 font-mono text-xs ${STATUS_STYLE[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

/** One nomination in a list: Congress.gov's own description (it names the
 *  nominee, their state and the office), its status and latest action. */
export default function NominationRow({ n }: { n: NominationSummary }) {
  const isList = n.nomineeCount > 1;
  return (
    <li className="border-b border-white/[0.09] py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusBadge status={n.status} />
        <span className="font-mono text-xs text-ink-min">
          {n.citation} · <AgencyLink name={n.organization} slug={n.agencySlug} />
          {n.daysToConfirm != null && ` · confirmed in ${n.daysToConfirm} days`}
        </span>
      </div>
      <Link
        href={nominationHref(n)}
        className="mt-1 block text-base text-ink underline decoration-white/20 underline-offset-4 hover:text-phos"
      >
        {isList
          ? `${n.nomineeCount.toLocaleString()} nominees: ${[n.positionTitle, n.organization].filter(Boolean).join(", ")}`
          : // A military nomination carries no description: its nominee and grade.
            n.description ||
            [n.nominees[0], n.positionTitle || n.organization].filter(Boolean).join(": ") ||
            `Nomination ${n.citation}`}
      </Link>
      <p className="mt-1 text-sm text-ink-lo">
        {n.latestAction.date && (
          <span className="font-mono">{shortDate(n.latestAction.date)}: </span>
        )}
        {n.latestAction.text}
      </p>
    </li>
  );
}
