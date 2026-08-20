import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";

/**
 * The one page that still glitches.
 *
 * Everywhere else the effect was removed — a Matrix motif argues against a
 * project whose claim is deterministic, auditable scoring. Here it is
 * literally true: something broke, and the page says so in the register's own
 * voice rather than borrowing an aesthetic.
 */
export default function NotFound() {
  return (
    /* The site chrome, like every other public page.

       This was the one route with no Navbar — and therefore no RecordsBand,
       against the design note that says the band sits above every page. It
       also meant a reader who arrived from a stale link had exactly one way
       out, "back to the record", and no way to reach Bills or Politicians or
       Elections directly. A 404 is where wayfinding matters most, not least. */
    <>
      <Navbar />
      <main
        id="main-content"
        tabIndex={-1}
        className="flex min-h-[70vh] flex-col items-center justify-center px-4 pt-[var(--header-clearance)] text-center"
      >
        <GlitchText
          text="404"
          as="h1"
          className="mb-4 font-display text-6xl font-extrabold text-signal-magenta sm:text-8xl"
        />

        <div className="panel mb-8 max-w-md p-6">
          <p className="mb-2 font-mono text-xs uppercase tracking-[0.16em] text-signal-cyan">
            No record at this address
          </p>
          <p className="font-display text-base leading-relaxed text-ink-lo">
            This page doesn&apos;t exist or has moved. The public record is still out there.
          </p>
        </div>

        <Link
          href="/"
          className="border border-phos/40 px-6 py-2.5 font-mono text-sm uppercase tracking-[0.14em] text-phos-mid transition-colors hover:border-phos hover:text-phos"
        >
          Back to the record
        </Link>
      </main>
      <Footer />
    </>
  );
}
