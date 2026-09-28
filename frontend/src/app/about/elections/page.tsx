import { pageMetadata } from "@/lib/site";
import {
  AboutPage,
  Summary,
  Point,
  Section,
  Sub,
  P,
  List,
  Item,
  More,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "How State Ballot Pages Work",
  description:
    "What Civitas's state ballot pages show, what they leave out and why, how candidate lists are confirmed, and why ballot measures are quoted word for word with their drafter named.",
  path: "/about/elections",
});

export default function ElectionsChapter() {
  return (
    <AboutPage
      href="/about/elections"
      eyebrow="Methodology · elections"
      title="Elections & ballots"
      lede={
        <p>
          Each state has a ballot page built for researching what&apos;s on the ballot — who is
          running, their money and record, and every statewide measure in the state&apos;s own
          words.
        </p>
      }
    >
      <Summary>
        <Point>
          A ballot page shows what&apos;s the same across a whole state, plus your district once you
          pick it on a map or by county. It never asks for your address.
        </Point>
        <Point>
          Every page says what it leaves out, and links to your election office for the rest.
        </Point>
        <Point>
          Ballot measures are quoted word for word, with the person or body who wrote the words
          named. No AI writes or summarizes anything on these pages.
        </Point>
        <Point>
          Each page says what its candidate lists are — a certified ballot, primary results, or
          campaign filings — and when a primary has already passed.
        </Point>
      </Summary>

      <Section id="the-page" title="What a ballot page shows">
        <P>
          Each state&apos;s page at{" "}
          <span className="font-mono text-ink-hi">/elections/states/ST</span> is laid out as a
          research tool, not a mock ballot, and never marks a choice. On a computer it sets out the
          ballot in three columns — federal offices, state offices, then measures and local contests
          — and opens any contest&apos;s research beside it: money raised, a sitting member&apos;s
          voting record, news coverage. On a phone it opens one contest per screen.
        </P>
        <P>
          Candidates appear under the name their state prints on its ballot. Each contest states the
          term it is for — two years for the House, six for the Senate, and for state offices, the
          term set by that state&apos;s constitution or statute (none is shown where we haven&apos;t
          confirmed it). Where a state&apos;s own results name them, the page also covers statewide
          executive offices, state legislative seats and elected judgeships; where they aren&apos;t
          covered yet, it says so.
        </P>
      </Section>

      <Section id="not-your-ballot" title="Why it isn’t “your ballot”">
        <P>
          A ballot is defined by the exact set of contests a voter is eligible for. Precincts split
          by district lines carry several versions, one county can print dozens, and nationally
          there are tens of thousands. Showing someone their exact ballot would mean asking for
          their home address and sending it to a lookup service — which is exactly what Civitas is
          built not to do.
        </P>
        <P>
          So each page covers what&apos;s uniform statewide and lists what it omits — county and
          city offices, local measures, primary ballots and so on — with a link to your own election
          office. Once a gap closes for a state, it comes off that state&apos;s list: a disclaimer
          that outlives the gap stops describing the page.
        </P>
      </Section>

      <Section id="finding-your-district" title="Finding your district without typing an address">
        <P>
          Pick your county and the page narrows to the district covering it. About 13% of counties
          span more than one district; those offer the two or three as a second tap. Where a county
          isn&apos;t enough, a map of the districts — from the Census Bureau&apos;s 119th-Congress
          boundaries, shaded by partisan lean — lets you click yours. A text filter over place names
          and sitting representatives works too. All of it runs on data already on the page: nothing
          is typed into a lookup, sent or stored.
        </P>
        <Sub title="The optional town selector">
          <P>
            For a small, hand-picked list of towns, a selector shows local races — city council,
            school board, local measures. It never asks for or sends your address: it looks up a
            fixed public address we chose for that town, its town hall, so everyone who picks the
            town gets the identical lookup and nothing about you leaves the server.
          </P>
          <P>
            It is an approximation, and says so beside the selector: a town can contain several
            precincts, so a race tied to your street may be missing or one from across town may
            appear. Its source only carries an election close to the date, so months ahead a town
            may show &ldquo;could not load local races right now&rdquo; — that is the likely reason,
            not a fault.
          </P>
        </Sub>
      </Section>

      <Section id="candidates" title="Whose names are on the list">
        <P>Four different things can fill a candidate list, and each page says which one it has:</P>
        <List>
          <Item label="A certified ballot">
            The state&apos;s own list of who is on the November ballot — third-party and independent
            candidates included. When a state&apos;s source is its certified ballot, it is the final
            word: anyone not on it comes off the page.
          </Item>
          <Item label="Nominees from primary results">
            Stands in until a state certifies. It can&apos;t see a Libertarian, Green or independent
            who never ran in a primary, so the page says &ldquo;nominees&rdquo;, not &ldquo;the
            ballot&rdquo;.
          </Item>
          <Item label="A primary ballot">Before the primary, the people running in it.</Item>
          <Item label="Campaign filings">
            When nothing is confirmed yet, everyone who filed with the FEC — some of whom will never
            be on any ballot. Before a primary that is the best available answer; after one, the
            page says plainly that the ballot has been decided and we don&apos;t have it yet.
          </Item>
        </List>
        <P>
          Everyone on a certified ballot is shown, including candidates who never filed with the FEC
          (&ldquo;no FEC filing&rdquo; appears in place of fundraising figures). Declared write-ins
          aren&apos;t printed on the ballot and are never shown as if they were. A Senate race
          appears only where the FEC&apos;s election calendar lists one. We don&apos;t read
          candidate lists published only as scanned images — one misread name would take a real
          nominee off the page — and we don&apos;t work around bot challenges or logins on state
          election sites.
        </P>
        <More label="How the candidate lists were checked, September 2026">
          <P>
            On 26 September 2026, 39 states had certified candidates and eleven were still showing
            FEC filers months after their primary — Ohio by 144 days, New York by 95, with one New
            York race listing 25 filers for a ballot holding about two. Those pages said nominees
            &ldquo;aren&apos;t confirmed yet&rdquo;, telling readers a settled contest was open.
            Pages now lead with what their lists are.
          </P>
          <P>
            Re-probing those eleven found three causes. Nine relied on a national source that
            doesn&apos;t publish general-election candidates until close to the election. New
            Hampshire wasn&apos;t broken: its results are held for 21 days and were 18 days old.
            South Dakota was the one real fault — its election-night site had moved on to a July
            runoff — and now reads the Secretary of State&apos;s list of who is on the November
            ballot, which also shows an independent candidate primary results never could. A second
            look found Louisiana, South Carolina and Missouri publish their November ballots
            directly, and they are now read as such.
          </P>
          <P>
            Reading the ballot rather than primary results also catches a nominee replaced after the
            primary: in Maine, Graham Platner won the Democratic Senate primary, withdrew in July,
            and the party nominated Troy Jackson; in South Carolina a special primary replaced the
            June Senate winner. Colorado, Virginia, Tennessee, Florida, New Jersey, Maryland, Iowa,
            Nebraska, New Mexico, Wyoming, Hawaii, Delaware, Kentucky, Alaska, Montana, Illinois and
            North Dakota are read from their certified lists (Tennessee&apos;s federal races alone
            list 36 independents), and Wisconsin from its official primary canvass.
          </P>
          <P>
            Utah and Alabama publish certified lists only as scanned images. For races those states,
            Arkansas and Connecticut leave unseen — districts whose primary was uncontested —
            Google&apos;s election index fills in once it publishes the general election, and only
            for those races. Five more states sit behind bot challenges, or in Oklahoma&apos;s case
            an API that requires a login.
          </P>
          <P>
            Where a state&apos;s results are reached through a link on its own site, each linked
            election is checked for its own name and date first — West Virginia moved its primary
            link to an archive of every election since 2016. Texas lists write-ins alongside
            nominees, and until 27 September 2026 this site showed four of them as Senate
            candidates. Before the FEC calendar check, people who file paperwork in many states at
            once were enough to invent Senate special elections in New York and Hawaii; Florida and
            Ohio, which do hold special elections this year, now have their candidates filed under
            the right race.
          </P>
        </More>
      </Section>

      <Section id="measures" title="Ballot measures: quoted, never rewritten">
        <P>
          Each measure shows its official ballot title, official summary, fiscal impact statement,
          and the state&apos;s own description of what a YES and a NO vote do — all verbatim, linked
          to the source. Where a state publishes no yes/no description, none is shown. We never
          infer one: the intuitive reading (&ldquo;yes enacts it&rdquo;) is backwards on a veto
          referendum, where approving <em>keeps</em> the law being challenged.
        </P>
        <Sub title="Who wrote the words you’re reading">
          <P>
            Ballot titles are among the most litigated documents in election law — courts have
            voided measures over a legislature&apos;s wording. So each quote names its author
            (&ldquo;Drafted by the Georgia General Assembly&rdquo;, &ldquo;Prepared by the
            Legislative Analyst&rsquo;s Office&rdquo;): who wrote it tells you how to weigh it. We
            don&apos;t reproduce the pro and con arguments in voter guides; campaigns write those,
            some states sell the slots, and printing them as a matched pair would manufacture a
            balance that may not exist.
          </P>
        </Sub>
        <Sub title="“No measures” is different from “we don’t know yet”">
          <P>
            An empty section reads as &ldquo;nothing to research&rdquo; — a damaging thing to imply
            about a state with seventeen amendments pending. So a state our source reports as having
            no statewide measures says so, and a state we haven&apos;t covered, or whose ingest
            failed, says <em>that</em> and points to the official lookup. Measures removed from the
            ballot — courts have struck about 2.3% since 1995 — are marked removed and kept for a
            while, not silently deleted.
          </P>
        </Sub>
        <Sub title="Why there’s no plain-language summary">
          <P>
            Ballot language is hard to read — statewide measures averaged a grade-21 reading level
            in 2025 — and a plain-language version would genuinely help. We left it out on purpose.
            Our checks on generated text confirm that its words appear in the source; they
            can&apos;t tell whether a sentence points the same direction. &ldquo;A YES vote repeals
            this tax&rdquo;, when the official text says approval <em>keeps</em> it, passes every
            check, because every word is in the source. On a page that can change how someone votes,
            an error we can&apos;t detect is one we won&apos;t ship.
          </P>
        </Sub>
        <P>
          Where a state publishes its measures in a form we can read reliably — currently
          California, Colorado, Louisiana, Massachusetts, Missouri and Virginia — they are read
          straight from the state&apos;s own official voter guide. Elsewhere they come from Vote
          Smart&apos;s free public API, a nonpartisan nonprofit. The state&apos;s official source is
          linked from every measure. See{" "}
          <A href="/about/limitations#ballot-coverage">known limitations</A> for where coverage is
          still incomplete.
        </P>
      </Section>
    </AboutPage>
  );
}
