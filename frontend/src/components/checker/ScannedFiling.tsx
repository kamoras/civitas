"use client";

import { useState } from "react";
import type { DisclosureScan } from "@/types/senator";
import { INLINE_LINK } from "@/lib/controlStyles";
import { SHARE_EXCLUDE_ATTR } from "@/lib/shareImage";

/** The alt text of one page: an image of text says nothing to a screen
 * reader, so it names what the page is and the official link stays beside it. */
export function pageAlt(scan: DisclosureScan, page: number): string {
  return `Page ${page} of ${scan.pages} of ${scan.title}, scanned as filed`;
}

const STEP =
  "min-h-11 sm:min-h-6 px-2 font-mono text-small text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed";

/** A scanned or paper disclosure, page by page, from this site's own copy
 * (backend/app/disclosure_scans.py): what a reader gets when the report
 * can't be read into a breakdown, instead of only a link out. */
export default function ScannedFiling({
  scan,
  sourceLink,
}: {
  scan: DisclosureScan;
  sourceLink: React.ReactNode;
}) {
  const [page, setPage] = useState(1);
  const [zoomed, setZoomed] = useState(false);

  if (scan.pages === 0) {
    return (
      <p className="text-small text-ink-lo" role="status">
        {scan.failedAt
          ? `The filing's page images couldn't be loaded (last tried ${scan.failedAt}).`
          : "The filing's page images aren't on this site yet."}{" "}
        {sourceLink}
      </p>
    );
  }

  const src = `${scan.pageUrlPrefix}${page}.webp`;
  const [width, height] = scan.pageSizes[page - 1] ?? [1700, 2200];
  const go = (next: number) => setPage(Math.min(scan.pages, Math.max(1, next)));

  return (
    <figure className="m-0">
      <figcaption className="text-small text-ink-lo">
        The filing as filed: a scanned paper form, shown as filed.{" "}
        <span className="text-ink-min">
          Its assets aren&apos;t charted: a scan can&apos;t be read into a breakdown reliably.
        </span>
      </figcaption>
      <div
        className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1"
        {...{ [SHARE_EXCLUDE_ATTR]: "" }}
      >
        <button
          type="button"
          className={STEP}
          onClick={() => go(page - 1)}
          disabled={page === 1}
          aria-label="Previous page of the filing"
        >
          &lt; prev
        </button>
        <span className="font-mono text-small text-ink-lo" role="status" aria-live="polite">
          Page {page} of {scan.pages}
        </span>
        <button
          type="button"
          className={STEP}
          onClick={() => go(page + 1)}
          disabled={page === scan.pages}
          aria-label="Next page of the filing"
        >
          next &gt;
        </button>
        <button
          type="button"
          className={STEP}
          aria-pressed={zoomed}
          onClick={() => setZoomed((z) => !z)}
        >
          {zoomed ? "Fit to width" : "Zoom in"}
        </button>
        <a href={src} target="_blank" rel="noopener" className={`${INLINE_LINK} text-small`}>
          Open full size
        </a>
      </div>
      {/* Scrollable when zoomed: tabIndex lets a keyboard scroll it. */}
      <div
        className="mt-2 max-h-[80vh] overflow-auto border border-white/15"
        tabIndex={0}
        role="region"
        aria-label={`Page ${page} of the filing`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- a stored scan, served as-is */}
        <img
          key={src}
          src={src}
          alt={pageAlt(scan, page)}
          width={width}
          height={height}
          className={zoomed ? "max-w-none" : "h-auto w-full"}
          style={zoomed ? { width } : undefined}
          decoding="async"
        />
      </div>
      <p className="mt-2 text-small leading-6 text-ink-min">
        Copied from the official filing{scan.fetchedAt ? ` on ${scan.fetchedAt}` : ""} ·{" "}
        {sourceLink}
      </p>
    </figure>
  );
}
