import Link from "next/link";
import type { CongressEvent, RollCallSummary } from "@/types/congress";
import { billHref, resultTone } from "@/lib/congress";

export function Figure({ value, label, muted = false }: { value: number | string; label: string; muted?: boolean }) {
  return (
    <div className="flex flex-col gap-1.5 border-l border-white/[0.09] px-4 py-3">
      <span className={`font-mono text-3xl leading-none tabular-nums ${muted ? "text-ink-min" : "text-ink-hi"}`}>
        {value}
      </span>
      <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">{label}</span>
    </div>
  );
}

export function FigureGroup({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="font-mono text-xs uppercase tracking-[0.14em] text-ink-lo">{name}</span>
      <div className="grid grid-cols-2 sm:grid-cols-4">{children}</div>
    </div>
  );
}

export function Section({
  title,
  count,
  level = 3,
  children,
}: {
  title: string;
  count?: number | string | null;
  /** 2 on a page whose sections sit directly under its title; 3 under a chamber's heading. */
  level?: 2 | 3;
  children: React.ReactNode;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section className="flex flex-col gap-3 border-t border-white/15 pt-4">
      <Heading className="font-display text-base font-bold text-ink-hi">
        {title}
        {count !== undefined && count !== null && (
          <span className="ml-2 font-mono text-sm font-normal text-ink-min">{count}</span>
        )}
      </Heading>
      {children}
    </section>
  );
}

const TONE_CLASS = { yes: "text-phos-mid", no: "text-signal-red", neutral: "text-ink-lo" } as const;

export function ResultLabel({ result, rejected }: { result: string; rejected: boolean | null }) {
  return (
    <span className={`font-mono text-xs uppercase tracking-[0.12em] ${TONE_CLASS[resultTone(rejected)]}`}>
      {result || "Recorded"}
    </span>
  );
}

/** Yea share of those voting yea or nay. Display scaling of the chamber's
 * own numbers, not a new figure. */
function TallyBar({ yeas, nays }: { yeas: number; nays: number }) {
  const total = yeas + nays;
  const pct = total > 0 ? (yeas / total) * 100 : 0;
  return (
    <div aria-hidden="true" className="h-1 w-24 bg-white/10">
      <div className="h-1 bg-ink-lo" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function voteHref(v: { chamber: string; congress: number; session: number; number: number; billId: string | null }) {
  return v.billId ? `${billHref(v.billId)}#vote-${v.chamber}-${v.session}-${v.number}` : null;
}

export function VoteRow({ vote, showDate = false }: { vote: RollCallSummary; showDate?: boolean }) {
  const href = voteHref(vote);
  return (
    <li className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-4 gap-y-2 border-b border-white/[0.09] py-3 sm:grid-cols-[4.5rem_minmax(0,1fr)_9.5rem]">
      <div className="flex flex-col gap-1 font-mono text-xs uppercase tracking-[0.1em] text-ink-lo">
        <span>No. {vote.number}</span>
        {showDate && <span className="text-ink-min">{vote.date.slice(5)}</span>}
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        <p className="text-[15px] leading-snug text-ink">{vote.question}</p>
        {vote.title && vote.title !== vote.question && (
          <p className="text-sm leading-snug text-ink-lo">{vote.title}</p>
        )}
        {href && vote.billLabel && (
          <Link href={href} className="w-fit font-mono text-sm text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos">
            {vote.billLabel}
          </Link>
        )}
      </div>
      <div className="col-start-2 flex flex-col items-start gap-1.5 sm:col-start-auto sm:items-end">
        <ResultLabel result={vote.result} rejected={vote.rejected} />
        <span className="font-mono text-lg tabular-nums text-ink-hi">
          {vote.yeas}–{vote.nays}
        </span>
        <TallyBar yeas={vote.yeas} nays={vote.nays} />
      </div>
    </li>
  );
}

export function MeasureRow({ event, tag }: { event: CongressEvent; tag?: string | null }) {
  return (
    <li className="flex flex-col gap-1.5 border-b border-white/[0.09] py-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 text-base font-bold text-ink-hi">
          {event.billId && event.billLabel ? (
            <Link href={billHref(event.billId)} className="hover:text-phos">
              <span className="mr-2 font-mono font-normal text-ink-lo">{event.billLabel}</span>
              {event.name}
            </Link>
          ) : (
            event.name || event.billLabel
          )}
        </p>
        {tag && (
          <span className="shrink-0 border border-white/20 px-2 py-0.5 font-mono text-xs uppercase tracking-[0.1em] text-ink-lo">
            {tag}
          </span>
        )}
      </div>
      <p className="text-sm leading-relaxed text-ink-lo">{event.text}</p>
    </li>
  );
}

export function EventList({ children }: { children: React.ReactNode }) {
  return <ul className="flex flex-col">{children}</ul>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-[15px] text-ink-lo">{children}</p>;
}
