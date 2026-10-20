import type { ReactNode } from "react";
import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";

export interface NextStep {
  href: string;
  label: string;
}

/**
 * A 404 page: the site chrome, the one glitch the site keeps (something
 * broke, and the page says so in the register's own voice), and the way on.
 *
 * A record page that misses (a member, a bill, a vote, an issue, a
 * document) names what it was looking for and offers the next step for
 * that kind of record (`steps`, and `children` for anything worked out in
 * the browser, such as the members whose names are closest to the
 * address). A generic "back to the record" left a reader who mistyped a
 * member's address five taps from the member. The status stays 404 and
 * Next marks the page noindex: these render from each segment's
 * not-found.tsx, after the route calls notFound().
 */
export default function RecordNotFound({
  label = "No record at this address",
  message = "This page doesn't exist or has moved. The public record is still out there.",
  steps = [],
  children,
}: {
  /** The panel's eyebrow: what has no record. */
  label?: string;
  message?: string;
  steps?: NextStep[];
  children?: ReactNode;
}) {
  return (
    /* The site chrome, like every other public page: a 404 is where
       wayfinding matters most, not least. */
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
          className="mb-4 font-display text-6xl font-semibold text-signal-magenta sm:text-8xl"
        />

        <div className="panel mb-8 w-full max-w-md p-6">
          <p className="mb-2 font-mono text-xs text-signal-cyan">{label}</p>
          <p className="font-sans text-base leading-relaxed text-ink-lo">{message}</p>
          {children}
          {steps.length > 0 && (
            <ul className="mt-5 flex flex-col gap-2 text-left">
              {steps.map((s) => (
                <li key={s.href}>
                  <Link
                    href={s.href}
                    className="flex min-h-11 items-center justify-between gap-3 border border-white/15 px-3 py-2 font-sans text-[15px] text-ink-hi transition-colors hover:border-phos/60 hover:text-phos"
                  >
                    {s.label}
                    <span aria-hidden="true" className="font-mono text-ink-min">
                      &rarr;
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        <Link
          href="/"
          className="border border-phos/40 px-6 py-2.5 font-mono text-sm text-phos-mid transition-colors hover:border-phos hover:text-phos"
        >
          Back to the record
        </Link>
      </main>
      <Footer />
    </>
  );
}
