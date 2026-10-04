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
  Steps,
  Step,
  More,
  Cite,
  A,
} from "@/components/about/AboutPage";
import ScoreWeightBar from "@/components/about/ScoreWeightBar";
import SignalOverlapReading from "@/components/about/SignalOverlapReading";

export const metadata = pageMetadata({
  title: "How Senators and Representatives Are Scored",
  description:
    "The three parts of every member of Congress's Representation Score (Funding Independence, Constituent Alignment and Legislative Effectiveness), what moves each, and the evidence behind them.",
  path: "/about/scores",
});

export default function ScoresChapter() {
  return (
    <AboutPage
      href="/about/scores"
      eyebrow="Methodology · Senate & House"
      title="How Congress is scored"
      lede={
        <p>
          Every senator and representative is scored the same way, from FEC filings, roll-call votes
          and Congress.gov bill records. This is how each part works.
        </p>
      }
    >
      <Summary>
        <Point>
          The Representation Score blends three parts, about a third each: where a member&apos;s
          campaign money comes from, whether they vote the way their seat elected them to, and
          whether their legislation goes anywhere.
        </Point>
        <Point>
          Members are compared with their own chamber, and on voting, with their own party in seats
          that lean the same way. Those comparisons are re-measured on every nightly run, not typed
          in by hand.
        </Point>
        <Point>
          Scores cover the current Congress. Funding covers the election that won the member their
          seat.
        </Point>
        <Point>
          A thin record is pulled toward the middle, so one vote or one bill can&apos;t produce an
          extreme score. Missing data counts as neutral, never as zero.
        </Point>
        <Point>
          Where a setting is a judgment call rather than a measurement, this page says so.
        </Point>
      </Summary>

      <Section id="overall" title="How the three parts combine">
        <ScoreWeightBar />
        <P>
          Each part runs from 0 to 100, higher is better, and the Representation Score is their
          weighted average. Every senator and representative goes through identical formulas in the
          same nightly run, so scores are comparable across both chambers.
        </P>
        <P>
          Each part answers to the same yardstick: does the member carry out the will of the
          majority of their constituents, not the preferences of a few large donors, and not party
          defection for its own sake? Crossing party lines earns credit only where it plausibly
          moves toward what the seat wants, and the funding measures exist because money
          concentrated in few hands is the main way representation drifts away from the majority.
        </P>
        <Sub title="Thin records and missing data">
          <P>
            When there is little data, a score is pulled toward a neutral 50 in proportion to how
            little there is. This borrows the idea of shrinkage estimation, which keeps small
            samples from producing extreme results
            <Cite id="efron1975" />, in a simpler form: the pull is set by a fixed count of
            observations rather than estimated from the data. Two parts differ. Constituent
            Alignment&apos;s voting half pulls a thin record toward what a typical member of the
            same party scores, because 50 sits below nearly every member on that scale. Legislative
            Effectiveness&apos;s bill half isn&apos;t pulled at all, because a member&apos;s bills
            are their whole record, not a sample of it.
          </P>
        </Sub>
        <Sub title="Why the current Congress, not a career">
          <P>
            A member who did great work a decade ago and has coasted since shouldn&apos;t get credit
            for it every night, so votes, bills and sponsorship cover the current two-year Congress.
            That is stricter than a six-year Senate term, and it avoids guessing term boundaries the
            official records don&apos;t publish. Funding is the exception: senators legitimately
            raise little money outside election years, so funding covers the member&apos;s most
            recent completed election: the campaign that won the current seat, not a re-election
            campaign still under way. Score trend charts mark the start of each Congress so a reset
            reads as what it is.
          </P>
        </Sub>
      </Section>

      <Section id="funding" kicker="Part 1" title="Funding Independence">
        <P>
          Rewards campaigns funded by many small donors rather than by PACs, a handful of big
          donors, or a single industry. Four measurements, weighted 20 : 10 : 10 : 13:
        </P>
        <Steps>
          <Step n={1} title="PAC dependency">
            The share of the member&apos;s contributions that came from PACs, against what the seat
            predicts: for a senator, the share senators from states that size take (small states
            have small donor pools, so their senators lean on PACs more); for a representative, the
            House median, since every district holds the same population. Both are re-measured every
            update. The expected share scores 50, and the gap is counted in percentage points
            against how widely the chamber varies, so a senator taking 5% where 3% is typical is a
            couple of points above typical, not almost twice it. Per chamber, because House
            candidates rely on PAC money far more than Senate candidates.
          </Step>
          <Step n={2} title="Small-donor share">
            Money in gifts under $200: the broadest possible funding base. A senator is compared
            with what a state of that population typically raises this way; a representative, with
            the House median.
          </Step>
          <Step n={3} title="Top-donor concentration">
            How much of all the outside money came from the top ten donors, ranked against the rest
            of the chamber: the fewer big donors a campaign needs, the higher it scores. The
            member&apos;s own money and transfers from their own committees are left out. Without a
            donor list to measure, this part is left out of the score the same way.
          </Step>
          <Step n={4} title="Industry concentration">
            How spread out the money is across industries, measured with the Herfindahl-Hirschman
            index
            <Cite id="rhoades1993" />, ranked against the members of the member&apos;s own party in
            the chamber (an independent against the whole chamber). One party&apos;s donors work in
            fewer, broader industries than the other&apos;s, so a ranking across the chamber scored
            each party&apos;s donor base rather than the member. Funding concentrated in one
            industry suggests a risk of regulatory capture. Every PAC contribution is assigned the
            industry of the organization behind the PAC, and each itemized individual gift the
            industry of the donor&apos;s stated occupation, where most people in that occupation
            work in one industry by Census Bureau survey data (lawyers in law, nurses in health
            care); a chief executive, a retiree or a job title the data can&apos;t place counts
            toward no industry. When too little money can be assigned to tell, this part is left out
            and the score is weighed over the other three, rather than counted as neutral. Money
            from a party, candidate, joint-fundraising or leadership committee is political money,
            not an industry&apos;s: the FEC&apos;s own registration of each committee decides that,
            not its name. A state&apos;s home industry counts as concentration like any other,
            because local economic weight plausibly gives an industry more leverage over a member,
            not less.
          </Step>
        </Steps>
        <P>
          Shares are of contributions (money given by individuals, PACs, party committees or the
          candidate), not of total receipts, which also count transfers from joint fundraising
          committees whose sources aren&apos;t broken out. Money that can&apos;t be attributed at
          all (committee transfers, donations with no employer listed; a median of about a third of
          senators&apos; funding) is scored as neutral, not as a sign of concentration.
        </P>
        <P>
          Democrats and Republicans raise money differently on average (in July 2026 Senate data,
          Democrats took roughly half the PAC share and twice the small-donor share), so average
          scores differ by party too, though the formula has no party term.
        </P>
        <More label="The evidence, and two inputs we removed">
          <P>
            PAC dependency follows Stratmann
            <Cite id="stratmann2005" />, who found PAC contributions track roll-call behavior more
            closely than individual contributions do. The concentration measures apply the same
            logic at the donor level
            <Cite id="bonica2014" />.
          </P>
          <P>
            Two inputs were removed in v6.13 after checking them against FEC data for 2020–2024
            incumbents. Outside spending (super PAC and other independent spending supporting a
            member) used to count toward PAC dependency, but it tracks how competitive a race is,
            not how dependent the member is: it was highest in swing seats, lower for members who
            take more PAC money, and by law the member can&apos;t direct it. &ldquo;Source
            breadth&rdquo; turned out to be the small-donor share counted a second time (it
            explained 79–86% of breadth&apos;s variation) plus a penalty on self-funding. The study
            is in the project repository at docs/research/funding-independence.md.
          </P>
          <P>
            Industry concentration was a separate Funding Diversity score until 2026-07, when the
            two were found to correlate at r = 0.72 across the Senate and it was folded in here.
          </P>
        </More>
      </Section>

      <Section id="alignment" kicker="Part 2" title="Constituent Alignment">
        <P>
          Checks whether a member votes the way their seat elected them to, by comparing them with
          members of their own party in seats that lean the same way. Breaking with the party about
          as often as those members do scores highest. It is 70% how often they break and 30% where
          their overall voting record sits.
        </P>
        <Sub title="How often they break with their party">
          <Steps>
            <Step n={1} title="Pick the party-line votes">
              Every roll call of the current Congress counts when the parties split on it: at least
              65% of one party voting yes and at most 35% of the other. Votes with no recorded roll
              call don&apos;t count, and neither does housekeeping (quorum calls, adjourning, the
              House&apos;s previous question, motions to table or to recommit), which splits on
              party lines as a matter of course. Each bill or nomination counts once, however many
              times it came to a vote: cloture and then confirmation on one nominee is one decision.
            </Step>
            <Step n={2} title="Count breaks toward the other party">
              A member breaks when they vote with the other side <em>and</em> the party&apos;s
              members who broke on that vote sit nearer the other party than the party does. A vote
              against the party from its own flank (hardliners voting down their party&apos;s bill)
              is listed on the profile but not counted here, because how far toward the flank a
              member sits is already scored by where their record sits (below)
              <Cite id="kirkland2017" />. Every break on a profile shows that roll call&apos;s party
              tallies.
            </Step>
            <Step n={3} title="Work out what the seat expects">
              From the chamber itself, on every run: how often members of the same party break in
              seats with the same partisan lean (Cook PVI). Each party gets its own line, allowed to
              bend at swing seats, so a Republican in a seat Biden won is compared with how
              Republicans in seats like that actually vote. A House seat&apos;s lean is the district
              the member was elected on (their Congress&apos;s lines, not the map the next election
              uses), and a score&apos;s worked math is redone on the same lines as the score.
            </Step>
            <Step n={4} title="Measure the gap">
              In standard deviations, not percentage points. Four extra points on a seat whose
              members break 1.5% of the time is several times the norm; four extra on a seat whose
              members break 7% of the time is a modest departure.
            </Step>
            <Step n={5} title="Score it">
              Matching the expectation scores 100. Breaking more often lowers the score in a
              straight line, reaching 0 at one and a half times the gap the most out-of-pattern
              tenth of the member&apos;s party shows. Being more loyal than expected lowers it half
              as fast, reaching 0 only at three times that gap.
            </Step>
            <Step n={6} title="Allow for thin records">
              With fewer than 20 party-line roll calls, the result is pulled toward what a typical
              member of the same party scores, so a single break can&apos;t reach either end and a
              newcomer isn&apos;t ranked below colleagues just for having few votes.
            </Step>
          </Steps>
          <P>
            One procedural exception, read from the chamber&apos;s own recorded result: a majority
            leader who votes with the winning side against their party on a motion, so that they can
            move to reconsider it, is not counted as breaking, and only during their time in that
            office. The Speaker and minority leaders are never exempted.
          </P>
        </Sub>
        <Sub title="Where their voting record sits">
          <P>
            The other 30% compares the member&apos;s overall roll-call position from Voteview: the
            congress-specific Nokken-Poole estimate
            <Cite id="nokken2004" />, not a career average, with what a same-party member of a
            similarly-leaning seat typically holds. Toward the party&apos;s flank scores below
            neutral; toward the seat&apos;s center scores above, by the same amount either way. When
            that data isn&apos;t available, the score is all break-rate.
          </P>
          <P>
            Both parts come from roll calls (a crossing rate and a position), so every pipeline run
            checks that they still measure different things by how closely they move together across
            each chamber. <SignalOverlapReading pair="constituent" />
          </P>
        </Sub>
        <Sub title="What 0 and 100 mean">
          <P>
            They are relative positions, not verdicts. A 100 means the member breaks with their
            party about as often as members of their own party in seats that lean the same way. A 0
            means they break far more often than that, or, much more rarely, far less. Because each
            party&apos;s yardstick is measured from its own members, a party that happens to be more
            unified isn&apos;t scored higher for it: across every Senate from 1989 on, the two
            parties&apos; averages differed by 1.8 points on average, and which one was higher
            changed from Congress to Congress.
          </P>
        </Sub>
        <More label="Where the numbers come from, with September 2026 examples (before v6.20)">
          <P>
            The gap is measured from each chamber on every update, separately for each party. In the
            Senate as of September 2026 it was about 0.37 standard deviations per vote for Democrats
            and 0.33 for Republicans, so a Democrat&apos;s vote score reached 0 at about 0.56 more
            independent than their seat&apos;s norm. Each score&apos;s breakdown on a profile shows
            its own numbers.
          </P>
          <P>
            The one and a half and the three are design choices, not measurements. Tested against
            election results, wider or narrower settings didn&apos;t fit consistently better, so
            they were set to reserve 0 for the heaviest breakers (five senators as of September
            2026) and to keep loyalty the gentler side. No senator was then more than about
            three-quarters of a gap more loyal than their seat&apos;s norm, so extra loyalty cost at
            most about 24 points. The 70/30 split is also a design choice: the break-rate part
            showed the larger association in the one election where both could be tested.
          </P>
        </More>
        <More label="Why loyalty counts, and why safe seats get no special treatment">
          <P>
            Through v6.12, a member more loyal than expected was held at neutral, and credit for
            breaking shrank in safe seats. We tested both choices against 2,545 House re-election
            results (1994–2010), using the approach of the studies that established this measure:
            does it predict how the incumbent does with their own voters once the seat&apos;s
            partisanship, the national tide and seniority are accounted for
            <Cite id="carson2010" />
            <Cite id="canes2002" />? It does, and it contradicted both choices. Loyalty beyond what
            the seat predicts carried the strongest association of all (about 2 points of vote share
            per standard deviation in 2004), and the association was the same in safe seats as in
            competitive ones.
          </P>
          <P>
            Why the score peaks at the seat&apos;s norm (since v6.16): a member elected by a seat
            under a party label answers to both. The member&apos;s own party&apos;s voters reward
            exactly this shape: in House primaries from 1990 to 2010, incumbents who scored higher
            on it won a larger share of the primary vote, and those who broke far more than their
            seat&apos;s norm lost about 3 points per standard deviation. The electorate as a whole
            leans the other way: in Senate general elections from 1990 to 2024 and the 2004 House
            elections, members who broke more than their seat&apos;s norm did somewhat better. We
            report that rather than hide it: the score follows what a member was elected under, not
            what maximizes their vote share.
          </P>
          <P>
            The position half uses per-party fits, which avoid the swing-seat artifact a single
            pooled fit creates
            <Cite id="bafumi2010" />, and the congress-specific position predicted results better
            than career-long DW-NOMINATE. The full study, including where the evidence is weak (the
            association fades after 2008 as elections nationalized, and the Senate doesn&apos;t
            confirm the House result for loyalty), is in the project repository at
            docs/research/constituent-alignment.md.
          </P>
        </More>
        <More label="What this part used to measure">
          <P>
            Until v6.20 (September 2026) the break rate was read from a sample of each member&apos;s
            latest votes, counted every vote against most of the party, including breaks from the
            party&apos;s flank, and counted a nominee&apos;s cloture and confirmation as two. Over
            the whole Congress, counting only breaks toward the other party and each measure once
            predicted election results at least as well (docs/research/constituent-alignment.md,
            sections 11 and 12). The September 2026 figures above were measured before that change.
          </P>
          <P>
            Before v4.2 it was called Independent Voting and rewarded raw defection, and it exempted
            party-line votes on topics tied to a member&apos;s top donor industries. That exemption
            was removed: donor industries aren&apos;t a stand-in for a state&apos;s interests, and
            it shielded exactly the votes most open to donor influence.
          </P>
          <P>
            Coalition breadth (20% here from v5 through v6.10) moved to Legislative Effectiveness:
            demand for bipartisanship varies with a seat&apos;s own makeup
            <Cite id="harbridge2011" />, so a bipartisan member of a lopsided seat can be bipartisan
            and misaligned at once. A Donor Independence component (25%, through v6.4) was removed
            in 2026-07: it measured a close cousin of Funding Independence and reduced to one of
            four fixed values for 85% of senators, because no data source discloses per-bill donor
            positions. As Ansolabehere et al. caution, a correlation between donations and votes
            doesn&apos;t show one caused the other
            <Cite id="ansolabehere2003" />.
          </P>
        </More>
      </Section>

      <Section id="effectiveness" kicker="Part 3" title="Legislative Effectiveness">
        <P>
          Measures whether a member gets legislation done, following the Legislative Effectiveness
          Score political scientists use
          <Cite id="volden2014" />, where a bill that becomes law counts for far more than one that
          is only introduced. Three components:
        </P>
        <List>
          <Item label="Bills and how far they got (60%)">
            The member&apos;s legislative record, compared with the typical sponsor of the same
            status, majority or minority, in the same chamber.
          </Item>
          <Item label="Legislative leadership (25%)">
            How central the member is in the network of who cosponsors whose bills (see{" "}
            <A href="#leadership">below</A>).
          </Item>
          <Item label="Bipartisan attraction (15%)">
            The share of cosponsors on the member&apos;s own bills who come from the other party,
            compared with the median member of the same party. Democrats cosponsor across the aisle
            more readily than Republicans under either party&apos;s majority, so a chamber-wide
            median would score the other party&apos;s habits rather than the member.
          </Item>
        </List>
        <P>
          When cosponsorship data is missing, the split falls back to 70% bills and 30% leadership.
        </P>
        <P>
          Leadership and bipartisan attraction are both measured from the cosponsorship network (how
          central a member is, and how many cosponsors cross party lines to join them), so their
          combined weight is capped at 40%, and every pipeline run checks that they still carry
          separate information by how closely they move together across each chamber. Two earlier
          pairs that shipped as distinct signals turned out to move together at |r| of 0.72 and 0.76
          and were restructured (see the <A href="/changelog">scoring changelog</A>).{" "}
          <SignalOverlapReading pair="effectiveness" />
        </P>
        <Sub title="How bills are counted">
          <Steps>
            <Step n={1} title="Weight by significance">
              Bills and joint resolutions count five times as much as simple and concurrent
              resolutions. Commemorative bills (renaming a post office, awarding a Congressional
              Gold Medal) count like resolutions; they are recognized from the title.
            </Step>
            <Step n={2} title="Credit every stage reached">
              Introduced, action in committee (a hearing or markup), action beyond committee
              (reported, discharged or on the floor), passed its chamber, became law.
            </Step>
            <Step n={3} title="Divide by the chamber's total at each stage">
              Few bills get far, so later stages have small totals and are worth more: a bill that
              becomes law counts as much as dozens of introductions. The stages are added up so the
              average member scores 1.0.
            </Step>
            <Step n={4} title="Compare with the typical member of the same status">
              Majority-party members advance bills far more often, so each member is compared with
              the median member of their own status in their own chamber, re-measured every run, as
              in Volden and Wiseman&apos;s own benchmarks.
            </Step>
          </Steps>
          <P>
            Members with few bills aren&apos;t pulled toward 50, and a member with no substantive
            bills after half a year in office scores as a record of zero, so doing nothing never
            outscores trying. Each profile breaks its bill count into introduced only, advanced
            further and became law.
          </P>
        </Sub>
        <Sub title="Why bipartisan attraction">
          <P>
            Attracting cosponsors from the other party robustly predicts lawmaking success for
            majority and minority members alike, and it is specifically attracting them, not
            cosponsoring across the aisle, that carries the effect
            <Cite id="harbridgeyong2023" />. So this uses a receive-only rate, not the
            give-and-receive bipartisanship figure shown on profiles. That evidence is a strong
            association rather than proof of cause, and the measure is an input to effectiveness
            rather than an output, which is why its weight stays modest.
          </P>
        </Sub>
        <More label="How we checked it against the published scores">
          <P>
            Checked against Volden and Wiseman&apos;s own published scores for the 110th–118th
            Congresses, this version ranks members at a rank correlation of 0.90 in the House and
            0.97 in the Senate. The stage each bill is given was checked bill by bill against their
            118th Congress counts (backend/scripts/research_les_stage_classifier.py): at least 0.93
            at every stage compared. The commemorative classifier was calibrated against their
            commemorative counts (backend/scripts/calibrate_commemorative.py): exact per-member
            counts for 87% of the 118th House, about one false flag per 1,000 bills. The
            reproduction is backend/scripts/research_les_stage_weighting.py.
          </P>
          <P>
            One difference remains: their top &ldquo;substantive and significant&rdquo; tier (10×)
            comes from CQ Almanac coverage, which has no source here.
          </P>
        </More>
        <More label="What changed along the way">
          <P>
            Until v6.14 (September 2026) this skipped the division by each stage&apos;s total, which
            made a law worth four introductions; that version ranked members at 0.71 (House) and
            0.76 (Senate) against the published scores and mostly tracked how many bills a member
            introduced. v6.17 added action beyond committee as its own stage, raising the Senate
            from 0.96 to 0.97.
          </P>
          <P>
            Also until September 2026, the minority party was judged against the chamber median
            scaled by the ratio of the two parties&apos; advancement rates (in a July 2026
            measurement, Senate majority sponsors advanced 3.6% of bills against 2.4% for the
            minority; House 6.4% against 2.4%). Most credit came from introducing bills, which
            majority status doesn&apos;t change, so that ratio over-corrected: the House&apos;s gap
            between the parties reached 18 points. Each status is now centered on its own typical
            member. Since v6.9 all of this is measured separately for each chamber; one pooled
            figure had understated House members and overstated senators. The{" "}
            <A href="/changelog">scoring changelog</A> has the numbers.
          </P>
        </More>
      </Section>

      <Section id="leadership" kicker="Context on the leaderboard" title="Leadership and ideology">
        <P>
          Two more figures come from the cosponsorship network (who signs onto whose bills), a
          separate network for each chamber. Leadership feeds Legislative Effectiveness; ideology is
          context only.
        </P>
        <Sub title="Legislative Leadership (0–100)">
          <P>
            PageRank
            <Cite id="brin1998" /> over the cosponsorship network, the approach GovTrack uses
            <Cite id="tauberer2012" />: a member whose bills attract many cosponsors, especially
            influential ones, scores higher. Each cosponsorship is weighted by what happened to the
            bill (became law, advanced, or stalled: a stalled bill still counts for something, as
            evidence of a working relationship), so signing onto symbolic bills doesn&apos;t build
            the same weight as signing onto laws. Raw values are log-rescaled within the chamber, so
            the median member sits near 50 and the lowest member scores 0.
          </P>
          <P>
            Network position takes years to build, so for members with under six years in office,
            the part of Legislative Effectiveness built from it, and the leader or backbencher label
            on their profile, is pulled toward neutral in proportion to tenure. A newcomer reads as
            &ldquo;not enough track record yet&rdquo;, not &ldquo;bad at leadership&rdquo;. The
            leaderboard shows the unadjusted network score.
          </P>
        </Sub>
        <Sub title="Ideology (0–1) and partisan depth">
          <P>
            A behavioral left–right position from singular value decomposition of the cosponsorship
            matrix, following GovTrack
            <Cite id="tauberer2012" />, like DW-NOMINATE
            <Cite id="poole1985" />, but from cosponsorships rather than votes. Lower is more
            progressive, higher more conservative.
          </P>
          <P>
            Partisan depth (how strongly a member leans within each policy area) comes mainly from
            their votes, each counted toward the party whose positions its bill matches in that area
            (see <A href="#party-labels">how a bill gets a party label</A>
            ). The lean bar runs from every counted vote going the Democrats&apos; way to every one
            going the Republicans&apos; way. The ideology score only steadies it for members with
            few votes, fading to no weight at 15. The label (deep, moderate or centrist) is the
            member&apos;s third within their own party, or &ldquo;cross-cutting&rdquo; when more
            than 30% of their positions sit with the other party, so a fixed cut-off can&apos;t make
            one party look more extreme just because the two parties sit on different ranges. The
            one-line description on a profile (say, &ldquo;Progressive Democrat leader&rdquo;)
            combines ideology, party and a leadership tier.
          </P>
        </Sub>
      </Section>

      <Section id="party-labels" title="How a bill gets a party label">
        <P>
          Whether a member broke with their party is defined only by how the parties actually voted
          on that roll call, so a bill that reads partisan but passed with both parties&apos;
          majorities is not a party-line vote, and no break is counted without a roll call. The
          party badge on a bill is that split where there was a roll call, and its content
          otherwise.
        </P>
        <P>
          The bill&apos;s content decides partisan depth (the lean bar and its policy-area
          breakdown), and never a break. For it, each bill a member voted on is compared with each
          party&apos;s positions in that policy area
          <Cite id="manning2008" />, with its direction (does it strengthen or roll back?)
          separating cases where both parties have positions on the same topic
          <Cite id="laver2000" />. Each party&apos;s position in an area starts from its platform
          and is refined over time by the bills earlier runs labelled for that party, by the
          parties&apos; actual split where there was a roll call
          <Cite id="yarowsky1995" />.
        </P>
        <More label="Why votes win over content">
          <P>
            Roll calls don&apos;t always reflect sincere preferences: vote trading, whip pressure
            and omnibus packaging all intervene
            <Cite id="clinton2004" />
            <Cite id="snyder2000" />, which is why content analysis was once the primary signal. A
            2026-06 audit found that choice marked members as voting &ldquo;against their
            party&rdquo; on bills nearly everyone supported, pinning every House member&apos;s score
            near 87–89. The question this label answers is whether a member broke with their party,
            and only the roll call can say that.
          </P>
        </More>
      </Section>

      <Section id="donor-vote" title="Donor-vote connections">
        <P>
          Not scored. An industry that makes up at least a quarter of a member&apos;s classifiable
          donor money is matched to the member&apos;s votes on legislation in that industry&apos;s
          policy area. For the industry&apos;s largest donor we look up the Lobbying Disclosure Act
          registry (lda.gov) under the donor&apos;s name: the sponsoring company&apos;s when the
          donor is its PAC. A PAC the FEC lists no separate sponsor for is searched under its own
          name, and finding nothing there is reported as unknown, not as no lobbying.
        </P>
        <P>
          Profiles show registered lobbying spend for every client the search matched, each with its
          amount, and any bill the member voted on that those filings name, linked to the filing and
          the client it was for. A client sharing the name can be a subsidiary or a separate company
          (an independent bottler beside The Coca-Cola Company), and no name rule can tell which, so
          the client is always shown rather than assumed to be the donor. Filings cite bills by
          number and often name earlier congresses&apos; bills, so a number counts only when the
          filer&apos;s wording around it also matches that bill&apos;s title in the current
          Congress.
        </P>
        <P>
          A filing records that an organization lobbied on a bill, not which way, so none of this
          says whether a vote went the donor&apos;s way, and none of it shows influence
          <Cite id="ansolabehere2003" />.
        </P>
      </Section>

      <Section id="trades-and-holdings" title="Stock trades and holdings">
        <P>
          Profiles also show members&apos; STOCK Act trade disclosures and the assets from their
          latest annual financial disclosure, and the sitting president&apos;s trade filings and
          annual report the same way. These are shown, not scored.
        </P>
        <P>
          These forms report each amount as a range, with no purchase price or share count, so no
          profit or net-worth figure is produced: the ranges are shown as filed. A range with no
          ceiling, such as &ldquo;Over $50,000,000&rdquo;, is shown as &ldquo;$50,000,000+&rdquo;.
          The holdings chart sizes each asset by its range&apos;s midpoint (an open-ended range by
          its minimum) and says so. Asset categories come only from the type the filer declared on
          the form, never guessed from an asset&apos;s name. The president&apos;s form has no type
          column: a business is categorized by the underlying assets it states, a fund by the
          form&apos;s fund marker, and every other security reads &ldquo;type not stated&rdquo;. A
          report that can&apos;t be read, such as a scanned paper filing, is linked rather than
          machine-read.
        </P>
      </Section>
    </AboutPage>
  );
}
