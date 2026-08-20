"use client";

import Link from "next/link";
import { BillInFlight } from "@/types/bill";
import { useConfig } from "@/hooks/useConfig";
import { PARTY_BADGE } from "@/lib/partyStyles";
import { billStageStyle } from "@/lib/billStages";

function timeAgo(dateStr: string): string {
  if (!dateStr) return "";
  const then = new Date(dateStr).getTime();
  if (Number.isNaN(then)) return "";
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} mo ago`;
  return `${Math.floor(months / 12)} yr ago`;
}

export default function BillRow({ bill }: { bill: BillInFlight }) {
  const config = useConfig();
  const stageInfo = config?.billStages?.[bill.stage];
  const party = PARTY_BADGE[bill.sponsorParty] ?? PARTY_BADGE.I;
  const stageStyle = billStageStyle(bill.stage);

  return (
    <div
      className={`flex items-start gap-3 border-l-2 px-2 py-2 transition-colors hover:bg-white/[0.02] ${stageStyle.rule}`}
    >
      {/* w-[100px], not w-[92px]: the global minimum-size floor moved this from
          11px to 12px, and at 12px the longest stage names ("IN COMMITTEE",
          "TO PRESIDENT") measure ~85px, which with the 12px of horizontal
          padding overflowed the old 92px box and truncated. */}
      <span
        className={`shrink-0 mt-0.5 text-xs font-mono uppercase tracking-wider px-1.5 py-0.5 border w-[100px] text-center truncate ${stageStyle.text} ${stageStyle.border} ${stageStyle.bg}`}
        title={stageInfo?.name ?? bill.stage}
      >
        {stageInfo?.name ?? bill.stage}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-2">
          <Link
            href={`/bills/${encodeURIComponent(bill.billId)}`}
            // min-h-6 (24px): WCAG 2.2 target size. The row is deliberately
            // dense, so the height comes from the tap target rather than from
            // padding that would space the list out.
            className="min-w-0 flex-1 min-h-6 flex items-center text-sm text-ink-hi hover:text-phos hover:underline leading-snug truncate"
          >
            {bill.title || bill.billId}
          </Link>
          {bill.mentionCount > 0 && (
            <span
              className="shrink-0 text-xs font-mono text-signal-cyan border border-white/15 bg-signal-cyan/10 px-1.5 py-0.5"
              title={`Referenced in ${bill.mentionCount} current Action Center issue${bill.mentionCount === 1 ? "" : "s"}`}
            >
              ACTIVE ×{bill.mentionCount}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 mt-0.5 text-xs text-ink-lo">
          <Link
            href={`/politicians/${bill.sponsorId}`}
            className="flex min-h-6 shrink-0 items-center gap-1 hover:text-phos"
          >
            {bill.sponsorThumbnailUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={bill.sponsorThumbnailUrl}
                alt=""
                className="h-4 w-4 border border-white/[0.07] object-cover"
              />
            )}
            <span className={`px-1 border text-xs ${party.className}`}>{party.label}</span>
            <span className="text-ink">{bill.sponsorName}</span>
          </Link>
          <span className="text-ink-min">· {bill.sponsorState}</span>
          <span className="text-ink-min">· {bill.chamber === "senate" ? "Senate" : "House"}</span>
          {bill.latestAction && (
            <span className="text-ink-min truncate">
              · {bill.latestAction}
              {bill.latestActionDate && ` (${timeAgo(bill.latestActionDate)})`}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
