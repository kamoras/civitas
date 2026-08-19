import type { Metadata } from "next";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import { SCORE_VERSIONS } from "@/lib/scoreVersions";

export const metadata: Metadata = {
  title: "Scoring Changelog — Civitas",
  description:
    "Version history of the Civitas scoring algorithms — every formula and data-input change, and why it was made.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel mb-6">
      <div className="p-6 space-y-4">
        <h2 className="text-signal-cyan font-mono text-sm tracking-widest">{title}</h2>
        {children}
      </div>
    </section>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-ink-lo font-mono">{children}</span>;
}

export default function ChangelogPage() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <PageMasthead
            className="mb-10"
            eyebrow="Changelog · versioned scoring methodology"
            title="Scoring changelog"
          >
            <p>
              The scoring algorithms are versioned. When a formula or its data inputs change, every
              affected score can shift on the next nightly run — the trend charts mark these dates
              so a methodology update is never mistaken for a change in a politician&apos;s
              behavior. See the{" "}
              <a
                href="/about"
                className="text-signal-cyan hover:text-phos underline underline-offset-2"
              >
                methodology page
              </a>{" "}
              for how scores are calculated.
            </p>
          </PageMasthead>

          <Section title="VERSION HISTORY">
            <div className="space-y-6">
              {SCORE_VERSIONS.map((v) => (
                <div key={v.version}>
                  <Label>
                    {v.version} — {v.title} ({v.date})
                  </Label>
                  {v.tldr && (
                    <p className="text-base text-signal-cyan leading-relaxed font-medium mt-1">
                      <span className="text-signal-amber">In short:</span> {v.tldr}
                    </p>
                  )}
                  <ul className="list-disc pl-5 space-y-1 text-sm text-ink mt-1">
                    {v.changes.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Section>
        </div>
      </main>
      <Footer />
    </>
  );
}
