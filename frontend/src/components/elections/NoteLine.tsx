import type { ReactNode } from "react";
import { INLINE_LINK } from "@/lib/controlStyles";

/**
 * A note about the ballot above the contests, in one line: what it says
 * in short, with the full text behind "More" (a native disclosure, which
 * find-in-page still searches and opens). The ballot-basis, runoff,
 * top-two and ranked-choice notes use it, so a reader is told what the
 * lists are before the ballot (AGENTS.md section 7) without scrolling past
 * paragraphs to reach it. `alert` is for the one note that must not be
 * missed: FEC filers shown for a contest whose primary is decided.
 */
export default function NoteLine({
  lead,
  alert = false,
  testId,
  children,
}: {
  lead: ReactNode;
  alert?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <details
      data-testid={testId}
      className={`group/note mb-2 border-l-2 pl-3 text-sm ${
        alert ? "border-signal-red" : "border-ink-lo"
      }`}
    >
      <summary className="min-h-6 cursor-pointer list-none py-0.5 [&::-webkit-details-marker]:hidden">
        <span className={alert ? "font-semibold text-signal-red" : "text-ink"}>{lead}</span>{" "}
        <span className={`${INLINE_LINK} whitespace-nowrap`}>
          <span className="group-open/note:hidden">More</span>
          <span className="hidden group-open/note:inline">Less</span>
        </span>
      </summary>
      <div className="mt-1 pb-1 text-[13px] leading-relaxed text-ink-lo">{children}</div>
    </details>
  );
}
