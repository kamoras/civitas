import Link from "next/link";
import type { AgencyIndex } from "@/types/agency";
import { money } from "@/lib/agencies";
import { agencyHref } from "@/lib/routes";

type Row = NonNullable<AgencyIndex["budget"]>["agencies"][number];

/** One agency's budgetary resources as a bar scaled to the largest agency's,
 * with the figure and its share of the year's total in words beside it, so
 * the bar never carries the number alone. Every figure is the backend's. */
export function BudgetBar({ row }: { row: Row }) {
  const name = row.slug ? (
    <Link href={agencyHref(row.slug)} className="text-ink-hi hover:text-phos">
      {row.name}
    </Link>
  ) : (
    <span className="text-ink-lo">{row.name}</span>
  );
  return (
    <li className="py-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 break-words">{name}</span>
        <span className="shrink-0 font-mono text-xs tabular-nums text-ink-lo">
          {row.reported ? (
            <>
              {money(row.budgetAuthority)}
              {row.share != null && (
                <span className="text-ink-min"> · {row.share.toFixed(1)}%</span>
              )}
            </>
          ) : (
            <>
              FY{row.fiscalYear} not yet reported
              {row.latest && (
                <span className="text-ink-min">
                  {" "}
                  · FY{row.latest.fiscalYear}: {money(row.latest.budgetaryResources)}
                </span>
              )}
            </>
          )}
        </span>
      </div>
      {row.reported && (
        <div className="mt-1 h-2 bg-white/[0.05]" aria-hidden="true">
          <div className="h-2 bg-phos/70" style={{ width: `${row.barPercent}%` }} />
        </div>
      )}
    </li>
  );
}

export default function BudgetBars({ rows }: { rows: Row[] }) {
  return (
    <ol className="divide-y divide-white/[0.05]">
      {rows.map((r) => (
        <BudgetBar key={r.toptierCode} row={r} />
      ))}
    </ol>
  );
}
