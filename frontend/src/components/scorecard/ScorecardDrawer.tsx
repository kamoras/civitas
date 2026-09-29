"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { useModalDialog } from "@/lib/useModalDialog";
import {
  SectionHeadingLevelProvider,
  SectionsOpenProvider,
} from "@/components/shared/CollapsibleSection";

/** Everything behind one of the scorecard's "all of it" links — every vote,
 *  every donor, every bill — over the scorecard: a panel beside it on a
 *  desktop, the whole screen on a phone. The sections inside render open
 *  (a drawer is what the reader asked to see) at h3, under the drawer's
 *  h2. A real modal dialog: see useModalDialog. */
export default function ScorecardDrawer({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useModalDialog(onClose);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
    panel.current?.querySelector("[data-drawer-body]")?.scrollTo?.({ top: 0 });
  }, [title, panel]);

  return (
    <div className="fixed inset-0 z-[60] flex justify-end">
      <button
        type="button"
        aria-label="Close and return to the scorecard"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 hidden bg-surface-base/80 lg:block"
      />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="scorecard-drawer-title"
        className="relative flex h-full w-full flex-col border-white/25 bg-surface-base font-sans lg:w-[720px] lg:border-l"
      >
        <div className="flex items-start justify-between gap-3 border-b border-white/25 bg-surface-raised px-5 py-4">
          <div className="min-w-0">
            <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">{subtitle}</p>
            <h2
              id="scorecard-drawer-title"
              ref={heading}
              tabIndex={-1}
              className="mt-1 text-2xl font-extrabold leading-tight text-ink-hi outline-none"
            >
              {title}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="min-h-[44px] min-w-[44px] shrink-0 border border-white/25 px-3 font-mono text-xs tracking-[0.1em] text-ink hover:border-white/50 hover:text-phos"
          >
            CLOSE
          </button>
        </div>
        <div data-drawer-body className="flex-1 overflow-y-auto px-5 pb-8 pt-5">
          <SectionHeadingLevelProvider value="h3">
            <SectionsOpenProvider value={true}>{children}</SectionsOpenProvider>
          </SectionHeadingLevelProvider>
        </div>
      </div>
    </div>
  );
}
