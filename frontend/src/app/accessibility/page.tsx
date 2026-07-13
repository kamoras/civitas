import type { Metadata } from "next";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import TerminalTitlebar from "@/components/TerminalTitlebar";

export const metadata: Metadata = {
  title: "Accessibility Statement — Civitas",
  description:
    "Civitas accessibility conformance statement: WCAG 2.1 Level AA target, known limitations, testing approach, and how to report barriers.",
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

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-matrix-green/70 leading-relaxed">{children}</p>;
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-neon-pink/80 font-terminal">{children}</span>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3 text-sm">
      <span className="text-neon-yellow/70 font-terminal shrink-0 sm:w-56">{label}</span>
      <span className="text-matrix-green/60">{value}</span>
    </div>
  );
}

function Check({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2 text-sm text-matrix-green/70 leading-relaxed">
      <span className="text-matrix-green shrink-0 font-pixel text-[10px] mt-0.5">[✓]</span>
      <span>{children}</span>
    </li>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2 text-sm text-neon-yellow/70 leading-relaxed">
      <span className="text-neon-yellow shrink-0 font-pixel text-[10px] mt-0.5">[!]</span>
      <span>{children}</span>
    </li>
  );
}

export default function AccessibilityPage() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <h1 className="font-pixel text-xl sm:text-3xl text-matrix-green tracking-widest mb-2">
              ACCESSIBILITY
            </h1>
            <p className="text-matrix-green/40 text-sm max-w-xl mx-auto">
              Our commitment to making civic data accessible to everyone.
            </p>
          </div>

          <Section title="CONFORMANCE STATUS">
            <P>
              Civitas targets conformance with{" "}
              <Label>Web Content Accessibility Guidelines (WCAG) 2.1 Level AA</Label>.
              We are <em className="text-matrix-green/80">partially conformant</em> — most
              features meet AA criteria, but some areas are still being improved as documented
              below.
            </P>
            <div className="space-y-2 mt-4">
              <Row label="Standard" value="WCAG 2.1 Level AA" />
              <Row label="Status" value="Partially conformant" />
              <Row label="Last reviewed" value="2026-05-20" />
            </div>
          </Section>

          <Section title="FEATURES">
            <P>Civitas includes the following accessibility features:</P>
            <ul className="space-y-2 mt-3">
              <Check>Skip to main content link at the top of every page</Check>
              <Check>Semantic HTML landmarks: header, nav, main, section, article</Check>
              <Check>Logical heading hierarchy (h1 → h2 → h3) on all pages</Check>
              <Check>ARIA labels and roles on interactive elements (tabs, buttons, modals)</Check>
              <Check>Full keyboard navigation: Tab, Shift-Tab, Arrow keys, Escape, Home/End</Check>
              <Check>Focus trap management in mobile navigation menu</Check>
              <Check>Visible focus indicators on all interactive elements (2px cyan outline)</Check>
              <Check>
                <Label>prefers-reduced-motion</Label> support — all animations disabled when requested
              </Check>
              <Check>
                <Label>prefers-contrast: more</Label> support — low-opacity text raised to full
                opacity, partisan colors lightened for improved contrast
              </Check>
              <Check>
                Plain language toggle — all five score metrics available in everyday language
                (e.g., &ldquo;PAC Money Reliance&rdquo; instead of &ldquo;Funding Independence&rdquo;)
              </Check>
              <Check>Score tooltips explain every metric — no number is shown without context</Check>
              <Check>External links announce &ldquo;opens in new tab&rdquo; to screen readers</Check>
              <Check>Decorative elements marked aria-hidden to prevent screen reader noise</Check>
              <Check>Data tables use proper th scope attributes and accessible captions</Check>
              <Check>Progress bars use role=&ldquo;progressbar&rdquo; with aria-valuenow/min/max</Check>
              <Check>Loading and error states use role=&ldquo;status&rdquo; and role=&ldquo;alert&rdquo;</Check>
              <Check>ESLint jsx-a11y plugin enforces ARIA correctness at development time</Check>
            </ul>
          </Section>

          <Section title="KNOWN LIMITATIONS">
            <P>
              We are aware of the following limitations and are actively working to address them:
            </P>
            <ul className="space-y-2 mt-3">
              <Warn>
                <strong className="text-neon-yellow">Decorative fonts</strong> — VT323 and
                Press Start 2P are pixel/bitmap fonts used for headings and labels. These are
                stylistic and may be harder to read for some users. All body text and data use
                Share Tech Mono or system monospace fonts. The decorative fonts are not used for
                content that requires precise reading.
              </Warn>
              <Warn>
                <strong className="text-neon-yellow">Low-opacity secondary text</strong> —
                Some secondary labels use reduced opacity for visual hierarchy. Opacity is
                floored by default so every text color still clears WCAG AA&apos;s 4.5:1
                minimum against the terminal background — this is enforced globally in CSS
                rather than per element, so it can&apos;t be missed on new components. In
                high-contrast mode (<code>prefers-contrast: more</code>), opacity is pushed
                further, to fully solid.
              </Warn>
              <Warn>
                <strong className="text-neon-yellow">JavaScript-dependent tooltips</strong> —
                Metric explanation tooltips require JavaScript. If JavaScript is unavailable,
                metric descriptions are not accessible.
              </Warn>
            </ul>
          </Section>

          <Section title="TESTING APPROACH">
            <P>
              Accessibility is verified through a combination of automated and manual testing:
            </P>
            <ul className="space-y-2 mt-3">
              <Check>
                <strong className="text-matrix-green">Automated</strong> — ESLint
                jsx-a11y plugin runs on every code change, enforcing ARIA attribute correctness,
                label associations, and semantic role usage.
              </Check>
              <Check>
                <strong className="text-matrix-green">Manual keyboard testing</strong> — All
                interactive flows (scorecard navigation, tab switching, tooltip opening, form
                submission) verified with keyboard-only navigation.
              </Check>
              <Check>
                <strong className="text-matrix-green">Contrast verification</strong> — Every
                text color and opacity level actually used in the codebase (2026-07 audit)
                had its WCAG relative-luminance contrast ratio computed against the terminal
                background; any combination below 4.5:1 is floored in CSS to the minimum
                opacity, or substituted for a lighter shade, that clears it.
              </Check>
              <Check>
                <strong className="text-matrix-green">Reduced motion</strong> — Animation
                behavior verified with prefers-reduced-motion enabled in browser settings.
              </Check>
            </ul>
          </Section>

          <Section title="HOW TO REPORT AN ISSUE">
            <P>
              If you encounter an accessibility barrier on Civitas — something that prevents you
              from using a feature or accessing information — please let us know.
            </P>
            <div className="space-y-2 mt-4">
              <Row
                label="GitHub Issues"
                value={
                  <a
                    href="https://github.com/ryanmack/civitas/issues"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-neon-cyan/80 hover:text-neon-cyan transition-colors"
                  >
                    github.com/ryanmack/civitas/issues
                  </a>
                }
              />
              <Row label="Response time" value="We aim to respond within 5 business days" />
              <Row label="What to include" value="Describe what you were trying to do, what happened, your browser and OS, and any assistive technology you use" />
            </div>
          </Section>

          <Section title="FORMAL COMPLAINTS">
            <P>
              If you are not satisfied with our response, you may contact the{" "}
              <a
                href="https://www.hhs.gov/civil-rights/filing-a-complaint/index.html"
                target="_blank"
                rel="noopener noreferrer"
                className="text-neon-cyan/80 hover:text-neon-cyan transition-colors"
              >
                U.S. Department of Health and Human Services Office for Civil Rights
              </a>{" "}
              or another relevant authority in your jurisdiction.
            </P>
          </Section>
        </div>
      </main>
      <Footer />
    </>
  );
}
