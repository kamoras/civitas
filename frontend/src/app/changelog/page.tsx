import type { Metadata } from "next";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { SCORE_VERSIONS } from "@/lib/scoreVersions";

export const metadata: Metadata = {
  title: "Scoring Changelog — Civitas",
  description:
    "Version history of the Civitas scoring algorithms — every formula and data-input change, and why it was made.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="terminal-window mb-6">
      <TerminalTitlebar title={`${title.toLowerCase().replace(/ /g, "_")}.txt`} />
      <div className="p-6 space-y-4">
        <h2 className="text-neon-cyan font-terminal text-sm tracking-widest">{title}</h2>
        {children}
      </div>
    </section>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-neon-pink/80 font-terminal">{children}</span>;
}

export default function ChangelogPage() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <h1 className="font-pixel text-xl sm:text-3xl text-matrix-green tracking-widest mb-2">
              SCORING CHANGELOG
            </h1>
            <p className="text-matrix-green/40 text-sm max-w-xl mx-auto">
              The scoring algorithms are versioned. When a formula or its data inputs
              change, every affected score can shift on the next nightly run — the trend
              charts mark these dates so a methodology update is never mistaken for a
              change in a politician&apos;s behavior. See the{" "}
              <a href="/about" className="text-neon-cyan/70 hover:text-neon-cyan underline underline-offset-2">
                methodology page
              </a>{" "}
              for how scores are calculated.
            </p>
          </div>

          <Section title="VERSION HISTORY">
            <div className="space-y-6">
              {SCORE_VERSIONS.map((v) => (
                <div key={v.version}>
                  <Label>{v.version} — {v.title} ({v.date})</Label>
                  <ul className="list-disc pl-5 space-y-1 text-sm text-matrix-green/70 mt-1">
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
