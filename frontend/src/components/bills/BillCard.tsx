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

export default function BillCard({ bill }: { bill: BillInFlight }) {
  const config = useConfig();
  const stageInfo = config?.billStages?.[bill.stage];
  const party = PARTY_BADGE[bill.sponsorParty] ?? PARTY_BADGE.I;

  return (
    <div className="terminal-window p-3 flex flex-col gap-2 hover:border-matrix-green/40 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <a
          href={billUrl(bill.billId)}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-matrix-green hover:text-neon-cyan hover:underline leading-snug"
        >
          {bill.title || bill.billId}
        </a>
        <span
          className="shrink-0 text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border"
          style={{
            color: stageInfo?.color ?? "#00ff41",
            borderColor: `${stageInfo?.color ?? "#00ff41"}4d`,
            backgroundColor: `${stageInfo?.color ?? "#00ff41"}1a`,
          }}
        >
          {stageInfo?.name ?? bill.stage}
        </span>
      </div>

      <div className="flex items-center gap-2 text-xs text-matrix-green/60">
        <Link
          href={`/politicians/${bill.sponsorId}`}
          className="flex items-center gap-1.5 hover:text-neon-cyan"
        >
          {bill.sponsorThumbnailUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={bill.sponsorThumbnailUrl}
              alt=""
              className="w-5 h-5 rounded-full object-cover border border-matrix-green/20"
            />
          )}
          <span className={`px-1 rounded border text-[10px] ${party.className}`}>{party.label}</span>
          <span>{bill.sponsorName}</span>
          <span className="text-matrix-green/40">· {bill.sponsorState}</span>
        </Link>
        <span className="text-matrix-green/30">
          {bill.chamber === "senate" ? "Senate" : "House"}
        </span>
      </div>

      {bill.latestAction && (
        <p className="text-[11px] text-matrix-green/50 truncate">
          {bill.latestAction}
          {bill.latestActionDate && (
            <span className="text-matrix-green/30"> · {timeAgo(bill.latestActionDate)}</span>
          )}
        </p>
      )}
    </div>
  );
}
