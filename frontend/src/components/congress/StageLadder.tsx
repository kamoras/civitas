import Link from "next/link";
import { reachedHref } from "@/lib/congress";
import type { StageMilestones } from "@/types/bill";

/**
 * How far this bill has got beside how far bills get: one row per
 * milestone, with the share of this Congress's bills that reached it, and
 * this bill's furthest milestone marked. Rows the bill has reached are
 * filled; the ones still ahead of it are drawn as the track alone, so the
 * reader sees both where it is and how few bills go further.
 *
 * The counts and shares are the API's (bill_service.stage_milestones), over
 * the bills sitting members sponsored; this only lays them out.
 */
export default function StageLadder({ data }: { data: StageMilestones }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="font-mono text-xs text-ink-min">How far it has got</p>
      <ol className="flex flex-col">
        {data.milestones.map((m, i) => {
          const reached = i <= data.reached;
          const here = i === data.reached;
          return (
            <li
              key={m.label}
              aria-current={here ? "step" : undefined}
              className="grid grid-cols-[minmax(0,10.5rem)_minmax(0,1fr)_3.25rem] items-center gap-3 border-t border-white/[0.07] py-1.5 text-sm"
            >
              <span className={reached ? "text-ink-hi" : "text-ink-lo"}>
                {/* The label opens the bills that got at least this far, the
                    same list /congress's pipeline row opens. The "This bill"
                    mark stays outside the link: it is this bill's status,
                    not part of where the link goes. */}
                <Link
                  href={reachedHref(m.stage)}
                  aria-label={`${m.label}: ${m.bills.toLocaleString("en-US")} ${m.bills === 1 ? "bill" : "bills"}`}
                  className="inline-block min-h-6 py-0.5 underline decoration-white/30 underline-offset-4 hover:text-phos"
                >
                  {m.label}
                </Link>
                {here && (
                  <span className="ml-2 whitespace-nowrap border border-signal-cyan/50 px-1 font-mono text-[11px] text-signal-cyan">
                    This bill
                  </span>
                )}
              </span>
              <span className="relative h-2.5 bg-white/[0.07]" aria-hidden="true">
                <span
                  className={`absolute inset-y-0 left-0 ${reached ? "bg-signal-cyan" : "bg-white/[0.22]"}`}
                  style={{ width: `${Math.max(m.share * 100, 0.6)}%` }}
                />
              </span>
              <span className="text-right font-mono text-xs tabular-nums text-ink-lo">
                {m.shareText}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="text-xs leading-relaxed text-ink-min">
        Share of the {data.total.toLocaleString("en-US")} bills sitting members have sponsored this
        Congress that got at least that far. Pick a step to list its bills.
      </p>
    </div>
  );
}
