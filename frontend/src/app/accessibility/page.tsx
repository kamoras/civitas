import type { Metadata } from "next";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";

export const metadata: Metadata = {
  title: "Accessibility Statement — Civitas",
  description:
    "Civitas accessibility conformance statement: WCAG 2.1 Level AA conformance, testing approach, and how to report barriers.",
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

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-base text-ink leading-relaxed">{children}</p>;
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-ink-lo font-mono">{children}</span>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3 text-sm">
      <span className="text-signal-amber font-mono shrink-0 sm:w-56">{label}</span>
      <span className="text-ink-lo">{value}</span>
    </div>
  );
}

function Check({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2 text-sm text-ink leading-relaxed">
      <span className="text-ink-hi shrink-0 font-mono text-xs mt-0.5">[✓]</span>
      <span>{children}</span>
    </li>
  );
}

export default function AccessibilityPage() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        {/* `font-sans` because this is the reading, not the data.

            The body element is `font-mono`, so anything that does not name a
            face inherits Share Tech Mono — which on these four documents meant
            103 of 105 paragraphs on /about alone. That contradicts the rule
            the palette states outright: "mono is data, IDs, timestamps, labels
            and status — the terminal voice, now meaning something because it
            is no longer also the body face" (tailwind.config.ts). It was still
            the body face here.

            Set on the container rather than on the `P` helper so raw `<p>`,
            `<li>` and inline prose are covered too. Everything that should
            stay mono on these pages — section headings, `Label`, `Row`'s key
            column, the +/- markers — already declares `font-mono` and still
            wins. The global default is left alone deliberately: the dense data
            surfaces were measured against Share Tech Mono's advance width (see
            the note in layout.tsx), and flipping it wholesale is a different
            change from fixing the prose. */}
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-10"
            eyebrow="Accessibility · how this site is built to be used"
            title="Accessibility"
          >
            <p>Our commitment to making civic data accessible to everyone.</p>
          </PageMasthead>

          <Section title="CONFORMANCE STATUS">
            <P>
              Civitas conforms to{" "}
              <Label>Web Content Accessibility Guidelines (WCAG) 2.1 Level AA</Label>. Every page is
              checked against this standard automatically on every code change (see Testing Approach
              below); no known non-conformances remain open. If you find one, it&apos;s a bug —
              please report it below.
            </P>
            <div className="space-y-2 mt-4">
              <Row label="Standard" value="WCAG 2.1 Level AA" />
              <Row label="Status" value="Fully conformant" />
              <Row label="Last reviewed" value="2026-07-27" />
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
                <Label>prefers-reduced-motion</Label> support — all animations disabled when
                requested
              </Check>
              <Check>
                <Label>prefers-contrast: more</Label> support — low-opacity text raised to full
                opacity, partisan colors lightened for improved contrast
              </Check>
              <Check>
                Plain-language summaries throughout — every score metric renders a jargon-free
                one-line explanation next to its technical label (no toggle to find), and each
                methodology section opens with an &ldquo;In short&rdquo; summary before the
                citations and formulas
              </Check>
              <Check>
                Score tooltips explain every metric — no number is shown without context; the
                explanation text is always present in the page (not injected only on hover), so
                it&apos;s reachable via CSS-only hover/focus even with JavaScript disabled
              </Check>
              <Check>
                External links announce &ldquo;opens in new tab&rdquo; to screen readers
              </Check>
              <Check>Decorative elements marked aria-hidden to prevent screen reader noise</Check>
              <Check>Data tables use proper th scope attributes and accessible captions</Check>
              <Check>
                Progress bars use role=&ldquo;progressbar&rdquo; with aria-valuenow/min/max
              </Check>
              <Check>
                Loading and error states use role=&ldquo;status&rdquo; and role=&ldquo;alert&rdquo;
              </Check>
              <Check>ESLint jsx-a11y plugin enforces ARIA correctness at development time</Check>
            </ul>
          </Section>

          <Section title="TESTING APPROACH">
            <P>
              Accessibility is verified through a combination of automated and manual testing, on
              every code change — not a one-time audit that goes stale:
            </P>
            <ul className="space-y-2 mt-3">
              <Check>
                <strong className="text-ink-hi">Automated Lighthouse CI gate</strong> — every pull
                request builds the site and runs Lighthouse&apos;s accessibility audit against every
                major route (home, leaderboard, bills, explore, and this page among them); the build
                is blocked from merging unless every route scores 100/100. This has been the case on
                every change since the gate was added.
              </Check>
              <Check>
                <strong className="text-ink-hi">ESLint jsx-a11y</strong> — runs on every code
                change, enforcing ARIA attribute correctness, label associations, and semantic role
                usage.
              </Check>
              <Check>
                <strong className="text-ink-hi">Manual keyboard testing</strong> — All interactive
                flows (scorecard navigation, tab switching, tooltip opening, form submission)
                verified with keyboard-only navigation.
              </Check>
              <Check>
                <strong className="text-ink-hi">Contrast verification</strong> — Every text color
                and opacity level actually used in the codebase (2026-07 audit) had its WCAG
                relative-luminance contrast ratio computed against the terminal background; any
                combination below 4.5:1 is floored in CSS to the minimum opacity, or substituted for
                a lighter shade, that clears it — enforced globally so it can&apos;t be missed on a
                new component. The floor is applied by text colour and opacity, not by typeface — no
                font is exempt from it.
              </Check>
              <Check>
                <strong className="text-ink-hi">Typeface metrics</strong> — typefaces are chosen on
                measured metrics, not period flavour. Body copy is set in Share Tech Mono, whose
                lowercase letters are 25% taller than the display face the site previously inherited
                for prose, and which distinguishes 0/O and 1/l/I. Some pages still set the display
                face explicitly for tabular and archival content; those are being migrated. The
                bitmap label face is drawn on an 8-cell-per-em grid and only rasterises cleanly when
                font size × display scale is a multiple of 8, so it is no longer used below 12px,
                where that condition is met at almost no common display scale.
              </Check>
              <Check>
                <strong className="text-ink-hi">Reduced motion</strong> — Animation behavior
                verified with prefers-reduced-motion enabled in browser settings.
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
                label="Feedback form"
                value={
                  <Link
                    href="/feedback"
                    className="text-signal-cyan hover:text-phos transition-colors"
                  >
                    civitas-research.org/feedback
                  </Link>
                }
              />
              <Row label="Response time" value="We aim to respond within 5 business days" />
              <Row
                label="What to include"
                value="Describe what you were trying to do, what happened, your browser and OS, and any assistive technology you use"
              />
            </div>
          </Section>

          <Section title="FORMAL COMPLAINTS">
            <P>
              If you are not satisfied with our response, you may contact the{" "}
              <a
                href="https://www.hhs.gov/civil-rights/filing-a-complaint/index.html"
                target="_blank"
                rel="noopener noreferrer"
                className="text-signal-cyan hover:text-phos transition-colors"
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
