import type { ReactNode } from "react";
import { INLINE_LINK } from "@/lib/controlStyles";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";

/**
 * "How to read this": a native disclosure beside a chart a newcomer may not
 * know how to read (a peer strip, a seat's lean, a split bar). Closed by
 * default so a returning reader isn't slowed; the words are in the page, so
 * find-in-page and a screen reader reach them. Left out of shared images,
 * which carry their own source line.
 */
export default function HowToRead({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <details className={`text-small text-ink-lo ${className}`} {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
      <summary
        className={`${INLINE_LINK} inline-flex min-h-6 cursor-pointer list-none items-center gap-1.5 [&::-webkit-details-marker]:hidden`}
      >
        How to read this
      </summary>
      <div className="mt-1 max-w-prose space-y-1 text-ink">{children}</div>
    </details>
  );
}
