"use client";

import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import { BOXED_CONTROL } from "@/lib/controlStyles";

/** The president list could not be fetched (page.tsx throws for it). */
export default function ComparePresidentsError({ retry }: { retry: () => void }) {
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-5xl">
          <PageMasthead eyebrow="Compare · two presidents, side by side" title="Compare presidents">
            <p>
              The presidents&apos; records could not be reached just now. This is an outage on this
              site.
            </p>
          </PageMasthead>
          <button
            type="button"
            onClick={retry}
            className={`border px-3 py-2 font-mono text-xs uppercase tracking-[0.12em] transition-colors ${BOXED_CONTROL.unselected}`}
          >
            Try again
          </button>
        </div>
      </main>
      <Footer />
    </div>
  );
}
