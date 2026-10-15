import Link from "next/link";
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
import {
  countWord,
  fetchLiveStates,
  fetchRankedChoiceStates,
  stateNameList,
} from "@/lib/liveStates";

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
  const [live, rankedChoice] = await Promise.all([fetchLiveStates(), fetchRankedChoiceStates()]);
  return (
    <AboutPage
      href="/about/elections"
      eyebrow="Methodology · elections"
      title="Elections & ballots"
      lede={
        <p>
          Each state has a ballot page built for researching what&apos;s on the ballot: who is
          running, their money and record, and every statewide measure in the state&apos;s own
          words.
        </p>
      }
    >
      <Summary>
        <Point>
          A ballot page shows what&apos;s the same across a whole state, plus your district once you
          pick it on a map, by county, or by city or town. It never asks for your address.
        </Point>
        <Point>
          Every page says what it leaves out, and links to your election office for the rest.
        </Point>
        <Point>
          Ballot measures are quoted word for word, with the person or body who wrote the words
          named. No AI writes or summarizes anything on these pages.
        </Point>
        <Point>
          Each page says what its candidate lists are (a certified ballot, primary results, or
          campaign filings), and when a primary has already passed.
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
          ballot in three columns (federal offices, state offices, then measures and local
          contests), and opens any contest&apos;s research beside it: money raised, a member of
          Congress&apos;s voting record, news coverage. On a phone it opens one contest per screen.
        </P>
        <P>
          Candidates appear under the name their state prints on its ballot. Each contest states the
          term it is for: two years for the House, six for the Senate, and for state offices, the
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

      <Section id="race-context" title="What shapes a race (and why there is no forecast)">
        <P>
          Each federal contest&apos;s research opens with what shapes the race. Civitas publishes no
          prediction, odds or rating for any race, and calls none; these figures describe a race,
          they don&apos;t forecast it.
        </P>
        <P>
          <strong>The seat.</strong> Its lean is the Cook-PVI-style figure: how much more Republican
          or Democratic it voted than the nation in the last two presidential elections. Beside it,
          one square for every seat of its kind, from the most Democratic-leaning to the most
          Republican: every U.S. House district on the lines this election is fought on, or the 50
          states for a Senate race. The count of seats leaning further each way is worked out on the
          server from the same table the lean comes from. A district that only has its state&apos;s
          figure is not placed among districts, and from election day the lean is not shown at all,
          so that it can&apos;t be read as a call on the count. A House district also shows the lean
          Civitas computes from precinct returns with the same formula, beside Cook&apos;s and used
          in no score (<A href="/about/data#computed-lean">how it is computed</A>).
        </P>
        <P>
          <strong>The money.</strong> Each candidate&apos;s contributions are split into the
          categories of the FEC&apos;s own totals, which add up to the total: people who gave the
          campaign $200 or less (the FEC doesn&apos;t list them by name), people who gave more, PACs
          and other committees, party committees, and the candidate. Loans a candidate makes to the
          campaign are shown on their own, because they are not contributions. Each candidate&apos;s
          figures carry the last day they cover and a link to that election&apos;s FEC page. Where a
          filing&apos;s categories don&apos;t add up to its total, no split is shown. No category is
          presented as better or worse than another.
        </P>
        <P>
          <strong>The data.</strong> The <span className="font-mono text-ink-hi">[ CSV ]</span> link
          on any race downloads every federal candidate in that state with each FEC figure, the
          dates they are as of, the seat&apos;s lean and the FEC link. The{" "}
          <span className="font-mono text-ink-hi">[ JSON ]</span> link is the race as the page reads
          it.
        </P>
      </Section>

      <Section id="deadlines" title="Voter deadlines">
        <P>
          Each state page starts with that state&apos;s deadlines for this election: when you can
          register online, by mail and in person (and on Election Day itself, where the state allows
          it), when early voting opens and closes, and when a mail ballot must be asked for and sent
          back. The next one shows a countdown. The homepage lists every state&apos;s next deadline,
          soonest first.
        </P>
        <P>
          Every date comes from the state&apos;s own election office or its election law, never from
          a third-party site, and each one links to its source. When a rule says &ldquo;15 days
          before the election,&rdquo; we show the rule and the date it works out to. Each source is
          checked again when the list is rebuilt, and a date we can&apos;t check isn&apos;t shown.
          When a state&apos;s site blocks automated checks, we use its law instead. If we can&apos;t
          read a state&apos;s deadlines from either, its page lists none, says so, and links the
          state&apos;s own voter lookup.
        </P>
        <P>
          Deadlines are counted on the state&apos;s own clock. A deadline that has passed stays on
          the page and says so, with the ways to register that are still open.
        </P>
      </Section>

      <Section id="not-your-ballot" title="Why it isn’t “your ballot”">
        <P>
          A ballot is defined by the exact set of contests a voter is eligible for. Precincts split
          by district lines carry several versions, one county can print dozens, and nationally
          there are tens of thousands. Showing someone their exact ballot would mean asking for
          their home address and sending it to a lookup service, which is exactly what Civitas is
          built not to do.
        </P>
        <P>
          So each page covers what&apos;s uniform statewide and lists what it omits (county and city
          offices, local measures, primary ballots and so on), with a link to your own election
          office. Once a gap closes for a state, it comes off that state&apos;s list: a disclaimer
          that outlives the gap stops describing the page. And the list names only gaps a state
          actually has: no judicial line where judges are appointed or elected at another election,
          no &ldquo;retention questions&rdquo; where voters never decide whether to keep a judge, no
          legislative line in a year the state elects no legislature.
        </P>
        <P>
          That link goes to the state&apos;s own voter lookup, where you can see your sample ballot
          or polling place on the state&apos;s site (or its polling-place page, where the lookup
          itself refuses automated checks). Each night Civitas checks that the link still opens that
          page; when it doesn&apos;t (the page is gone, or moved to the site&apos;s homepage), the
          page links USAGov&apos;s directory of election offices instead. Some states&apos; sites
          turn away every automated check, though they open for a person. Civitas never gets past
          that block, so it can&apos;t confirm those links are up: their pages still link the
          state&apos;s own lookup, say it couldn&apos;t be checked, and give USAGov&apos;s directory
          as the backup.
        </P>
      </Section>

      <Section id="finding-your-district" title="Finding your district without typing an address">
        <P>
          Pick your county and the page narrows to the district covering it. About 13% of counties
          span more than one district; those offer the two or three as a second tap. Where a county
          isn&apos;t enough, a map of the districts lets you click yours. It draws the lines the
          ballot&apos;s House seats are contested on: the Census Bureau&apos;s 119th-Congress
          boundaries, and for the nine states that redrew for 2026 their new lines, built from
          Census blocks. Districts are shaded by partisan lean, and in the redrawn states that is
          the lean of the new district on the ballot (Cook&apos;s 2026 figures for the new lines),
          not of the old seat with the same number. A district with no lean of its own on file is
          left unshaded rather than given its state&apos;s colour. A text filter works too: type
          your city, town or county, a candidate&apos;s name or a district number. Each district
          lists the cities, towns and communities in it, from the same Census block files as the
          state legislative lists below (a place with fewer than 50 residents inside a district is
          left off it), and a district that matches what you typed lists the matching place or
          county first, by its full name: a city can share its name with a county elsewhere in the
          state, and the label shows which one matched. Type a place&apos;s full name and the page
          says which districts it is in; a city split between districts says so, since there your
          street decides which one is yours. On the map, Tab reaches one district and the arrow keys
          move between them. All of it runs on data already on the page: nothing is typed into a
          lookup, sent or stored.
        </P>
        <P>
          In a state whose lines were redrawn after its members were elected (those going into the
          election, or those sitting now), a lookup by representative (house.gov&apos;s, or your
          current member&apos;s name) answers for the district your current member was elected in,
          which on the new map can be a different place under the same number. Those pages point to
          the map, the counties and the state&apos;s own voter lookup instead. For the same reason a
          member of Congress running there is marked a &ldquo;sitting member&rdquo; (from election
          day on, a &ldquo;member before this election&rdquo;, since the results can stay up until
          the new Congress is sworn in), not the &ldquo;incumbent&rdquo;: no seat on the new map has
          a previous holder.
        </P>
        <P>
          For each redrawn state, a{" "}
          <Link
            href="/elections/redistricting"
            className="underline decoration-white/30 underline-offset-4 hover:text-phos"
          >
            what-changed page
          </Link>{" "}
          answers whether your district is different and by how much: for each new district, the
          share of its residents who were in each old one and how its lean moved, and for each old
          district, where its residents went. Find yours by city, town or county. The counts are
          2020 Census residents, joined block by block to the old and new district files, so
          everyone is counted once; both leans are Cook&apos;s on the same presidential elections,
          so a change in lean comes from the lines alone. The page leads with how many seats lean
          each way before and after; how many districts lean differently from the old district most
          of their residents came from is a separate count, shown with the method. Each page links
          the act or court order behind the map, explains the method and offers the whole table as
          CSV and JSON. It describes who is in which district; it says nothing about why a line was
          drawn.
        </P>
        <P>
          To find who represents you today, use the Politicians directory instead: pick your state,
          then type your city, town or county. It uses the districts as of the current Congress, the
          lines today&apos;s members were elected on (the Census Bureau&apos;s 119th-Congress file),
          not the 2026 lines. In the nine redrawn states the two give different answers, and each
          page says which map it shows. When the new Congress is sworn in on January 3, the
          directory moves to the 2026 lines on its own.
        </P>
        <P>
          State legislative districts are far smaller than a county, so each one lists the places it
          covers: the towns themselves in the states where towns are the local government (New
          England, New York, New Jersey, Pennsylvania, Michigan, Minnesota, Wisconsin), every city
          and every unincorporated community Census names (Silver Spring, The Woodlands), and every
          county it touches. Type any of them, or a candidate&apos;s name or the district number,
          and the list narrows. The places come from the Census Bureau&apos;s 2026 state legislative
          district files joined block by block to the 2020 census, so a place is listed for a
          district only where people in it actually live inside that district; a sliver with fewer
          than 50 residents (about one census block) is left off. A city split between districts is
          listed under each, and your street decides which: the state&apos;s own voter lookup gives
          the exact answer.
        </P>
        <Sub title="The optional town selector">
          <P>
            For a small, hand-picked list of towns, a selector shows local races: city council,
            school board, local measures. It never asks for or sends your address: it looks up a
            fixed public address we chose for that town, its town hall, so everyone who picks the
            town gets the identical lookup and nothing about you leaves the server.
          </P>
          <P>
            It is an approximation, and says so beside the selector: a town can contain several
            precincts, so a race tied to your street may be missing or one from across town may
            appear. Its source only carries an election close to the date, so months ahead a town
            may show &ldquo;could not load local races right now&rdquo;: that is the likely reason,
            not a fault.
          </P>
        </Sub>
      </Section>

      <Section id="candidates" title="Whose names are on the list">
        <P>Four different things can fill a candidate list, and each page says which one it has:</P>
        <List>
          <Item label="A certified ballot">
            The state&apos;s own list of who is on the November ballot: third-party and independent
            candidates included. When a state&apos;s source is its certified ballot, it is the final
            word: anyone not on it comes off the page.
          </Item>
          <Item label="Nominees from primary results">
            Stands in until a state certifies. It can&apos;t see a nominee who never ran in a
            primary (one a party convention chose, or a Libertarian, Green or independent
            candidate), so the page says &ldquo;nominees&rdquo;, not &ldquo;the ballot&rdquo;.
          </Item>
          <Item label="A primary ballot">Before the primary, the people running in it.</Item>
          <Item label="Campaign filings">
            When nothing is confirmed yet, everyone who filed with the FEC: some of whom will never
            be on any ballot. Before a primary that is the best available answer; after one, the
            page says plainly that the ballot has been decided and we don&apos;t have it yet.
          </Item>
        </List>
        <P>
          Everyone on a certified ballot is shown, including candidates who never filed with the FEC
          (&ldquo;no FEC filing&rdquo; appears in place of fundraising figures). Declared write-ins
          aren&apos;t printed on the ballot and are never shown as if they were. A Senate race
          appears only where the FEC&apos;s election calendar lists one. We don&apos;t read
          candidate lists published only as scanned images (one misread name would take a real
          nominee off the page), and we don&apos;t work around bot challenges or logins on state
          election sites.
        </P>
        <P>
          Fundraising figures are the FEC&apos;s totals for this race&apos;s election only, dated by
          the last day the candidate&apos;s reports cover (&ldquo;FEC reports through&rdquo;), not
          by when we last checked. A candidate with no report for this election yet says so; their
          money from an earlier race is never shown in its place.
        </P>
        <More label="Three lists corrected, October 2026">
          <P>
            An audit on 8 October 2026 found three pages built from primary results that missed
            people a primary can&apos;t show. Idaho&apos;s Senate race still listed a Democratic
            nominee who had withdrawn, and left out two independents on the ballot by petition.
            Montana&apos;s Senate race, marked a complete ballot, left out an independent whose
            petition the state certified: it was read from the primary&apos;s candidate list, which
            never moves a petition candidate to &ldquo;nominated&rdquo;. Arkansas&apos;s statewide
            races showed only the Democrat for Governor and none of the other offices, because their
            nominees were unopposed in the primary.
          </P>
          <P>
            Each is now read from the state&apos;s own list for the general election: Montana&apos;s
            general-election candidate list, Idaho&apos;s final candidates list and Arkansas&apos;s
            candidate search, which the state trims to the November field after the primary.
            Arkansas&apos;s search has no status column, so a withdrawal shows only once the state
            removes the name. Where one of these lists can&apos;t be read, the page goes back to
            saying its names come from primary results and may be incomplete.
          </P>
          <P>
            The same audit found every House race in Nevada and New York still listing everyone who
            filed with the FEC. Both states&apos; election sites answer with a bot challenge. Clark
            County, Nevada publishes its own list of November contests, which now settles three of
            the state&apos;s four House races. New York City&apos;s Board of Elections posts a
            contest list too, but it is marked tentative, predates the state&apos;s certification
            and covers only the city&apos;s districts, so it isn&apos;t presented as the ballot.
          </P>
        </More>
        <More label="Checked against twelve certified lists, 9 October 2026">
          <P>
            A second audit compared the pages for twelve states, across most of the systems Civitas
            reads, with each state&apos;s own certified list. Federal contests matched in all of
            them apart from how some names were written. What it found, and what changed:
          </P>
          <List>
            <Item label="North Carolina’s legislature and courts">
              Read from primary results, which in North Carolina list only contested primaries: the
              page showed 59 legislative nominees in 55 of 170 districts (three since replaced by
              their party), and nothing for the Supreme Court seat. Both now come from the State
              Board of Elections&apos; candidate list for November.
            </Item>
            <Item label="Names">
              Candidates who never filed with the FEC (60 of them in 17 states) were shown as
              &ldquo;SURNAME, GIVEN&rdquo; rather than as printed, and a nickname the ballot prints
              inside a name (&ldquo;Tobias (Toby)&rdquo;) was dropped. Both now read as the state
              prints them.
            </Item>
            <Item label="Incumbents">
              Nine sitting members running again were not marked as incumbents, because someone else
              in the race carried the FEC&apos;s open-seat code. A member the state&apos;s ballot
              lists is now trusted as running. A member of Congress running for the other chamber (a
              House member running for the Senate) was listed with no link to their record; the race
              now links it, named as what they are now (&ldquo;House member, GA-6&rdquo;), never as
              the seat&apos;s incumbent. A member&apos;s own profile now links to the race they are
              on the ballot in.
            </Item>
            <Item label="Parties">
              Where a state prints a party Civitas has no code for (California&apos;s Peace and
              Freedom, &ldquo;No Party Preference&rdquo;), it is now shown as printed rather than as
              no party. A party named &ldquo;American Independent&rdquo; or &ldquo;Independent
              American&rdquo; is a party, not an independent.
            </Item>
            <Item label="Special elections and runoffs">
              A legislative seat with a special election for the rest of its term beside its regular
              contest is shown as two contests. Louisiana&apos;s House contests on 3 November are an
              all-party primary with a 12 December runoff, and Georgia requires a majority in
              November with a 1 December runoff; both pages now say so above the ballot. So do
              California and Washington, whose top-two primaries can send two candidates of one
              party to November, and Alaska, whose top-four primary sends four. Maine and Alaska
              rank candidates in their federal contests, and their pages now say so before election
              day, not only on election night.
            </Item>
            <Item label="Finding your district">
              The filter ignores accents and punctuation, so &ldquo;dona ana&rdquo;, &ldquo;st
              louis&rdquo; and &ldquo;prince georges&rdquo; find their places.
            </Item>
          </List>
          <P>
            Ohio&apos;s and Utah&apos;s legislative nominees still come from primary results: Ohio
            publishes no statewide list of November candidates (its counties certify their own), and
            Utah&apos;s is a scanned image. New York&apos;s and northern Nevada&apos;s House races
            still list FEC filers, for the reason above.
          </P>
        </More>
        <More label="How the candidate lists were checked, September 2026">
          <P>
            On 26 September 2026, 39 states had certified candidates and eleven were still showing
            FEC filers months after their primary: Ohio by 144 days, New York by 95, with one New
            York race listing 25 filers for a ballot holding about two. Those pages said nominees
            &ldquo;aren&apos;t confirmed yet&rdquo;, telling readers a settled contest was open.
            Pages now lead with what their lists are.
          </P>
          <P>
            Re-probing those eleven found three causes. Nine relied on a national source that
            doesn&apos;t publish general-election candidates until close to the election. New
            Hampshire wasn&apos;t broken: its results are held for 21 days and were 18 days old.
            South Dakota was the one real fault (its election-night site had moved on to a July
            runoff), and now reads the Secretary of State&apos;s list of who is on the November
            ballot, which also shows an independent candidate primary results never could. A second
            look found Louisiana, South Carolina and Missouri publish their November ballots
            directly, and they are now read as such.
          </P>
          <P>
            Reading the ballot rather than primary results also catches a nominee replaced after the
            primary: in Maine, the winner of the Democratic Senate primary withdrew in July and the
            party nominated a replacement; in South Carolina a special primary replaced the June
            Senate winner. Colorado, Virginia, Tennessee, Florida, New Jersey, Maryland, Iowa,
            Nebraska, New Mexico, Wyoming, Hawaii, Delaware, Kentucky, Alaska, Montana, Illinois,
            North Dakota, Idaho and Arkansas are read from their certified lists (Tennessee&apos;s
            federal races alone list 36 independents), and Wisconsin from its official primary
            canvass.
          </P>
          <P>
            Utah and Alabama publish certified lists only as scanned images. For races those states
            and Connecticut leave unseen (districts whose primary was uncontested), Google&apos;s
            election index fills in once it publishes the general election, and only for those
            races. Michigan, Ohio and Oklahoma are read from lists the states publish themselves
            (Oklahoma&apos;s results API requires a login, so its State Election Board&apos;s
            published list of November ballots is read instead). Nevada and New York answer every
            request with a bot challenge. Three of Nevada&apos;s four House races are read from
            Clark County&apos;s own list of November contests; for the rest, and for New York,
            Google&apos;s election index is the only source until that changes.
          </P>
          <P>
            Where a state&apos;s results are reached through a link on its own site, each linked
            election is checked for its own name and date first: West Virginia moved its primary
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
        {/* The summary is the part most readers need; it stays at a grade-8
            reading level. Every rule is spelled out in the parts below it. */}
        <Sub title="In short" id="election-night-summary">
          <List>
            <Item>
              Once a state&apos;s polls close, its page shows the vote count from the state&apos;s
              own election office.
            </Item>
            <Item>
              While results are up, the homepage opens with each state&apos;s count, one tap from
              its page.
            </Item>
            <Item>
              The count is checked every five minutes while it is moving. Some states don&apos;t
              publish a count we can read. Their pages say so and link to the office that does.
            </Item>
            <Item>
              The person ahead &ldquo;leads&rdquo;. Until the state says its count is official, the
              lead is &ldquo;not final&rdquo;.
            </Item>
            <Item>
              Civitas never says who won. A recount, a runoff or a court can still change the
              result.
            </Item>
            <Item>
              If a number can&apos;t be trusted, the page shows no number and tells you why.
            </Item>
            <Item>
              Results stay up for two weeks after the count stops changing, and never past January
              3.
            </Item>
          </List>
        </Sub>
        <Sub title="What the pages show" id="election-night-pages">
          <List>
            <Item label="Results first">
              From election day, <span className="font-mono text-ink-hi">/elections</span> and each
              state page put results first.
            </Item>
            <Item label="The national map">
              Each state is shaded by who is leading, not by how it usually votes. The colour is
              fainter while fewer than half of its reporting areas (usually precincts) are in. It
              turns solid once the state lists its count as official.
            </Item>
            <Item label="A state with several races">
              Where a state has more than one race on the map (its House seats, or both Senate
              seats), it takes the colour of the party leading the most of them. It is grey when two
              parties lead equally many. It stays fainter until every race has half its areas in,
              and turns solid only when every count is official.
            </Item>
            <Item label="Patterns as well as colours">
              A state still voting, a state with no votes yet, and a state whose feed couldn&apos;t
              be read each get a pattern as well as a colour. Each state&apos;s name, read aloud,
              says where its count stands.
            </Item>
            <Item label="Each state page">
              It says when its count was last read. Above the ballot research it shows the Senate
              race or races, every House district, and a district map shaded the same way. A
              live-updates feed tells each change as it happens: first returns, a new leader, every
              reporting area in, a count the state lists as official, a seat changing party.
            </Item>
            <Item label="A race with no count">
              Sometimes Civitas shows a count for some of a state&apos;s races but not one of them.
              The feed may not list that contest, or it couldn&apos;t be matched. Two Senate
              contests may not be told apart. A count may have been set aside as impossible, or the
              seat may be uncontested. That race is listed and marked as exactly that, never as
              &ldquo;no votes yet&rdquo;. The same goes for a whole chamber on the national map,
              which is hatched and named &ldquo;no count shown here&rdquo;. In a state electing both
              senators with a count shown for only one, the other race is marked that way on the
              state&apos;s map label and row.
            </Item>
            <Item label="Polls still open">
              While a state&apos;s polls are open, its page stays a ballot-research page, and the
              national map marks its polls &ldquo;not yet closed&rdquo;. Nothing is said about a
              count until its last polls close.
            </Item>
            <Item label="No partisan lean">
              From election day the elections pages show no partisan lean, on a map or beside a
              district, even where there is no count to show. Next to a live count, a lean reads as
              a prediction of it.
            </Item>
          </List>
        </Sub>
        <Sub title="Where the numbers come from" id="election-night-sources">
          <P>
            The count comes from the state&apos;s own election-night results site: the same systems
            Civitas already reads for confirmed candidates. It is read every five minutes while
            counts are moving, and hourly once none has moved for a day.{" "}
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
            that publishes one. It is never drawn as a state where nothing has happened.
            {rankedChoice && rankedChoice.length > 0 && (
              <>
                {" "}
                {stateNameList(rankedChoice)}{" "}
                {rankedChoice.length === 1 ? "decides its" : "decide their"} congressional races by
                ranked-choice voting: when no one has a majority of first choices the winner comes
                from rounds counted after election night, so a first-choice count could show the
                wrong candidate ahead, and{" "}
                {rankedChoice.length === 1
                  ? "it is not"
                  : rankedChoice.length === 2
                    ? "neither is"
                    : "none is"}{" "}
                read live.
              </>
            )}
          </P>
        </Sub>
        <Sub title="When we show no number" id="election-night-refused">
          <P>A wrong number on election night is worse than none.</P>
          <List>
            <Item label="Before polls close">
              Nothing from a state is read until its last polls close.
            </Item>
            <Item label="Feeds we refuse">
              A feed marked as test or practice data, one for the wrong election, or one older than
              what we already show is refused. The page keeps the last count it trusted. If a
              state&apos;s feed couldn&apos;t be read at all, the page says so, with the time,
              rather than implying counting hasn&apos;t started.
            </Item>
            <Item label="When this page can't refresh">
              The page says so, with when the counts still on screen were read. No state is marked
              live, and every count on the map is striped, so an old count never passes for a live
              one. That is all it says then: it doesn&apos;t blame a state&apos;s feed for its own
              failure to ask.
            </Item>
            <Item label="When Civitas stops reading a state">
              If a state&apos;s feed hasn&apos;t been checked in well over a pass (15 minutes while
              counts move, 70 once they are read hourly), or there is no record of a check at all
              since its polls closed, the state is marked <strong>stale</strong>. Its row says when
              its feed was last checked and when the count shown was read. The map keeps the last
              leader&apos;s colour under amber stripes.
            </Item>
            <Item label="Whose clock">
              All of this is judged by the server&apos;s clock, from the time on the page&apos;s
              latest answer, not by your device&apos;s, which may be off. While the page can&apos;t
              refresh, that clock stops where it stood. It never runs backwards.
            </Item>
            <Item label="A count that goes down">
              A county may pull a bad upload. The lower count is shown but announces nothing. A
              change of party already announced stays announced (on the map, in the live updates and
              in the Action Center alike) until the next count says otherwise. If the lower count
              shows the holder&apos;s party ahead again, the results pages say both: that the change
              was announced, and that the latest count shows the holder&apos;s party ahead.
            </Item>
          </List>
        </Sub>
        <Sub title="We never call a race" id="election-night-leads">
          <List>
            <Item label="Leads, not wins">
              A candidate &ldquo;leads&rdquo;. The lead is &ldquo;not final&rdquo; while the state
              hasn&apos;t listed its count as official, and the tag beside the leader says so itself
              (LEADING · NOT FINAL), not only the note under the candidates. It still
              &ldquo;leads&rdquo; once it has, and never &ldquo;wins&rdquo;: an official
              count&apos;s leader can still face a runoff (Georgia requires a majority), a recount
              or a court.
            </Item>
            <Item label="A seat changing party">
              A seat is only described as changing party once half its reporting areas are in. Where
              a state reports by county or town, it takes every county or town in and six hours
              since the first votes, or the state&apos;s official count.
            </Item>
            <Item label="What &ldquo;in&rdquo; means">
              Some states count a county as in with its first batch of ballots. Others count it only
              once its count is complete, which can take weeks. There the map shows who leads in the
              meantime without calling it a change of party. How much is in is said the state&apos;s
              way too: &ldquo;fully reported&rdquo; where it counts areas whose count is complete,
              &ldquo;results from&rdquo; where an area counts once it has reported anything.
            </Item>
            <Item label="Once said, it stands">
              A change of party stays until the lead itself goes back to the seat&apos;s party or
              ties. A count that dips below that bar with the same candidate ahead is not a
              reversal.
            </Item>
            <Item label="Seats with no previous holder">
              A House seat in a state whose congressional map was redrawn for this election has no
              previous holder to compare against: the district with the same number is a different
              district. So it is never described as changing party. Nor is a seat whose previous
              holder was still unknown when the new Congress took office at noon on January 3. From
              then the member list is changing over to the winners, so it no longer says who held
              the seat going in.
            </Item>
          </List>
        </Sub>
        <Sub title="Developing stories and posts" id="election-night-posts">
          <P>
            A seat changing party opens a <em>developing</em> story in the{" "}
            <A href={ACTION_CENTER_HREF}>Action Center</A>, marked as not yet confirmed by the
            press. It follows the count until a news story naming that race confirms it: the
            state&apos;s seat, and one of its candidates by full name, never the surname alone. If
            the lead reverts first, the story comes off the Action Center and is rewritten to say
            the count no longer shows a change of party. Once confirmed, it is the news story.
          </P>
          <P>
            Civitas&apos;s Bluesky account posts fewer moments than the feed shows: a seat changing
            party, a count the state lists as official (a Senate race, or a seat changing party),
            and the big moves in a Senate race (a new leader with most of the count in, every
            reporting area in). It posts a few an hour at most. A post that can&apos;t go out within
            two hours, or that a newer post about the same race overtakes, is dropped rather than
            posted late. A posted change of party that reverts gets a correction.
          </P>
          <P>
            Every sentence in the feed, the story and the posts is a fixed template around the
            state&apos;s own figures. No AI writes any of it.
          </P>
        </Sub>
        <Sub title="How long results stay up" id="election-night-ends">
          <P>
            The results stay up for two weeks after the last count changes, never past January 3,
            when the new Congress is sworn in. Then the pages turn to the next election.
          </P>
        </Sub>
      </Section>

      <Section id="measures" title="Ballot measures: quoted, never rewritten">
        <P>
          Each measure shows its official ballot title, official summary, fiscal impact statement,
          and the state&apos;s own description of what a YES and a NO vote do: all verbatim, linked
          to the source. Where a state publishes no yes/no description, none is shown. We never
          infer one: the intuitive reading (&ldquo;yes enacts it&rdquo;) is backwards on a veto
          referendum, where approving <em>keeps</em> the law being challenged.
        </P>
        <P>
          On the ballot page, each measure in the measures box (on a phone, each measure&apos;s
          button under the measures line) opens the full text at that measure, so finding the ninth
          of fourteen takes one click rather than scrolling past the other eight.
        </P>
        <Sub title="Who wrote the words you’re reading">
          <P>
            Ballot titles are among the most litigated documents in election law: courts have voided
            measures over a legislature&apos;s wording. So each quote names its author
            (&ldquo;Drafted by the Georgia General Assembly&rdquo;, &ldquo;Prepared by the
            Legislative Analyst&rsquo;s Office&rdquo;): who wrote it tells you how to weigh it.
            Where the summary or the yes/no description has a different author from the title, as in
            Alabama, where a Fair Ballot Commission writes them under the Legislature&apos;s
            question, each names its own. Where the law leaves it open who wrote a text, we name
            everyone it could be rather than guess one. Measures are listed in the order the state
            lists them. We don&apos;t reproduce the pro and con arguments in voter guides; campaigns
            write those, some states sell the slots, and printing them as a matched pair would
            manufacture a balance that may not exist.
          </P>
        </Sub>
        <Sub title="“No measures” is different from “we don’t know yet”">
          <P>
            An empty section reads as &ldquo;nothing to research&rdquo;: a damaging thing to imply
            about a state with seventeen amendments pending. So a state is shown as having no
            statewide measures only when its official source establishes it (or, as in Delaware, its
            constitution allows none: amendments there take no popular vote), and a state we
            haven&apos;t covered, or whose ingest failed, says <em>that</em> and points to the
            official lookup. Measures removed from the ballot (courts have struck about 2.3% since
            1995) are marked removed and kept for a while, not silently deleted.
          </P>
        </Sub>
        <Sub title="Why there’s no plain-language summary">
          <P>
            Ballot language is hard to read (statewide measures averaged a grade-21 reading level in
            2025), and a plain-language version would genuinely help. We left it out on purpose. Our
            checks on generated text confirm that its words appear in the source; they can&apos;t
            tell whether a sentence points the same direction. &ldquo;A YES vote repeals this
            tax&rdquo;, when the official text says approval <em>keeps</em> it, passes every check,
            because every word is in the source. On a page that can change how someone votes, an
            error we can&apos;t detect is one we won&apos;t ship.
          </P>
        </Sub>
        <P>
          Every measure is read directly from the state itself (its Secretary of State, elections
          board or legislature) through its certified list, voter guide or ballot notice. Where a
          state&apos;s whole site refuses automated requests (Arizona, Georgia and Nevada put theirs
          behind bot checks), we don&apos;t try to get past it: the state&apos;s own document is
          read from another public office&apos;s copy of it (a county election office, or for
          Arizona the state&apos;s Citizens Clean Elections Commission), with every check the
          state&apos;s own copy would get, and each measure names that office. We use no third-party
          source for measures. A state we don&apos;t read automatically yet, or which publishes no
          official list, says it isn&apos;t covered and which of the two it is; a guide not yet
          published reads the same way, never as &ldquo;none&rdquo;. The state&apos;s official
          source is linked from every measure. See{" "}
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
