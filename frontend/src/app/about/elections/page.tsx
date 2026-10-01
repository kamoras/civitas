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
import { ACTION_CENTER_HREF } from "@/lib/routes";
import { countWord, fetchLiveStates, stateNameList } from "@/lib/liveStates";

// The live-count state list below is read from the backend (fetchLiveStates),
// every five minutes (LIVE_STATES_REVALIDATE_S). A literal: Next reads it
// statically.
export const revalidate = 300;

export const metadata = pageMetadata({
  title: "How State Ballot Pages Work",
  description:
    "What Civitas's state ballot pages show, what they leave out and why, how candidate lists are confirmed, why ballot measures are quoted word for word with their drafter named, and how the live count is read on election night.",
  path: "/about/elections",
});

export default async function ElectionsChapter() {
  const live = await fetchLiveStates();
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
        <Point>
          From election day the pages lead with the count, read from each state&apos;s own election
          office. A candidate &ldquo;leads&rdquo; even once the state lists its count as official;
          Civitas calls no race.
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
          confirmed it). Where a state&apos;s own results or candidate list name them, the page also
          covers statewide executive offices, state legislative seats and elected judgeships; where
          they aren&apos;t covered yet, it says so rather than showing an empty section that would
          read as &ldquo;no governor&apos;s race&rdquo;. A state whose governor isn&apos;t up this
          year says that too, with the calendar that decides it. A minor party is shown as the state
          printed it, and where the names come from primary results rather than the state&apos;s
          November list, the section says what primary results can&apos;t show: an unopposed nominee
          is often not itemised, and independents never are.
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
          isn&apos;t enough, a map of the districts lets you click yours. It draws the lines each
          state votes on this year: the Census Bureau&apos;s 119th-Congress boundaries, and for the
          nine states that redrew for 2026 their new lines, built from Census blocks. Districts are
          shaded by partisan lean where the district has one of its own; the redrawn states&apos;
          new districts are left unshaded, because no per-district lean is published here for them
          yet. A text filter over county names, candidates&apos; names and district numbers works
          too. All of it runs on data already on the page: nothing is typed into a lookup, sent or
          stored.
        </P>
        <P>
          In a state voting on new lines, a lookup by representative — house.gov&apos;s, or your
          current member&apos;s name — answers for the district you were in at the last election,
          which on the new map can be a different place under the same number. Those pages point to
          the map, the counties and the state&apos;s own ballot lookup instead. For the same reason
          a member of Congress running there is marked a &ldquo;sitting member&rdquo;, not the
          &ldquo;incumbent&rdquo;: the district they hold today is on the old map, and no seat on
          the new one has a previous holder.
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
            for those races. Michigan, Ohio and Oklahoma are read from lists the states publish
            themselves (Oklahoma&apos;s results API requires a login, so its State Election
            Board&apos;s published list of November ballots is read instead). Nevada and New York
            answer every request with a bot challenge, so Google&apos;s election index is their only
            source until that changes.
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

      <Section id="election-night" title="Election night: the count">
        <P>
          From election day, <span className="font-mono text-ink-hi">/elections</span> and each
          state page put results first. The national map is shaded by who is leading each race
          instead of by how the state usually leans — fainter while fewer than half its reporting
          areas (usually precincts) are in, solid once the state lists its count as official. Where
          a state has more than one race on the map — its House seats, or both Senate seats — it
          takes the colour of the party leading the most of them, grey when two lead equally many,
          and stays fainter until every one has half in; it turns solid only when every count is
          official. A state still voting, one with no votes yet and one whose feed couldn&apos;t be
          read are each marked with a pattern as well as a colour, and each state&apos;s name, read
          aloud, says where its count stands. Each state page says when its count was last read, and
          shows its Senate race or races, every House district and a district map shaded the same
          way, above the ballot research, and a live-updates feed tells each change as it happens:
          first returns, a new leader, every reporting area in, a count the state lists as official,
          a seat changing party. A district or Senate race the state&apos;s results feed gives no
          count for while it counts the state&apos;s other races — a contest it doesn&apos;t list or
          that couldn&apos;t be matched, an uncontested seat — is listed and marked as exactly that,
          not as &ldquo;no votes yet&rdquo;; so is a whole chamber on the national map, hatched and
          named &ldquo;no count from the state&apos;s feed&rdquo;. While a state&apos;s polls are
          still open, its page stays a ballot-research page and the national map marks its polls
          &ldquo;not yet closed&rdquo;: nothing is said about a count until its last polls close.
          From election day the elections pages show no partisan lean, on a map or beside a
          district, even where there is no count to show: next to a live count, a lean reads as a
          prediction of it.
        </P>
        <Sub title="Where the numbers come from">
          <P>
            Every five minutes while counts are moving (hourly once none has moved for a day), from
            the state&apos;s own election-night results site — the same systems Civitas already
            reads for confirmed candidates.{" "}
            {live && live.length > 0 ? (
              <>
                {capitalize(countWord(live.length))}{" "}
                {live.length === 1 ? "state publishes" : "states publish"} a count we can read this
                way: {stateNameList(live)}.
              </>
            ) : (
              // The list couldn't be read: name no number rather than a
              // stale one. The results map shows which states are covered.
              <>
                Only some states publish a count we can read this way; the results map marks which.
              </>
            )}{" "}
            Every other state&apos;s page says it has no live count here and links to the office
            that publishes one; it is never drawn as a state where nothing has happened.
          </P>
        </Sub>
        <Sub title="What we won’t show">
          <P>
            A wrong number on election night is worse than none. Nothing from a state is read until
            its last polls close. A feed marked as test or practice data, one answering for the
            wrong election, or one older than what we already show is refused, and the page keeps
            the last count it trusted — and says so, with the time, if a state&apos;s feed
            couldn&apos;t be read at all rather than implying counting hasn&apos;t started. If the
            page itself can&apos;t refresh, it says that too, with when the counts still on screen
            were read; no state is marked live and every count on the map is striped, so an old
            count never passes for a live one &mdash; and that is all it says then: it doesn&apos;t
            blame a state&apos;s feed for its own failure to ask. If Civitas itself stops reading a
            state&apos;s feed &mdash; no check in well over a pass, 15 minutes while counts move or
            70 once they are read hourly, or no record of one at all since the state&apos;s polls
            closed &mdash; the state is marked <strong>stale</strong>: its row says when its feed
            was last checked and when the count shown was read, and the map keeps the last
            leader&apos;s colour under amber stripes. That is judged by the server&apos;s clock,
            from the time on the page&apos;s latest answer, not by your device&apos;s, which may be
            off; while the page can&apos;t refresh, that clock stops where it stood, and it never
            runs backwards. A count that goes down (a county pulling a bad upload) is shown but
            announces nothing: a change of party already announced stays announced &mdash; on the
            map, in the live updates and in the Action Center alike &mdash; until the next count
            says otherwise. If that lower count shows the holder&apos;s party ahead again, the
            results pages say both: that the change was announced, and that the latest count shows
            the holder&apos;s party ahead.
          </P>
          <P>
            We never call a race. A candidate &ldquo;leads&rdquo; &mdash; &ldquo;not final&rdquo;
            while the state hasn&apos;t listed its count as official, and still &ldquo;leads&rdquo;
            once it has, never &ldquo;wins&rdquo;: an official count&apos;s leader can still face a
            runoff (Georgia requires a majority), a recount or a court. A seat is only described as
            changing party once half its reporting areas are in; where a state reports by county or
            town, each of which &ldquo;reports&rdquo; with its first batch of ballots, it takes
            every county or town in and six hours since the first votes, or the state&apos;s
            official count. Once said, it stands until the lead itself goes back to the seat&apos;s
            party or ties: a count that dips below that bar with the same candidate ahead is not a
            reversal. A House seat in a state whose congressional map was redrawn for this election
            has no previous holder to compare against — the district with the same number is a
            different district — so it is never described as changing party.
          </P>
        </Sub>
        <Sub title="Developing stories and posts">
          <P>
            A seat changing party opens a <em>developing</em> story in the{" "}
            <A href={ACTION_CENTER_HREF}>Action Center</A>, marked as not yet confirmed by the
            press. Until a news story naming that race — the state&apos;s seat, and one of its
            candidates by full name (&ldquo;Wayne Johnson&rdquo;, never just &ldquo;Johnson&rdquo;)
            — confirms it, it follows the count, and if the lead reverts it comes off the Action
            Center and is rewritten to say the count no longer shows a change of party; once
            confirmed, it is the news story. Civitas&apos;s Bluesky account posts fewer moments than
            the feed shows: a seat changing party, a count the state lists as official (a Senate
            race, or a seat changing party), and the big moves in a Senate race — a new leader with
            most of the count in, every reporting area in. A few an hour at most; one that
            can&apos;t go out within two hours, or that a newer post about the same race overtakes,
            is dropped rather than posted late, and a posted change of party that reverts gets a
            correction. Every sentence in the feed, the story and the posts is a fixed template
            around the state&apos;s own figures — no AI writes any of it.
          </P>
        </Sub>
        <P>
          The results stay up for two weeks after the last count changes, never past January 3, when
          the new Congress is sworn in. Then the pages turn to the next election.
        </P>
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
            about a state with seventeen amendments pending. So a state is shown as having no
            statewide measures only when its official source establishes it, and a state we
            haven&apos;t covered, or whose ingest failed, says <em>that</em> and points to the
            official lookup. Measures removed from the ballot — courts have struck about 2.3% since
            1995 — are marked removed and kept for a while, not silently deleted.
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
          Every measure is read directly from the state itself — its Secretary of State, elections
          board or legislature — through its certified list, voter guide or ballot notice. We use no
          third-party source for measures. A state we don&apos;t read automatically yet, or which
          publishes no official list, says it isn&apos;t covered and which of the two it is; a guide
          not yet published reads the same way, never as &ldquo;none&rdquo;. The state&apos;s
          official source is linked from every measure. See{" "}
          <A href="/about/limitations#ballot-coverage">known limitations</A> for where coverage is
          still incomplete.
        </P>
      </Section>
    </AboutPage>
  );
}

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}
