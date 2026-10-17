import type { CommentWindow } from "@/types/commentWindow";

/** How much of a comment period is left: a bar filled to today, and the same
 *  fact in words beside it (the bar is decoration; the words are what a
 *  screen reader reads). */
export default function CommentWindowBar({
  period: w,
  className = "",
}: {
  period: CommentWindow;
  className?: string;
}) {
  const words =
    w.daysLeft === 0
      ? `Closes today, the last of ${w.totalDays} days`
      : `${w.daysLeft} of ${w.totalDays} day${w.totalDays === 1 ? "" : "s"} left to comment`;
  return (
    <span className={`flex items-center gap-3 ${className}`}>
      <span aria-hidden="true" className="relative h-1.5 w-24 shrink-0 bg-white/[0.12]">
        <span
          className="absolute inset-y-0 left-0 bg-signal-cyan"
          style={{ width: `${w.elapsedPercent}%` }}
        />
      </span>
      <span className="font-mono text-xs tracking-[0.06em] text-ink-lo">{words}</span>
    </span>
  );
}
