"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import { CongressTabs } from "@/components/congress/CongressNav";
import { BOXED_CONTROL } from "@/lib/controlStyles";

/** A report that could not be fetched (congressServer.ts throws for it).
 * Says so, rather than reading as "nothing recorded" or a 404. */
export default function CongressError({ retry }: { retry: () => void }) {
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <CongressTabs active="reports" />
          <PageMasthead eyebrow="What happened in Congress" title="Congress">
            <p>
              The record of Congress could not be reached just now. This is an outage on this site, not
              a day without a record. The{" "}
              <Link href="/congress/bills" className="underline decoration-white/30 underline-offset-4 hover:text-phos">
                bills tab
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
