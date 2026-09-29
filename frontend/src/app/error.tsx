"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import { BOXED_CONTROL } from "@/lib/controlStyles";

/** A page whose record could not be fetched (lib/ssrPayload.ts fetchRecord
 * throws for an outage). Says so, rather than a 404 that tells the reader
 * — and a search engine — the page doesn't exist. /congress has its own. */
export default function PageError({ retry }: { retry: () => void }) {
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <PageMasthead eyebrow="Temporarily unavailable" title="This page could not be loaded">
            <p>
              The record behind this page could not be reached just now. This is an outage on this
              site, not a missing record, and it is usually over within a minute or two. The{" "}
              <Link
                href="/"
                className="underline decoration-white/30 underline-offset-4 hover:text-phos"
              >
                home page
              </Link>{" "}
              may still load.
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
