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
    "Civitas accessibility statement: the WCAG 2.2 AA standard it is built to, what is checked automatically on every change, the gaps we know of, and how to report a barrier.",
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
        {/* `font-sans`: this is reading. The body element is `font-sans`
            too; named here so the page reads the same whatever it sits in. */}
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
                Civitas is built to WCAG 2.2 Level AA, and the project&apos;s rules don&apos;t merge
                a change that fails it. The gaps we know of are listed below; anything else you find
                is a bug.
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
                <Fact label="Standard">WCAG 2.2 Level AA</Fact>
                <Fact label="Status">
                  Partially conformant: every check below passes on every page we audit, and the
                  gaps we know of are listed under &ldquo;Known gaps&rdquo;.
                </Fact>
                <Fact label="Last reviewed">
                  2026-10-16: every public route, at 320, 375 and 1280 pixels wide and at 200% text
                  size, by keyboard and with axe-core.
                </Fact>
              </Facts>
            </Section>

            <Section id="features" title="What you can rely on">
              <List>
                <Item label="Keyboard">
                  Everything works from the keyboard. A skip link at the top of each page jumps past
                  the navigation. In a set of tabs, the arrow keys, Home and End move between tabs
                  and Tab moves into the content. In the main navigation, the button beside each
                  section lists its pages as plain links; Escape closes the list and returns focus
                  to the button, and moving focus away closes it too. In the phone menu each
                  section&apos;s pages open from a button beside it that says whether they are
                  showing. Choosing your state says what it set, in a message a screen reader reads
                  out, with links to that state&apos;s members and ballot. The mobile menu, the
                  share dialog, the map&apos;s seat sheet and the member-page and ballot drawers
                  keep focus inside while open (Tab and Shift+Tab both wrap), close on Escape, and
                  return focus to what opened them. Map tiles are one keyboard stop per state,
                  opened with Enter.
                </Item>
                <Item label="Long pages on a phone">
                  A member&apos;s page, a bill, a roll call, a state ballot, the Supreme Court page
                  and the About chapters show a bar under the header once their first section
                  scrolls up: it names the section you are reading, and SECTIONS opens every section
                  in a sheet from the bottom of the screen, the current one marked. Choosing one
                  moves focus to its heading. The sheet keeps focus inside, closes on Escape, its
                  44-pixel Close button or a tap outside it, and returns focus to SECTIONS.
                </Item>
                <Item label="Focus">
                  The element with keyboard focus always shows a 2px green outline, and the page
                  scrolls so it is never hidden under the fixed header, or under a phone&apos;s
                  section bar. A phone&apos;s header comes back whenever focus moves into it. Its
                  data status is a small square beside the name, a button that reads out and shows
                  the whole status line.
                </Item>
                <Item label="Contrast">
                  The site is dark text on a warm paper by default, and a warm dark theme follows
                  your device&apos;s dark setting, or the &ldquo;Dark theme&rdquo; button in the
                  footer (kept for this tab only). Text colours come from a fixed palette in each
                  theme with no dimmer shades to reach for. The faintest text clears 4.5:1 against
                  every background any text sits on, in both themes, and stays at or above APCA Lc
                  58. Links inside a sentence are underlined, not only coloured. Maps and the
                  Court&apos;s agreement grid are drawn on a dark plate in both themes, the ground
                  their tile colours were measured on; letters on map tiles sit on a dark outline,
                  so they read on any tile colour.
                </Item>
                <Item label="Screen readers">
                  Pages use landmarks (header, navigation, main) and headings in order. Tabs,
                  dialogs, score bars and live loading and error messages carry their ARIA roles,
                  and every filter button says whether it is on. Data tables have column headers and
                  a caption. Links that open a new tab say so. Decorative marks are hidden.
                </Item>
                <Item label="Explanations">
                  Every score has a <span className="font-mono text-sm text-ink-hi">[?]</span>{" "}
                  button whose explanation is in the page text itself, so a screen reader reads it
                  with the button. Each of a member&apos;s three scores opens with a plain sentence
                  giving that member&apos;s own figures. Each <A href="/about">About</A> chapter
                  opens with a short version before any method.
                </Item>
                <Item label="Motion">
                  With reduced motion turned on in your system settings, animations stop,
                  transitions are instant and the page jumps instead of scrolling smoothly. On a
                  phone the header, which otherwise slides away while you scroll down, stays put.
                </Item>
                <Item label="Windows High Contrast">
                  In forced-colours mode, decorative colour effects that would smear text are
                  removed.
                </Item>
                <Item label="Touch">
                  Controls are at least 24×24 pixels, WCAG 2.2&apos;s minimum target size, or spaced
                  so a 24-pixel circle around each touches no other; links inside a sentence are the
                  exception the standard allows. On a phone the MENU button, the way to every
                  section, is a 44×44 target around the word, every standalone action (calling or
                  writing a member, sharing, paging, a roll call&apos;s names) is 44 pixels tall,
                  and every text box and menu is 44 pixels tall with 16-pixel text, so an iPhone
                  doesn&apos;t zoom the page when one is tapped. Nothing needs dragging.
                </Item>
                <Item label="Pop-up explanations">
                  A score&apos;s explanation opens on hover, focus or tap, stays open while the
                  pointer moves onto it, and closes with Escape wherever focus is.
                </Item>
              </List>
              <More label="Typefaces, and why">
                <P>
                  Prose, labels and figures are set in Fira Sans, a humanist sans with open letter
                  shapes that stay readable at small sizes, with figures of equal width so columns
                  of numbers line up. Page and section titles are in Source Serif 4. DM Mono is kept
                  for record identifiers (issue, roll-call and bill numbers), where telling 0 from O
                  and 1 from l matters. The Civitas wordmark is Archivo. All four are open-licence
                  fonts served from this site.
                </P>
              </More>
            </Section>

            <Section id="testing" title="How it is checked">
              <List>
                <Item label="Lighthouse, on every change">
                  Each pull request builds the site and runs Lighthouse&apos;s accessibility audit
                  twice. The first run has no backend, so pages show the empty and error states a
                  visitor sees when a source is down (12 routes). The second uses a seeded fictional
                  dataset, so profiles, score tables, documents and state ballots are audited with
                  content (17 routes). Every route has to score 100.
                </Item>
                <Item label="axe-core in a browser, on every change">
                  The same routes, on the same production build, are loaded in Chrome at 320 and
                  1280 pixels wide and checked against every axe-core rule for WCAG 2.0, 2.1 and 2.2
                  at levels A and AA, including colour contrast and target size. A page wider than a
                  320-pixel screen fails too, and so does a text box or menu under 16 pixels or 44
                  pixels tall at that width.
                </Item>
                <Item label="axe-core, in the test suite">
                  The member, president and justice pages, the Congress report pages and the state
                  ballot page are rendered in tests and checked with axe-core. These tests run on
                  every change.
                </Item>
                <Item label="Lint">
                  ESLint&apos;s jsx-a11y rules check ARIA attributes, labels and roles as code is
                  written.
                </Item>
                <Item label="Palette tests">
                  Tests fail if code uses a stock Tailwind colour or a semi-transparent colour
                  outside the palette. They hold each text colour to its APCA floor on every
                  background in both themes, and compute the WCAG ratio of every text and background
                  pair written in the code, failing below 4.5:1 (3:1 for large text).
                </Item>
              </List>
              <More label="What the automated checks don't cover">
                <P>
                  Automated tools find some kinds of failure and not others. They can confirm that a
                  button has a name, not that the name makes sense. Contrast is measured in the
                  browser checks, not in the component tests, which run without a real renderer;
                  axe-core does not measure text drawn inside a chart or map, so those colours are
                  checked by the palette tests and by hand. Keyboard behaviour is covered by
                  component tests where it has regressed before (the tab bars and dialog focus are
                  two). Otherwise it is checked by hand on a production build when it changes, not
                  on a schedule.
                </P>
                <P>
                  The project&apos;s rules don&apos;t merge a change that fails these checks, but
                  the browser job (Lighthouse and axe-core) is not yet a required status check on
                  GitHub, so a failure shows on the pull request rather than technically blocking
                  the merge.
                </P>
              </More>
            </Section>

            <Section id="gaps" title="Known gaps">
              <List>
                <Item label="No audit with disabled users">
                  The checks above are automated or done by us. We have not run a formal audit with
                  screen reader, switch or voice-control users.
                </Item>
                <Item label="Shared images">
                  An image made with a Share button has no description of its own once it leaves the
                  site: the preview in the dialog is described, but the saved file is not. Add a
                  description where you post it.
                </Item>
                <Item label="The admin dashboard">
                  The operators&apos; dashboard behind a login is not part of the public site and is
                  not in the audit.
                </Item>
                <Item label="Not every page variant">
                  The browser checks cover a fixed list of routes. Pages built from records the
                  fixture doesn&apos;t have (a live election night, a monitor&apos;s history) are
                  checked by hand when they change.
                </Item>
              </List>
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
