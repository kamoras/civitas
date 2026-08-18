import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";

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
    <main className="flex min-h-screen flex-col items-center justify-center px-4 text-center">
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
  );
}
