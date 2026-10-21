import { pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import {
  Summary,
  Point,
  Section,
  P,
  Boxes,
  Box,
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
                  2026-10-20: every public route, at 320, 375 and 1280 pixels wide and at 200% text
                  size, by keyboard and with axe-core.
                </Fact>
              </Facts>
            </Section>

            <Section id="features" title="What you can rely on">
              <Boxes>
                <Box
                  title="Keyboard"
                  dek="Everything works from the keyboard. Dialogs, drawers and menus keep focus inside, close on Escape and give focus back."
                  more="every keyboard path"
                >
                  <P>
                    Everything works from the keyboard. A skip link at the top of each page jumps
                    past the navigation. In a set of tabs, the arrow keys, Home and End move between
                    tabs and Tab moves into the content. In the main navigation, the button beside
                    each section lists its pages as plain links; Escape closes the list and returns
                    focus to the button, and moving focus away closes it too. In the phone menu each
                    section&apos;s pages open from a button beside it that says whether they are
                    showing. Choosing your state says what it set, in a message a screen reader
                    reads out, with links to that state&apos;s members and ballot. The mobile menu,
                    the share dialog, the map&apos;s seat sheet and the member-page and ballot
                    drawers keep focus inside while open (Tab and Shift+Tab both wrap), close on
                    Escape, and return focus to what opened them. Map tiles are one keyboard stop
                    per state, opened with Enter. A town split between House districts lists its
                    parts under its map as buttons, so a district can be picked without the map.
                  </P>
                </Box>
                <Box
                  title="Long pages on a phone"
                  dek="Long pages show a bar under the header naming the section you are in, with a sheet of every section."
                  more="how the bar and sheet work"
                >
                  <P>
                    A member&apos;s page, a bill, a roll call, a state ballot, the Supreme Court
                    page and the About chapters show a bar under the header once their first section
                    scrolls up: it names the section you are reading, and sections opens every
                    section in a sheet from the bottom of the screen, the current one marked.
                    Choosing one moves focus to its heading. The sheet keeps focus inside, closes on
                    Escape, its 44-pixel Close button or a tap outside it, and returns focus to
                    sections.
                  </P>
                </Box>
                <Box
                  title="Focus"
                  dek="Keyboard focus always shows a 2px green outline and is never hidden under the header."
                  more="the header and its status"
                >
                  <P>
                    The element with keyboard focus always shows a 2px green outline, and the page
                    scrolls so it is never hidden under the fixed header, or under a phone&apos;s
                    section bar. A phone&apos;s header comes back whenever focus moves into it. Its
                    data status is a small square beside the name, a button that reads out and shows
                    the whole status line.
                  </P>
                </Box>
                <Box
                  title="Contrast"
                  dek="The faintest text clears 4.5:1 on every background in both themes. Links in a sentence are underlined."
                  figure={{ type: "stat", value: 4.5, text: "4.5:1", caption: "faintest text" }}
                  more="themes, charts and maps"
                >
                  <P>
                    The site is dark text on a warm paper by default, and a warm dark theme follows
                    your device&apos;s dark setting, or the &ldquo;Dark theme&rdquo; button in the
                    footer (kept for this tab only). Text colours come from a fixed palette in each
                    theme with no dimmer shades to reach for. The faintest text clears 4.5:1 against
                    every background any text sits on, in both themes, and stays at or above APCA Lc
                    58. Links inside a sentence are underlined, not only coloured. Charts and maps
                    follow the theme you are viewing: their marks clear 3:1 against the page in both
                    themes, text on a filled cell clears 4.5:1, and letters on map tiles sit on an
                    outline in the page&apos;s colour, so they read on any tile colour. A shared
                    picture of a section is drawn in the theme you are viewing.
                  </P>
                </Box>
                <Box
                  title="Large text"
                  dek="At 200% text on a 320-pixel screen no page scrolls sideways."
                  more="wide tables"
                >
                  <P>
                    At 200% text on a 320-pixel screen no page scrolls sideways: a table too wide
                    for the screen scrolls inside its own box, which is a named region you can reach
                    with Tab and scroll with the arrow keys.
                  </P>
                </Box>
                <Box
                  title="Screen readers"
                  dek="Pages use landmarks and headings in order. Tabs, dialogs and live messages carry their ARIA roles."
                  more="tables, links and filters"
                >
                  <P>
                    Pages use landmarks (header, navigation, main) and headings in order. Tabs,
                    dialogs, score bars and live loading and error messages carry their ARIA roles,
                    and every filter button says whether it is on. Data tables have column headers
                    and a caption. Links that open a new tab say so. Decorative marks are hidden.
                  </P>
                </Box>
                <Box
                  title="Explanations"
                  dek="Every score's [?] explanation is in the page text, so a screen reader reads it with the button."
                  more="the plain sentences"
                >
                  <P>
                    Every score has a <span className="font-mono text-sm text-ink-hi">[?]</span>{" "}
                    button whose explanation is in the page text itself, so a screen reader reads it
                    with the button. Each of a member&apos;s three scores opens with a plain
                    sentence giving that member&apos;s own figures. Each <A href="/about">About</A>{" "}
                    chapter opens with a short version before any method.
                  </P>
                </Box>
                <Box
                  title="Motion"
                  dek="With reduced motion on, animations stop and the page jumps instead of scrolling smoothly."
                  more="the phone header"
                >
                  <P>
                    With reduced motion turned on in your system settings, animations stop,
                    transitions are instant and the page jumps instead of scrolling smoothly. On a
                    phone the header, which otherwise slides away while you scroll down, stays put.
                  </P>
                </Box>
                <Box
                  title="Windows High Contrast"
                  dek={
                    <>
                      In forced-colours mode, decorative colour effects that would smear text are
                      removed.
                    </>
                  }
                />
                <Box
                  title="Touch"
                  dek="Controls are at least 24×24 pixels. On a phone, actions, text boxes and menus are 44 pixels tall."
                  figure={{ type: "stat", value: 24, text: "24px", caption: "smallest target" }}
                  more="targets on a phone"
                >
                  <P>
                    Controls are at least 24×24 pixels, WCAG 2.2&apos;s minimum target size, or
                    spaced so a 24-pixel circle around each touches no other; links inside a
                    sentence are the exception the standard allows. On a phone the menu button, the
                    way to every section, is a 44×44 target around the word, every standalone action
                    (calling or writing a member, sharing, paging, a roll call&apos;s names) is 44
                    pixels tall, and every text box and menu is 44 pixels tall with 16-pixel text,
                    so an iPhone doesn&apos;t zoom the page when one is tapped. On a phone the state
                    map on Elections is a grid of equal tiles, each a 24-pixel target, since the
                    real shapes of the smaller states are too small to tap. The floating back-to-top
                    button hides while a link, control or map is under it. Nothing needs dragging.
                  </P>
                </Box>
                <Box
                  title="Pop-up explanations"
                  dek={
                    <>
                      A score&apos;s explanation opens on hover, focus or tap, stays open while the
                      pointer moves onto it, and closes with Escape wherever focus is.
                    </>
                  }
                />
                <Box
                  title="Dot strips and charts"
                  dek="Pointing near a dot, bar or segment names what it is. Each chart is one Tab stop, moved with the arrow keys, and its figures are also written out."
                  more="pointer, touch and keyboard"
                >
                  <P>
                    On a strip with a dot per member, president or justice, pointing near a dot
                    names who it is, with the figure and its place on the row; the nearest dot is
                    picked, so no exact aim is needed, and a click opens that page. On a phone a tap
                    shows the label and a tap on the label opens the page. Each strip is one Tab
                    stop: the arrow keys move from dot to dot in order of value, Home and End go to
                    the ends, Enter opens the page, and a screen reader hears each dot as it is
                    reached. Every strip also has a table of the same figures with linked names.
                  </P>
                  <P>
                    Other charts work the same way, one chart at a time: a vote&apos;s yea and nay
                    bar names each side with its votes and share and links to who voted each way, a
                    party&apos;s dots name each member and their vote, a comment period&apos;s bar
                    gives its days gone and its dates, each voter deadline gives its rule and
                    source, each column of a race&apos;s lean chart names the seats in it, and each
                    state on the Elections maps gives its name, its figure and its page. Each is one
                    Tab stop the arrow keys walk (a map goes state by state, west to east and north
                    to south), and its figures are also written out beside it.
                  </P>
                  <P>
                    On a president&apos;s page, each month or year of a line gives its figure and
                    the other presidencies&apos; middle half at the same point, each bar its year,
                    and each election its winner, electoral votes and popular margin. A
                    justice&apos;s estimates name each justice and the interval. On a member&apos;s
                    page, each slice of the holdings pie gives its share and its disclosed sum
                    (Enter lists those assets, as the legend does), each policy area its side and
                    strength, and a measure over time each night&apos;s figure. A voter
                    deadline&apos;s tick sits over the early-voting band, so pointing within 12
                    pixels of it reaches it.
                  </P>
                </Box>
                <Box
                  title="Typefaces, and why"
                  dek="Fira Sans for reading, Source Serif 4 for titles and DM Mono for record numbers. Labels are in sentence case."
                  more="typefaces and sentence case"
                >
                  <P>
                    Prose, labels and figures are set in Fira Sans, a humanist sans with open letter
                    shapes that stay readable at small sizes, with figures of equal width so columns
                    of numbers line up. Page and section titles are in Source Serif 4. DM Mono is
                    kept for record identifiers (issue, roll-call and bill numbers), where telling 0
                    from O and 1 from l matters. The Civitas wordmark is Archivo. All four are
                    open-licence fonts served from this site.
                  </P>
                  <P>
                    Labels, buttons, menus and table headings are in sentence case, not capitals.
                    Capitals are slower to read once a label runs past a word or two, and a screen
                    reader can spell typed capitals out letter by letter. A short heading over a
                    block may still show in small capitals, but the words underneath are ordinary
                    text, so they are read as words.
                  </P>
                </Box>
              </Boxes>
            </Section>

            <Section id="testing" title="How it is checked">
              <Boxes>
                <Box
                  title="Lighthouse, on every change"
                  dek="Each pull request runs Lighthouse twice, on empty pages and on seeded data. Every route has to score 100."
                  figure={{ type: "stat", value: 100, caption: "required score" }}
                  more="the two runs"
                >
                  <P>
                    Each pull request builds the site and runs Lighthouse&apos;s accessibility audit
                    twice. The first run has no backend, so pages show the empty and error states a
                    visitor sees when a source is down (12 routes). The second uses a seeded
                    fictional dataset, so profiles, score tables, documents and state ballots are
                    audited with content (17 routes). Every route has to score 100.
                  </P>
                </Box>
                <Box
                  title="axe-core in a browser"
                  dek="The same routes load in Chrome at 320 and 1280 pixels and are checked against every WCAG A and AA rule in axe-core."
                  more="what fails a page"
                >
                  <P>
                    The same routes, on the same production build, are loaded in Chrome at 320 and
                    1280 pixels wide and checked against every axe-core rule for WCAG 2.0, 2.1 and
                    2.2 at levels A and AA, including colour contrast and target size. A page wider
                    than a 320-pixel screen fails too, and so does a text box or menu under 16
                    pixels or 44 pixels tall at that width.
                  </P>
                </Box>
                <Box
                  title="axe-core, in the test suite"
                  dek="Member, president and justice pages, Congress reports and the state ballot page are checked with axe-core on every change."
                  more="which pages"
                >
                  <P>
                    The member, president and justice pages, the Congress report pages and the state
                    ballot page are rendered in tests and checked with axe-core. These tests run on
                    every change.
                  </P>
                </Box>
                <Box
                  title="Lint"
                  dek={
                    <>
                      ESLint&apos;s jsx-a11y rules check ARIA attributes, labels and roles as code
                      is written.
                    </>
                  }
                />
                <Box
                  title="Palette tests"
                  dek="Tests check every text and background pair in the code, in both themes, and fail below 4.5:1."
                  more="what the tests hold"
                >
                  <P>
                    Tests fail if code uses a stock Tailwind colour or a semi-transparent colour
                    outside the palette. They hold each text colour to its APCA floor on every
                    background in both themes, and compute the WCAG ratio of every text and
                    background pair written in the code, failing below 4.5:1 (3:1 for large text).
                  </P>
                </Box>
                <Box
                  title="What the checks don't cover"
                  dek="Automated tools miss some failures. Text in charts and keyboard paths are also checked by hand."
                  more="what the automated checks don't cover"
                >
                  <P>
                    Automated tools find some kinds of failure and not others. They can confirm that
                    a button has a name, not that the name makes sense. Contrast is measured in the
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
                </Box>
              </Boxes>
            </Section>

            <Section id="gaps" title="Known gaps">
              <Boxes>
                <Box
                  title="No audit with disabled users"
                  dek={
                    <>
                      The checks above are automated or done by us. We have not run a formal audit
                      with screen reader, switch or voice-control users.
                    </>
                  }
                />
                <Box
                  title="Shared images"
                  dek={
                    <>
                      An image made with a Share button has no description of its own once it leaves
                      the site: the preview in the dialog is described, but the saved file is not.
                      Add a description where you post it.
                    </>
                  }
                />
                <Box
                  title="The admin dashboard"
                  dek={
                    <>
                      The operators&apos; dashboard behind a login is not part of the public site
                      and is not in the audit.
                    </>
                  }
                />
                <Box
                  title="Not every page variant"
                  dek={
                    <>
                      The browser checks cover a fixed list of routes. Pages built from records the
                      fixture doesn&apos;t have (a live election night, a monitor&apos;s history)
                      are checked by hand when they change.
                    </>
                  }
                />
              </Boxes>
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
