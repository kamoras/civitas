"use client";

import Link from "next/link";
import { BillInFlight } from "@/types/bill";
import { billUrl } from "@/lib/sources";
import { useConfig } from "@/hooks/useConfig";

const PARTY_BADGE: Record<string, { label: string; className: string }> = {
  D: { label: "D", className: "text-dem-blue border-dem-blue/30 bg-dem-blue/10" },
  R: { label: "R", className: "text-rep-red border-rep-red/30 bg-rep-red/10" },
  I: { label: "I", className: "text-ind-purple border-ind-purple/30 bg-ind-purple/10" },
};

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
  const stageColor = stageInfo?.color ?? "#00ff41";

  return (
    <div
      className="flex items-start gap-3 py-2 px-2 hover:bg-white/[0.02] transition-colors"
      style={{ boxShadow: `inset 2px 0 0 0 ${stageColor}55` }}
    >
      <span
        className="shrink-0 mt-0.5 text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border w-[92px] text-center truncate"
        style={{ color: stageColor, borderColor: `${stageColor}4d`, backgroundColor: `${stageColor}1a` }}
        title={stageInfo?.name ?? bill.stage}
      >
        {stageInfo?.name ?? bill.stage}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-2">
          <a
            href={billUrl(bill.billId)}
            target="_blank"
            rel="noopener noreferrer"
            className="min-w-0 flex-1 text-sm text-matrix-green hover:text-neon-cyan hover:underline leading-snug truncate"
          >
            {bill.title || bill.billId}
          </a>
          {bill.mentionCount > 0 && (
            <span
              className="shrink-0 text-[9px] font-mono text-neon-cyan border border-neon-cyan/30 bg-neon-cyan/10 px-1.5 py-0.5 rounded"
              title={`Referenced in ${bill.mentionCount} current Action Center issue${bill.mentionCount === 1 ? "" : "s"}`}
            >
              ACTIVE ×{bill.mentionCount}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 mt-0.5 text-[11px] text-matrix-green/50">
          <Link
            href={`/politicians/${bill.sponsorId}`}
            className="flex items-center gap-1 hover:text-neon-cyan shrink-0"
          >
            {bill.sponsorThumbnailUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={bill.sponsorThumbnailUrl}
                alt=""
                className="w-4 h-4 rounded-full object-cover border border-matrix-green/20"
              />
            )}
            <span className={`px-1 rounded border text-[9px] ${party.className}`}>{party.label}</span>
            <span className="text-matrix-green/70">{bill.sponsorName}</span>
          </Link>
          <span className="text-matrix-green/30">· {bill.sponsorState}</span>
          <span className="text-matrix-green/30">· {bill.chamber === "senate" ? "Senate" : "House"}</span>
          {bill.latestAction && (
            <span className="text-matrix-green/40 truncate">
              · {bill.latestAction}
              {bill.latestActionDate && ` (${timeAgo(bill.latestActionDate)})`}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
