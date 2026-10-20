import Link from "next/link";
import { pageMetadata } from "@/lib/site";
import {
  AboutPage,
  Summary,
  Point,
  Section,
  Sub,
  P,
  Steps,
  Step,
  More,
  Cite,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "How Senators and Representatives Are Compared",
  description:
    "How each member of Congress is set beside colleagues in the same party on small gifts, PAC money, breaks with the party and bills moved, and where each figure comes from.",
  path: "/about/scores",
});

export default function ScoresChapter() {
  return (
    <AboutPage
      href="/about/scores"
      eyebrow="Methodology · Senate & House"
      title="How Congress is compared"
      lede={
        <p>
          Every senator and representative is measured the same way, from FEC filings, roll-call
          votes and Congress.gov bill records, and set beside colleagues in the same party. This is
          how.
        </p>
      }
    >
      <Summary>
        {/* Each line reads at a Flesch-Kincaid grade of 8 or below. The
            detail is in each linked section. */}
        <Point>
          <A href="#peers">Where they stand</A>: each member page shows four figures beside the same
          figures for colleagues in the same party. A word says if the member is above, near or
          below them.
        </Point>
        <Point>
          <A href="#funding">Money</A>: how much came in small gifts of $200 or less, and how much
          from PACs.
        </Point>
        <Point>
          <A href="#alignment">Votes</A>: how often the member broke with the party, next to the
          same party in places like theirs.
        </Point>
        <Point>
          <A href="#effectiveness">Bills</A>: how many of the member&apos;s bills got past a
          committee, passed, or became law.
        </Point>
        <Point>
          The words come from a fixed rule, not a judgment. They say above or below, never better or
          worse.
        </Point>
        <Point>
          Figures cover the current Congress. Money covers the election that won the member their
          seat.
        </Point>
        <Point>There is no overall score and no rank.</Point>
      </Summary>

      <Section id="peers" title="How a member is compared with peers">
        <P>
          A member page leads with four figures: the share of the money given in gifts of $200 or
          less, the share given by PACs, how often the member broke with the party on party-line
          votes, and how many of the member&apos;s bills got past committee. Each is drawn as one
          dot among every peer&apos;s dot, with a tick at the figure the seat predicts (for small
          gifts, the share typical for a state that size; for breaks, the rate for the same party in
          seats that lean the same way) or, where no seat figure exists, the peers&apos; middle
          value.
        </P>
        <P>
          Peers are the other members of the same chamber who caucus with the same party (an
          independent counts with the party they caucus with). A switch on the page compares with
          the whole chamber instead.
        </P>
        <P>
          The word is set by a fixed rule, not a judgment: &ldquo;well above&rdquo; or &ldquo;well
          below peers&rdquo; outside the middle 80% of peers and more than one standard deviation
          from the expected figure; &ldquo;above&rdquo; or &ldquo;below peers&rdquo; outside the
          middle half; otherwise &ldquo;near peers&rdquo;. These cut points are conventions. The
          words say above or below, never better or worse; each figure says which way is higher. A
          figure with fewer than 20 peers or party-line votes behind it gets no word.
        </P>
        <P>
          No page adds the figures into one score or ranks members by them. The public API&apos;s{" "}
          <span className="font-mono text-[0.9em]">overall</span> field, left from an older score,
          is deprecated and will be removed (<A href="/developers">API reference</A>).
        </P>
        <Sub title="Why the current Congress, not a career">
          <P>
            Votes and bills cover the current two-year Congress, so a record from a decade ago
            doesn&apos;t stand in for the work now. That also avoids guessing term boundaries the
            official records don&apos;t publish. Money is the exception: senators raise little
            outside election years, so it covers the member&apos;s most recent completed election,
            the campaign that won the current seat, not a re-election campaign still under way. A
            profile&apos;s full vote list can be narrowed to the last 30 days or to this Congress.
          </P>
        </Sub>
      </Section>

      <Section id="funding" title="Money: small gifts and PAC money">
        <P>
          Both are shares of the contributions to the member&apos;s campaign for its most recent
          completed election, from FEC filings: six years of fundraising for a senator, two for a
          representative. A member with no completed election yet (appointed, or seated in a special
          election) is measured on the campaign under way.
        </P>
        <More label="The full method" context="Money">
          <Steps>
            <Step n={1} title="Small gifts">
              The money the FEC reports as unitemized: gifts from donors who gave $200 or less in
              the cycle. It leaves out small gifts made through conduits such as ActBlue and WinRed,
              which itemize every gift whatever its size, so it undercounts campaigns that raise
              that way. A campaign that itemizes every gift has no figure. The tick for a senator is
              the share typical for a state of that population, measured from the Senate on every
              run; a House member&apos;s tick is the peers&apos; median, since every district holds
              about the same number of people.
            </Step>
            <Step n={2} title="PAC money">
              The share that came from political action committees. PAC money tracks how members
              vote more closely than money from individuals does
              <Cite id="stratmann2005" />. The tick is the peers&apos; median. House candidates rely
              on PACs far more than Senate candidates, which is one reason peers come from the
              member&apos;s own chamber.
            </Step>
          </Steps>
          <P>
            Shares are of contributions (money given by individuals, PACs, party committees or the
            candidate), not of total receipts, which also count transfers from joint fundraising
            committees whose sources aren&apos;t broken out. Democrats and Republicans raise money
            differently on average (in July 2026 Senate data, Democrats took roughly half the PAC
            share and twice the small-donor share), so the figures differ by party too, though the
            method has no party term.
          </P>
          <Sub title="Industries and top donors">
            <P>
              The money column on a profile also shows where the money came from by industry, and
              the top donors. Each PAC contribution takes the industry of the organization behind
              the PAC, and each itemized gift from a person takes the industry of the donor&apos;s
              stated occupation, where most people in that occupation work in one industry by Census
              Bureau survey data (lawyers in law, nurses in health care). A chief executive, a
              retiree or a job title the data can&apos;t place counts toward no industry. Money from
              a party, candidate, joint-fundraising or leadership committee is political money, not
              an industry&apos;s: the FEC&apos;s own registration of each committee decides that,
              not its name. A PAC a labor organization sponsors is labor money, and a company&apos;s
              PAC takes the SEC&apos;s industry code for that company when the SEC lists it; only
              the rest is read from the name. The public API&apos;s breakdown also gives how
              concentrated the money is across industries, as a Herfindahl-Hirschman index
              <Cite id="rhoades1993" />, and how much of it the top ten donors gave
              <Cite id="bonica2014" />. The member&apos;s own money and transfers from their own
              committees are not counted as donors.
            </P>
          </Sub>
        </More>
      </Section>

      <Section id="alignment" title="Votes: breaks with the party">
        <P>
          The share of this Congress&apos;s party-line votes on which the member sided with the
          other party, from the roll calls as Senate.gov and the House Clerk record them. The tick
          is how often members of the same party break in seats that lean the same way.
        </P>
        <More label="The full method" context="Breaks with the party">
          <Steps>
            <Step n={1} title="Pick the party-line votes">
              Every roll call of the current Congress counts when the parties split on it: at least
              65% of one party voting yes and at most 35% of the other. Votes with no recorded roll
              call don&apos;t count, and neither does housekeeping (quorum calls, adjourning,
              approving the Journal, the House&apos;s previous question, motions to table or to
              recommit, and the House&apos;s motion to commit), which splits on party lines as a
              matter of course. Each bill or nomination counts once, however many times it came to a
              vote: cloture and then confirmation on one nominee is one decision.
            </Step>
            {/* The 200 below is prior_until_votes in backend/app/data/position_confidence.json,
                pinned by test_position_confidence.py. */}
            <Step n={2} title="Count breaks toward the other party">
              A member breaks when they vote with the other side <em>and</em> the party&apos;s
              members who broke on that vote sit nearer the other party than the party does. A vote
              against the party from its own flank (hardliners voting down their party&apos;s bill)
              is listed on the profile apart and not counted here
              <Cite id="kirkland2017" />. Which side of the party a member sits on is read from
              Voteview&apos;s position for the current Congress
              <Cite id="nokken2004" /> once it rests on 200 roll calls (our convention); until then
              from a full record in the last Congress, and without one from this Congress&apos;s
              position so far. A position cast in another party is never used. Every break on a
              profile shows that roll call&apos;s party tallies.
            </Step>
            <Step n={3} title="Work out what the seat expects">
              From the chamber itself, on every run: how often members of the same party break in
              seats with the same partisan lean (Cook PVI). Each party gets its own line, so a
              Republican in a seat the Democrat won for president is compared with how Republicans
              in seats like that actually vote. A House seat&apos;s lean is the district the member
              was elected on, not the map the next election uses.
            </Step>
          </Steps>
          <P>
            One procedural exception, read from the chamber&apos;s own recorded result: a majority
            leader who votes with the winning side against their party on a motion, so that they can
            move to reconsider it, is not counted as breaking, and only during their time in that
            office. The Speaker and minority leaders are never exempted.
          </P>
          <Sub title="What constituents say">
            <P>
              Profiles also show how the member&apos;s own constituents rate them, split by party,
              from the Cooperative Election Study, a national survey of about 60,000 people. The
              ratings from the other party and independents follow how a member actually votes,
              while the member&apos;s own party mostly rates by party
              <Cite id="ansolabehere2022" />. A senator&apos;s figure from the 2022 and 2024
              surveys, from different respondents, agrees closely; a House district has about a
              hundred respondents with an opinion, so a House figure leans on what similar members
              get and is marked when it does. The current survey was taken in October and November
              2024.
            </P>
          </Sub>
        </More>
      </Section>

      <Section id="effectiveness" title="Bills moved">
        <P>
          How many of the member&apos;s bills and joint resolutions this Congress got past
          committee: reported, discharged or taken to the floor, passed their chamber, or became
          law. From Congress.gov bill status. The tick is the peers&apos; median.
        </P>
        <More label="The full method" context="Bills moved">
          <P>
            Each profile also breaks the member&apos;s bills down by the furthest stage reached:
            introduced only, action in committee (a hearing or markup), past committee, passed the
            chamber, became law. Simple and concurrent resolutions (electing a member to a
            committee, say) are listed apart, so a resolution the chamber agreed to isn&apos;t
            reported as a bill that passed. The stages follow the Legislative Effectiveness Score
            political scientists use
            <Cite id="volden2014" />; the stage each bill is given was checked bill by bill against
            Volden and Wiseman&apos;s 118th Congress counts and agreed on at least 93% at every
            stage (backend/scripts/research_les_stage_classifier.py).
          </P>
          <P>
            Bills with cosponsors from the other party are more likely to become law, for majority
            and minority members alike
            <Cite id="harbridgeyong2023" />; each bill&apos;s page lists its cosponsors.
          </P>
        </More>
      </Section>

      <Section id="leadership" title="Leader or follower, and ideology">
        <P>
          The line under a member&apos;s name (say, &ldquo;Progressive Democrat leader&rdquo;) comes
          from the cosponsorship network: who signs onto whose bills, a separate network for each
          chamber.
        </P>
        <More label="The full method" context="Leader or follower, and ideology">
          <Sub title="Leader or follower">
            <P>
              PageRank
              <Cite id="brin1998" /> over the cosponsorship network, the approach GovTrack uses
              <Cite id="tauberer2012" />: a member whose bills attract many cosponsors, especially
              influential ones, ranks higher. Each cosponsorship is weighted by what happened to the
              bill (became law, advanced, or stalled), so signing onto symbolic bills doesn&apos;t
              build the same weight as signing onto laws. Network position takes years to build, so
              for members with under six years in office the label is pulled toward the middle in
              proportion to tenure: a newcomer reads as &ldquo;not enough track record yet&rdquo;,
              not as a follower.
            </P>
          </Sub>
          <Sub title="Ideology and positions by policy area">
            <P>
              A left-right position from singular value decomposition of the cosponsorship matrix,
              following GovTrack
              <Cite id="tauberer2012" />, like DW-NOMINATE
              <Cite id="poole1985" />, but from cosponsorships rather than votes. The ideology word
              is the member&apos;s third of their own party on it.
            </P>
            <P>
              Positions by policy area come mainly from votes: a Yea counts toward the party the
              bill leans to, a Nay toward the other, in each policy area the bill touches (see{" "}
              <A href="#party-labels">how a bill gets a party label</A>
              ). The lean bar runs from every counted vote going the Democrats&apos; way to every
              one going the Republicans&apos; way. The ideology position only steadies it for
              members with few votes, fading to no weight at 15. The label (deeply or moderately
              partisan, or centrist) is the member&apos;s third within their own party, so a fixed
              cut-off can&apos;t make one party look more extreme just because the two parties sit
              on different ranges. It reads &ldquo;cross-cutting&rdquo; instead when the areas where
              they side with the other party make up more than 30% of their areas, each area counted
              by how strongly it leans.
            </P>
          </Sub>
        </More>
      </Section>

      <Section id="party-labels" title="How a bill gets a party label">
        <P>
          Whether a member broke with their party on a vote is defined only by how the parties
          actually voted on that roll call, so a bill that reads partisan but passed with both
          parties&apos; majorities is not a party-line vote, and no break is counted without a roll
          call. The party badge on a member&apos;s sponsored bill is how the parties voted on the
          roll call Civitas tracks for it (R, D or bipartisan) when it is one of the key bills
          Civitas follows, unless that roll call was housekeeping (a motion to table or recommit,
          say); otherwise it comes from the bill&apos;s content.
        </P>
        <More label="The full method" context="How a bill gets a party label">
          <P>
            Partisan depth (the lean bar and its policy-area breakdown) reads a bill&apos;s lean the
            same way: from how the parties voted on its roll call, so a bill both parties passed
            leans to neither and counts toward neither. The bill&apos;s content decides its lean
            only where no roll call recorded how each party voted, and never decides a break. For
            that, the bill is compared with each party&apos;s positions in that policy area
            <Cite id="manning2008" />, with its direction (does it strengthen or roll back?)
            separating cases where both parties have positions on the same topic
            <Cite id="laver2000" />. Each party&apos;s position in an area starts from its platform
            and is refined over time by the bills earlier runs labelled for that party (by the roll
            call&apos;s split where there was one, by content otherwise, never from a housekeeping
            motion)
            <Cite id="yarowsky1995" />.
          </P>
          <More label="Why votes win over content">
            <P>
              Roll calls don&apos;t always reflect sincere preferences: vote trading, whip pressure
              and omnibus packaging all intervene
              <Cite id="clinton2004" />
              <Cite id="snyder2000" />, which is why content analysis was once the primary signal. A
              2026-06 audit found that choice marked members as voting &ldquo;against their
              party&rdquo; on bills nearly everyone supported. Party loyalty asks whether a member
              broke with their party, and only the roll call can say that.
            </P>
          </More>
        </More>
      </Section>

      <Section id="donor-vote" title="Donor-vote connections">
        <P>
          An industry that makes up at least a quarter of a member&apos;s classifiable donor money
          is matched to the member&apos;s votes on legislation in that industry&apos;s policy area.
          For the industry&apos;s largest donor we look up the Lobbying Disclosure Act registry
          (lda.gov) under the donor&apos;s name: the sponsoring company&apos;s when the donor is its
          PAC. A PAC the FEC lists no separate sponsor for is searched under its own name, and
          finding nothing there is reported as unknown, not as no lobbying. Spending is what the
          filings report, one report per lobbying firm, client and quarter: an amendment restates
          its quarter, so only the latest filed is counted. On a profile, the money column links to
          them.
        </P>
        <More label="The full method" context="Donor-vote connections">
          <P>
            Profiles show registered lobbying spend for every client the search matched, each with
            its amount, and any bill the member voted on that those filings name, linked to the
            filing and the client it was for. A client sharing the name can be a subsidiary or a
            separate company (an independent bottler beside The Coca-Cola Company), and no name rule
            can tell which, so the client is always shown rather than assumed to be the donor.
            Filings cite bills by number and often name earlier congresses&apos; bills, so a number
            counts only when the filer&apos;s wording around it also matches that bill&apos;s title
            in the current Congress.
          </P>
          <P>
            A filing records that an organization lobbied on a bill, not which way, so none of this
            says whether a vote went the donor&apos;s way, and none of it shows influence
            <Cite id="ansolabehere2003" />.
          </P>
        </More>
      </Section>

      <Section id="with-the-president" title="Votes with the president">
        <P>
          Each member&apos;s profile, and the{" "}
          <Link href="/congress/president/house">chamber pages</Link>, show how often the member
          voted on the president&apos;s side of a bill, beside what members of the same party in
          seats that lean the same way do. The president&apos;s side comes only from a Statement of
          Administration Policy, the signed position the White House budget office (OMB) issues to a
          chamber before a vote, linked with every vote it counts. Only a chamber&apos;s final vote
          on a measure counts, or a vote on an amendment a statement names: no statement addresses a
          procedural vote. A statement that takes no position on passage counts no vote.
        </P>
        <More label="The full method" context="Votes with the president">
          <P>
            Whether a statement supports or opposes the bill is read from its opening and closing
            paragraphs by comparing each sentence with written descriptions of support, opposition
            and neither <Cite id="yin2019" />; the sentence it was read from is shown. Checked
            against a reading of every statement, it agreed on 185 of 193, and none of the eight
            others had support and opposition swapped. The expected rate is fitted each night, per
            chamber and party, on the members&apos; own rates against their seat&apos;s partisan
            lean <Cite id="papke1996" />. Each rate carries a 95% range <Cite id="wilson1927" />
            <Cite id="agresti1998" />, and a member reads as voting with the president more or less
            often than expected only when that range misses the expected rate. Voting with or
            against the president is neither good nor bad.
          </P>
        </More>
      </Section>

      <Section id="trades-and-holdings" title="Stock trades and holdings">
        <P>
          Profiles also show members&apos; STOCK Act trade disclosures and the assets from their
          latest annual financial disclosure, and the sitting president&apos;s trade filings and
          annual report the same way.
        </P>
        <More label="The full method" context="Stock trades and holdings">
          <P>
            These forms report each amount as a range, with no purchase price or share count, so no
            profit or net-worth figure is produced: the ranges are shown as filed. A range with no
            ceiling, such as &ldquo;Over $50,000,000&rdquo;, is shown as &ldquo;$50,000,000+&rdquo;.
            The holdings chart sizes each asset by its range&apos;s midpoint (an open-ended range by
            its minimum) and says so; a value a filer stated exactly instead of as a range is shown
            and charted as that figure. A member who has filed no annual report yet, such as one
            seated in a special election, is shown their new-filer report, named by the date it was
            filed. Asset categories come only from the type the filer declared on the form, never
            guessed from an asset&apos;s name. The president&apos;s form has no type column: a
            business is categorized by the underlying assets it states, a fund by the form&apos;s
            fund marker, and every other security reads &ldquo;type not stated&rdquo;. A report that
            can&apos;t be read, such as a scanned paper filing, is linked rather than machine-read.
          </P>
        </More>
      </Section>
    </AboutPage>
  );
}
