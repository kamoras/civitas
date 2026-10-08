import type { ReactNode } from "react";
import { pageMetadata } from "@/lib/site";
import { AboutPage, Summary, Point, Section, P, More, Cite, A } from "@/components/about/AboutPage";
import { countWord, fetchLiveStates } from "@/lib/liveStates";

// The live-count state count below is read from the backend
// (fetchLiveStates), every five minutes (LIVE_STATES_REVALIDATE_S). A
// literal: Next reads it statically.
export const revalidate = 300;

export const metadata = pageMetadata({
  title: "Known Limitations of Civitas Scores",
  description:
    "What Civitas's scores and pages can't tell you, stated plainly (campaign size and funding windows, partisan lean as a stand-in for opinion, ballot coverage, news sources), with the reason each gap is still open.",
  path: "/about/limitations",
});

/**
 * Each entry is an open problem, not a settled trade-off. When one is fixed
 * the entry is removed, not left as history — a list that keeps disclaiming
 * what the site now does stops describing the site (AGENTS.md §7).
 */
function Limitation({ title, children }: { title: string; children: ReactNode }) {
  return (
    <article className="border-l-3 border-signal-amber/70 pl-4 space-y-3">
      <h3 className="font-display text-lg font-semibold leading-snug text-ink-hi">{title}</h3>
      {children}
    </article>
  );
}

export default async function LimitationsChapter() {
  const live = await fetchLiveStates();
  const liveCount = live && live.length > 0 ? countWord(live.length) : null;
  return (
    <AboutPage
      href="/about/limitations"
      eyebrow="Methodology · disclosures"
      title="Known limitations"
      lede={
        <p>
          What the scores and pages can&apos;t tell you. Where a gap can be fixed, we fix it and
          remove it from this list; where it can&apos;t yet, we say exactly why, so it can be
          revisited.
        </p>
      }
    >
      <Summary>
        <Point>
          Funding covers six years for a senator but two for a representative, so the two are best
          read side by side, not ranked.
        </Point>
        <Point>
          &ldquo;What a seat expects&rdquo; comes from how it votes for president: a broad stand-in
          for local opinion, not a measure of it.
        </Point>
        <Point>
          Ballot pages show local contests only for a few hand-picked towns; anywhere else it would
          take your address, which we won&apos;t ask for. The news sources lean center to left.
        </Point>
      </Summary>

      <Section id="money" title="Money">
        <Limitation title="Small-donor money is undercounted">
          <P>
            The small-donor share is the money the FEC reports as unitemized: gifts from donors who
            gave $200 or less in the cycle. Conduits such as ActBlue and WinRed itemize every gift
            they pass on, whatever its size, so small gifts made through them count as itemized and
            are left out. A campaign that raises small money that way shows a smaller small-donor
            share than it has, and one that itemizes every gift reports no unitemized money at all:
            its share is marked not measured and left out of its score rather than counted as 0%.
            The FEC&apos;s contributions-by-size tables would count them, but they total more than
            the campaigns&apos; own reported individual contributions (in one Senate race checked,
            $72.7 million against $62.0 million) and are empty for some candidates, so they are not
            used.
          </P>
        </Limitation>
        <Limitation title="Funding windows differ by chamber">
          <P>
            Funding covers a member&apos;s most recent completed election: six years of fundraising
            for a senator, two for a representative. The PAC share and top-donor concentration are
            each scored against the member&apos;s own chamber, but industry concentration is not,
            and no adjustment makes six years and two years the same span. Within a chamber members
            are measured over the same length of time, except a member with no completed election
            yet (appointed, or seated by a special election), who is measured on the campaign still
            in progress; across chambers the scores are best read side by side, not ranked, which is
            why the compare page names no winner when a senator and a representative are compared.
            Where FEC&apos;s candidate totals leave out the race that won a House seat, that
            election is read from the campaign committee&apos;s own totals for the cycle. The window
            itself is deliberate: a strict two-year window would leave most senators with little or
            no fundraising to measure. FEC filings also lag donations by weeks or months.
          </P>
        </Limitation>
      </Section>

      <Section id="voting" title="Voting">
        <Limitation title="Partisan lean stands in for opinion on the issue">
          <P>
            What a seat &ldquo;expects&rdquo; is measured from seats that vote like it for
            president, not from opinion on the issue in a given vote. A state&apos;s presidential
            lean says little about, say, public-land use in Utah or water rights in Arizona.
          </P>
          <More label="Why we haven’t replaced it">
            <P>
              The best free alternative found, survey-based ideology estimates by state and district
              (Tausanovitch and Warshaw), is still a single left–right score, the same kind of
              stand-in, and several years stale. Real issue-by-issue opinion at this scale would
              mean building multilevel regression and poststratification over raw survey data
              in-house: a statistics pipeline, and a black box next to every other formula on these
              pages. We chose not to trade auditability for a partial fix. Issue-level opinion data
              remains the named next step for the score itself. For the position part of the score,
              voters&apos; own left-right self-placement by state was tested as the expectation and
              predicted senators&apos; positions no better than partisan lean.
            </P>
          </More>
        </Limitation>
        <Limitation title="The seat, not the member’s own voters">
          <P>
            Constituent Alignment compares a member with same-party members of similarly-leaning
            seats
            <Cite id="canes2002" />, which avoids expecting anyone to sit at their seat&apos;s raw
            midpoint, where no member of either party sits
            <Cite id="bafumi2010" />. But it is one-dimensional, and members answer to their primary
            voters and supporters as well as to the whole seat
            <Cite id="fenno1978" />
            <Cite id="clinton2006" />. How the member&apos;s own constituents rate them, split by
            party, is now shown beside the score on each profile (see the next item).
          </P>
        </Limitation>
        <Limitation title="Heavy breaking reads the same whether it builds a coalition or burns one">
          <P>
            The score peaks at the seat&apos;s norm, the pattern a member&apos;s own party&apos;s
            primary voters reward. The wider electorate leans the other way: in Senate general
            elections from 1990 to 2024, members who broke more than their seat&apos;s norm did
            somewhat better, and some heavy breakers keep winning by appealing to both sides. A
            roll-call record can&apos;t tell them apart from members whose breaks cost them their
            base. That takes approval split by party, so each profile now shows it beside the score:
            approval of the member among the Democrats, Republicans and independents they represent,
            from the Cooperative Election Study. It is shown, not scored, until a second survey wave
            shows it is stable.
          </P>
          <More label="How sharp the approval figures can be">
            <P>
              The 2024 survey asked about 60,000 respondents in October and November 2024, and a
              profile shows a figure only for a member still in the seat the survey asked about: one
              who has since moved to another seat or district shows none. Small groups are pulled
              toward what a typical member of the same party gets from that group, by an amount
              estimated from how much members actually differ. The survey&apos;s size sets how sharp
              the House figures can be: a district has about a hundred respondents, so most House
              figures come mostly from what similar members get. Only Democrats&apos; ratings rest
              mostly on the district&apos;s own respondents, for about three in five Democratic
              members and two in five Republican ones, and the profile marks every figure that
              doesn&apos;t. Senators&apos; figures mostly rest on their own state&apos;s respondents
              (a median of 672).
            </P>
          </More>
        </Limitation>
        <Limitation title="Some bills are labelled by content">
          <P>
            Partisan depth (the lean bar and its policy-area breakdown) labels each bill a member
            voted on, housekeeping motions aside, by comparing it with each party&apos;s positions
            in that area, and a sponsored bill with no tracked roll call, or only a housekeeping one
            (a motion to table or recommit, say), gets its party badge the same way. Bipartisan or
            cross-cutting bills can be misread. It never decides whether a member broke with their
            party.
          </P>
        </Limitation>
      </Section>

      <Section id="presidents" title="Presidents">
        <Limitation title="Historians’ judgment carries its biases in">
          <P>
            Historical Legacy (50%) comes from C-SPAN&apos;s historians survey. Historians as a
            field tend to favor presidents who expanded federal power, and hold off rating recent
            presidents, so this ranking inherits those tendencies at roughly that weight.
          </P>
        </Limitation>
        <Limitation title="The economy isn’t only the president’s doing">
          <P>
            Effectiveness compares postwar growth with 13 peer economies, which takes out shocks the
            whole rich world shared, and sets aside the peers&apos; catching up with US incomes. It
            can&apos;t take out what hit the US alone without a president causing it. Jobs,
            unemployment and inflation aren&apos;t compared with peers: jobs are counted in
            millions, which has no counterpart abroad, and compared with peers, unemployment
            can&apos;t be measured consistently before the 1980s and inflation took on a strong
            trend with era. Unemployment and inflation are instead judged against where the term
            started. Presidents before 1947 are compared on growth and, from 1939, jobs.
          </P>
        </Limitation>
      </Section>

      <Section id="ballot-coverage" title="Ballots">
        <Limitation title="A state ballot page is the statewide slice">
          <P>
            Ballots are printed per precinct, so there&apos;s no single &ldquo;ballot&rdquo; for a
            whole state. Pages show federal contests, statewide measures and (where a state&apos;s
            own results or candidate list name them) statewide offices, state legislative seats and
            judgeships. County and city offices and local measures are shown only for a small,
            hand-picked list of towns (looked up from each town hall&apos;s address); anywhere else,
            showing them would mean taking a home address to a lookup service, and we won&apos;t do
            that. Every page lists what it omits for that state and links to the election office.
            See <A href="/about/elections">Elections &amp; ballots</A>.
          </P>
        </Limitation>
        <Limitation
          title={
            liveCount
              ? `Live results cover ${liveCount} ${live!.length === 1 ? "state" : "states"}`
              : "Live results cover only some states"
          }
        >
          <P>
            On election night the count is read from each state&apos;s own results site, and only{" "}
            {liveCount
              ? `${liveCount} ${live!.length === 1 ? "publishes" : "publish"}`
              : "some states publish"}{" "}
            one in a form we can read reliably. Other states&apos; pages say they have no live count
            and link to the office that publishes it; they are left unshaded on the results map
            rather than drawn as having no votes. A count is never called: a race
            &ldquo;leads&rdquo; even once the state lists its count as official; Civitas calls no
            race. See <A href="/about/elections#election-night">Elections &amp; ballots</A>.
          </P>
        </Limitation>
        <Limitation title="Ballot-measure coverage is still filling in">
          <P>
            Measures are read only from the states themselves, each through a reader built for that
            state&apos;s own publication, with no third-party source behind them. A few states
            publish no official list of what they have certified, and the rest aren&apos;t read
            automatically yet. Those pages say the state isn&apos;t covered and which of the two
            reasons applies, as do pages where an update failed, rather than showing an empty
            section, which would read as &ldquo;no measures&rdquo;, a different and potentially
            damaging claim. There, the official link on every page is the complete answer.
          </P>
        </Limitation>
      </Section>

      <Section id="news-and-data" title="News and classification">
        <Limitation title="The news sources lean center to left">
          <P>
            The seven newsrooms span center to lean-left under common media-bias ratings, with no
            right-of-center outlet at present. That shapes which stories surface, even though every
            sentence shown is quoted.
          </P>
        </Limitation>
        <Limitation title="Quoting means some stories publish nothing">
          <P>
            The language model is small (1.2 billion parameters). If it can&apos;t locate a claim
            that passes the word-for-word checks, the story isn&apos;t published, so some days carry
            fewer issues than the news would justify.
          </P>
        </Limitation>
        <Limitation title="Classification lacks world knowledge">
          <P>
            Embedding-based classification is fast and consistent, but it knows only what the text
            says. Shell companies and deliberately obscure names can land in the wrong industry. For
            PACs it is the last resort: a union&apos;s PAC, and a company&apos;s PAC the SEC lists,
            take their industry from those records, and a PAC with no sponsoring organization counts
            as political money. That last rule is right for about seven in ten of them; the rest are
            law and accounting partnerships&apos; and physician groups&apos; PACs, whose money then
            counts toward no industry. Trade associations and private companies have no such record,
            and their names are still read; measured against the records, the name reading agreed
            with them on about six PACs in ten.
          </P>
        </Limitation>
      </Section>

      <P>
        Several items that used to be on this list were fixed outright; the{" "}
        <A href="/changelog">scoring changelog</A> has the history.
      </P>
    </AboutPage>
  );
}
