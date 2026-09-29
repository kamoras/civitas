import { pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import {
  Summary,
  Point,
  Section,
  P,
  List,
  Item,
  More,
  Facts,
  Fact,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "Accessibility Statement",
  description:
    "Civitas accessibility statement: the WCAG 2.1 AA standard it is built to, what is checked automatically on every change, what isn't, and how to report a barrier.",
  path: "/accessibility",
});

/* Every claim on this page is checked against the code, not remembered. The
   previous version promised a `prefers-contrast` mode that didn't exist, a CSS
   contrast clamp that had been deleted, tooltips that worked without
   JavaScript, table captions only one table had, and a merge gate that wasn't
   a required check. When a feature here changes, change this page with it. */
export default function AccessibilityPage() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        {/* `font-sans`: this is reading, not data. The body element is
            `font-mono`, and prose that names no face inherits it. */}
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-6"
            eyebrow="Accessibility · how this site is built to be used"
            title="Accessibility"
          >
            <p>
              The standard this site is built to, how it is checked, where the checks stop, and how
              to tell us about a barrier.
            </p>
          </PageMasthead>

          <div className="mt-10 space-y-12">
            <Summary>
              <Point>
                Civitas is built to WCAG 2.1 Level AA. We know of no open failures; if you find one,
                it is a bug.
              </Point>
              <Point>
                Every change is audited automatically, on pages with data and on the empty and error
                states a visitor can hit when a source is down.
              </Point>
              <Point>
                Automated checks don&apos;t catch everything, so the list below says exactly what
                they cover.
              </Point>
              <Point>
                Report a barrier through the <A href="/feedback">feedback form</A>. Reports are
                filed as public issues, so leave out anything you want kept private.
              </Point>
            </Summary>

            <Section id="status" title="Where the site stands">
              <Facts>
                <Fact label="Standard">WCAG 2.1 Level AA</Fact>
                <Fact label="Status">
                  Conformant, as far as we know: no known failures are open.
                </Fact>
                <Fact label="Last reviewed">2026-09-29</Fact>
              </Facts>
            </Section>

            <Section id="features" title="What you can rely on">
              <List>
                <Item label="Keyboard">
                  Everything works from the keyboard. A skip link at the top of each page jumps past
                  the navigation. In a set of tabs, the arrow keys, Home and End move between tabs
                  and Tab moves into the content. The mobile menu and the scorecard and ballot
                  drawers keep focus inside while open, close on Escape, and return focus to what
                  opened them.
                </Item>
                <Item label="Focus">
                  The element with keyboard focus always shows a 2px cyan outline.
                </Item>
                <Item label="Contrast">
                  Text colours come from a fixed palette with no dimmer shades to reach for. The
                  faintest text clears 4.5:1 against the lightest background any text sits on, and
                  stays at or above APCA Lc 58 on every background, a stricter measure of how light
                  text on a dark page actually reads.
                </Item>
                <Item label="Screen readers">
                  Pages use landmarks (header, navigation, main) and headings in order. Tabs,
                  dialogs, score bars and live loading and error messages carry their ARIA roles.
                  Data tables have column headers and a caption. Links that open a new tab say so.
                  Decorative marks are hidden.
                </Item>
                <Item label="Explanations">
                  Every score has a <span className="font-mono text-sm text-ink-hi">[?]</span>{" "}
                  button whose explanation is in the page text itself, so a screen reader reads it
                  with the button. Each of a member&apos;s three scores opens with a plain sentence
                  giving that member&apos;s own figures. Each <A href="/about">About</A> chapter
                  opens with a short version before any method.
                </Item>
                <Item label="Motion">
                  With reduced motion turned on in your system settings, animations stop and
                  transitions are instant.
                </Item>
                <Item label="Windows High Contrast">
                  In forced-colours mode, decorative colour effects that would smear text are
                  removed.
                </Item>
                <Item label="Touch">
                  The small score-explanation buttons are 24×24 pixels, WCAG 2.2&apos;s minimum
                  target size.
                </Item>
              </List>
              <More label="Typefaces, and why">
                <P>
                  Prose is set in Archivo, a plain grotesque. Share Tech Mono is kept for data, IDs
                  and labels, where telling 0 from O and 1 from l matters. The pixel face is used
                  for the Civitas wordmark and nothing else.
                </P>
              </More>
            </Section>

            <Section id="testing" title="How it is checked">
              <List>
                <Item label="Lighthouse, on every change">
                  Each pull request builds the site and runs Lighthouse&apos;s accessibility audit
                  twice. The first run has no backend, so pages show the empty and error states a
                  visitor sees when a source is down (10 routes). The second uses a seeded fictional
                  dataset, so profiles, score tables, documents and a state ballot are audited with
                  content (11 routes). Every route has to score 100.
                </Item>
                <Item label="axe-core, in the test suite">
                  The member, president and justice scorecards, the Congress report pages and the
                  state ballot page are rendered in tests and checked with axe-core. These tests run
                  on every change.
                </Item>
                <Item label="Lint">
                  ESLint&apos;s jsx-a11y rules check ARIA attributes, labels and roles as code is
                  written.
                </Item>
                <Item label="Palette tests">
                  Tests fail if code uses a stock Tailwind colour or a semi-transparent colour
                  outside the palette, and they hold each text colour to its APCA floor on every
                  background.
                </Item>
              </List>
              <More label="What the automated checks don't cover">
                <P>
                  Automated tools find some kinds of failure and not others. They can confirm that a
                  button has a name, not that the name makes sense. Contrast is checked in
                  Lighthouse, but not in the axe-core tests, which run without a real renderer.
                  Keyboard behaviour is covered by component tests where it has regressed before
                  (the tab bars are one). Otherwise it is checked by hand on a production build when
                  it changes, not on a schedule. We have not run a formal audit with screen reader
                  users.
                </P>
                <P>
                  The Lighthouse job runs on every pull request but is not a required status check,
                  so a failing score is visible on the pull request rather than technically blocking
                  the merge.
                </P>
              </More>
            </Section>

            <Section id="report" title="Reporting a barrier">
              <P>
                If something on Civitas stops you using a feature or reaching information, please
                tell us.
              </P>
              <Facts>
                <Fact label="Where">
                  The <A href="/feedback">feedback form</A>. Choose &ldquo;Accessibility
                  barrier&rdquo;.
                </Fact>
                <Fact label="What happens">
                  Your report is filed as a public issue in the project&apos;s open-source
                  repository. Don&apos;t include anything you want kept private.
                </Fact>
                <Fact label="What helps">
                  The page, what you were trying to do and what happened. If you&apos;re happy to
                  share them publicly, your browser and any assistive technology you use.
                </Fact>
                <Fact label="Response">We aim to respond within 5 business days.</Fact>
              </Facts>
            </Section>

            <Section id="complaints" title="If our response doesn't resolve it">
              <P>
                The{" "}
                <a
                  href="https://civilrights.justice.gov/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-signal-cyan underline underline-offset-2 hover:text-phos"
                >
                  U.S. Department of Justice Civil Rights Division
                </a>{" "}
                enforces the Americans with Disabilities Act and takes complaints online. You can
                also contact the relevant authority where you live.
              </P>
            </Section>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
