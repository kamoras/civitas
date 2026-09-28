"use client";

import { useEffect, useRef, type ReactNode } from "react";
import type { BallotContest } from "@/lib/ballotContests";
import { useModalDialog } from "@/lib/useModalDialog";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";

const COLUMN_LABEL: Record<BallotContest["column"], string> = {
  federal: "FEDERAL",
  state: "STATE",
  local: "MEASURES · LOCAL",
};

/** One contest's research, over the ballot: the whole screen on a phone
 * (one contest per screen, the voting-machine pattern), a panel beside
 * the ballot on desktop so the ballot stays in view. Previous/Next walk
 * the ballot in order, so a reader can go through every contest without
 * returning to the index.
 *
 * A real modal dialog: focus moves into it and is kept there, Escape
 * closes it, and focus goes back to whatever opened it. */
export default function ContestDrawer({
  contest,
  index,
  total,
  prev,
  next,
  onNavigate,
  onClose,
  children,
}: {
  contest: BallotContest;
  index: number;
  total: number;
  prev: BallotContest | null;
  next: BallotContest | null;
  onNavigate: (key: string) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useModalDialog(onClose);
  const heading = useRef<HTMLHeadingElement>(null);

  // Each contest starts at its top, with its title announced.
  useEffect(() => {
    heading.current?.focus();
    panel.current?.querySelector("[data-drawer-body]")?.scrollTo?.({ top: 0 });
  }, [contest.key, panel]);

  return (
    <div className="fixed inset-0 z-[60] flex justify-end">
      <button
        type="button"
        aria-label="Close and return to the ballot"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 hidden bg-surface-base/80 lg:block"
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="contest-drawer-title"
        className="relative flex h-full w-full flex-col border-white/25 bg-surface-base font-sans lg:w-[660px] lg:border-l"
      >
        <div className="flex items-center justify-between gap-3 border-b border-white/[0.14] px-4 py-2.5">
          <span className="font-mono text-xs tracking-[0.12em] text-ink-lo">
            CONTEST {index + 1} OF {total} · {COLUMN_LABEL[contest.column]}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="min-h-[44px] px-2 font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
          >
            ALL CONTESTS
          </button>
        </div>
        <div className="flex gap-[3px] px-4 pt-2" aria-hidden="true">
          {Array.from({ length: total }, (_, i) => (
            <span key={i} className={`h-1 flex-1 ${i === index ? "bg-phos" : "bg-white/25"}`} />
          ))}
        </div>

        <div data-drawer-body className="flex-1 overflow-y-auto px-4 pb-6 pt-4">
          {/* The contest and its research, as one shareable image. */}
          <div {...{ [SHARE_SECTION_ATTR]: `contest-${contest.key}` }} className="bg-surface-base">
            <div className="mb-4 flex items-baseline justify-between gap-3 border border-white/25 bg-surface-raised px-4 py-3">
              <div className="min-w-0">
                <h2
                  id="contest-drawer-title"
                  ref={heading}
                  tabIndex={-1}
                  className="text-[19px] font-bold leading-tight text-ink-hi outline-none"
                >
                  {contest.title}
                </h2>
                <p className="mt-0.5 text-[13px] text-ink-lo">{contest.subtitle}</p>
              </div>
              <span className="flex shrink-0 items-center gap-3">
                {contest.instruction && (
                  <span className="font-mono text-xs text-ink-lo">{contest.instruction}</span>
                )}
                <ShareSectionButton label={contest.title} anchored={false} />
              </span>
            </div>
            {children}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 border-t border-white/[0.14] p-3">
          <button
            type="button"
            disabled={!prev}
            onClick={() => prev && onNavigate(prev.key)}
            className="min-h-[48px] border border-white/25 px-3 text-left text-sm text-ink-hi hover:border-white/50 disabled:text-ink-min disabled:hover:border-white/25"
          >
            {prev ? `←\u00a0${prev.title}` : "←\u00a0First contest"}
          </button>
          <button
            type="button"
            onClick={() => (next ? onNavigate(next.key) : onClose())}
            className="min-h-[48px] bg-phos px-3 text-right text-sm font-bold text-surface-base hover:bg-phos-mid"
          >
            {next ? `${next.title}\u00a0→` : "Back to the ballot"}
          </button>
        </div>
      </div>
    </div>
  );
}
