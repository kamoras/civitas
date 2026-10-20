import type { ReactNode } from "react";

/**
 * "Paperwork notices (N)": an agency's Paperwork Reduction Act notices,
 * folded under the rules and other notices (backend app/comment_periods).
 * They renew the forms an agency collects, and are open for comment like a
 * rule, so they are listed, just not first.
 */
export default function PaperworkFold({
  count,
  shown,
  children,
}: {
  count: number;
  /** How many the fold lists (the closing soonest), when fewer than count. */
  shown: number;
  children: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <details className="mt-3 border-t border-white/[0.07] pt-3">
      <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs text-signal-cyan hover:text-phos">
        Paperwork notices ({count})
      </summary>
      <p className="mt-2 text-xs text-ink-lo">
        Requests to renew or change a form the agency collects, under the Paperwork Reduction Act.
        {shown < count ? ` These are the ${shown} closing soonest.` : ""}
      </p>
      {children}
    </details>
  );
}
