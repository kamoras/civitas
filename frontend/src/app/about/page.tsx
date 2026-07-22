import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import TerminalTitlebar from "@/components/TerminalTitlebar";

function Section({ title, children, id }: { title: string; children: React.ReactNode; id?: string }) {
  return (
    <section className="terminal-window mb-6" id={id}>
      <TerminalTitlebar title={`${title.toLowerCase().replace(/ /g, "_")}.txt`} />
      <div className="p-6 space-y-4">
        <h2 className="text-neon-cyan font-terminal text-sm tracking-widest">{title}</h2>
        {children}
      </div>
    </section>
  );
}

function P({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <p className={`text-sm text-matrix-green/70 leading-relaxed ${className}`}>{children}</p>;
}

// Plain-language lead-in for a P block below it: one or two jargon-free
// sentences stating what a section means in practice, before the full
// technical explanation (citations, formulas, edge cases) for readers who
// want the depth. Added 2026-07 after feedback that the methodology page's
// prose — accurate, but dense with citations and terms like "SVD" or "PVI"
// — wasn't readable for a non-technical visitor on its own.
function Gist({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm text-neon-cyan/90 leading-relaxed font-medium">
      <span className="text-neon-yellow/70">In short:</span> {children}
    </p>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-neon-pink/80 font-terminal">{children}</span>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3 text-sm">
      <span className="text-neon-yellow/70 font-terminal shrink-0 sm:w-56">{label}</span>
      <span className="text-matrix-green/60">{value}</span>
    </div>
  );
}

function Cite({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <span className="text-matrix-green/40 text-xs">
      {" "}[<a href={`#ref-${id}`} className="text-neon-cyan/50 hover:text-neon-cyan/80 transition-colors">{id}</a>]{" "}
      <span className="sr-only">{children}</span>
    </span>
  );
}

function Ref({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <li id={`ref-${id}`} className="flex items-start gap-2 text-xs text-matrix-green/50 leading-relaxed">
      <span className="text-neon-cyan/50 shrink-0">[{id}]</span>
      <span>{children}</span>
    </li>
  );
}

export default function AboutPage() {
  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <h1 className="font-pixel text-xl sm:text-3xl text-matrix-green tracking-widest mb-2">
              METHODOLOGY
            </h1>
            <p className="text-matrix-green/40 text-sm max-w-xl mx-auto">
              Full transparency on how scores are calculated, where data comes from,
              and why each analytical technique was chosen. No black boxes.
            </p>
          </div>

          {/* ── Philosophy ── */}
          <Section title="OUR APPROACH">
            <P>
              Civitas is an open-data AI/ML platform that aggregates data from official
              U.S. government sources into unified transparency scorecards for senators,
              House representatives, presidents, and Supreme Court justices. Every score
              is computed from publicly available federal records. We do not editorialize,
              endorse, or oppose any candidate or party.
            </P>
            <P>
              What the scores measure: for senators and House representatives, how well
              they carry out <em className="text-matrix-green/80">the will of the majority
              of their constituents</em> — not the preferences of a few wealthy donors,
              and not party defection for its own sake; for presidents, how well they
              serve the country; for Supreme Court justices, how well they serve the law
              regardless of party. Every scoring dimension is justified against that
              yardstick: crossing party lines is credited only where it plausibly moves
              toward the state&apos;s median voter, and the funding dimensions exist because
              money concentrated in few hands is the main channel by which representation
              drifts away from the majority.
            </P>
            <P>
              Scores reflect observable behavior — voting patterns, funding sources,
              legislative activity — not ideology. The formulas are symmetric across
              parties: the same voting record in the same seat produces the same score
              regardless of whether the member is a Democrat or Republican. The system is
              designed to be structurally non-partisan.
            </P>
            <P>
              Every metric on the scorecard includes a <em className="text-matrix-green/80">[?]
              tooltip</em> explaining what it measures and how to interpret it. Hover on
              desktop or tap on mobile. We believe no number should be presented without
              context — if you see a metric, you should be able to understand what it means
              and where it came from.
            </P>
            <P>
              When data is missing or insufficient, scores default to a neutral 50 out of 100.
              No politician is penalized for something we cannot measure, and no politician
              receives a perfect score without evidence.               This implements Bayesian shrinkage
              toward a neutral prior — a standard statistical technique for preventing
              extreme estimates from small samples.
              <Cite id="19">Efron &amp; Morris 1975</Cite>
            </P>
            <P>
              The Action Center extends this mission to daily civic engagement. It
              automatically surfaces trending issues from news analysis, provides objective
              summaries free of editorial opinion, and recommends non-partisan actions
              citizens can take to participate in their government — without assuming which
              side of any issue the reader supports.
            </P>
          </Section>

          {/* ── Congressional Metrics (Senate + House) ── */}
          <Section title="CONGRESSIONAL SCORECARD METRICS">
            <P>
              Every senator and House representative receives three sub-scores on a
              0-100 scale, weighted into an overall Representation Score. Higher is
              better. All 100 senators and 435 House representatives are scored with
              the identical framework below — same formulas, same data sources (FEC,
              Congress.gov, GovInfo), same classification techniques — so scores are
              directly comparable across both chambers. House members are sourced
              from the same Congress.gov and FEC endpoints and processed in the same
              nightly pipeline run as senators; the House leaderboard supports
              pagination and party filtering to navigate the larger membership.
            </P>
            <P>
              Campaign-promise tracking (kept/broken/partial) is still collected and
              shown on each member&apos;s profile, but is not folded into the weighted
              score below. For the audit history behind the current weights and
              dimensions — including why Promise Persistence was removed, why Funding
              Diversity was folded into Funding Independence, and why Donor Independence
              was removed from Constituent Alignment — see the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70">scoring changelog</a>.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Funding Independence (33%)</Label>
                <Gist>
                  rewards members whose campaigns are funded by lots of small individual
                  donors rather than PACs or a handful of big donors and industries. The
                  more spread-out and grassroots the money, the higher this score — regardless
                  of party or chamber.
                </Gist>
                <P>
                  Measures five dimensions: (1) PAC dependency — a blend of the share of
                  funding from PACs and how close contributing PACs are to their legal
                  per-election cap, chamber-specific since Senate and House candidates
                  rely on PAC money at structurally different rates; (2) the share of
                  funding from small (&lt;$200, unitemized) donors — the broadest possible
                  funding base; (3) relative top-donor concentration — what fraction of
                  the itemized external donor pool comes from the top 10 donors, with the
                  member&apos;s own money and transfers from their own committees excluded;
                  (4) source breadth — small-donor money counts fully, industry-classified
                  money counts moderately, and opaque money counts least; and (5) industry
                  concentration — the inverse Herfindahl-Hirschman Index (HHI) of industry
                  donations, where funding concentrated in a single industry suggests
                  potential regulatory capture. Components (4) and (5) were folded in from
                  a separate Funding Diversity dimension in 2026-07 after finding the two
                  dimensions correlated at r=0.72 across the Senate — the same underlying
                  funding-profile signal under two labels, not two genuinely distinct
                  ones. PAC dependency follows Stratmann (2005),
                  <Cite id="5">Stratmann 2005</Cite>
                  who found that PAC contributions are more strongly correlated with
                  roll-call alignment than individual contributions. The concentration
                  components apply the same intuition as HHI at the donor level,
                  following Bonica (2014)
                  <Cite id="1">Bonica 2014</Cite>
                  {" "}and the industrial-organization logic Rhoades (1993)
                  <Cite id="6">Rhoades 1993</Cite>
                  {" "}built the HHI metric on.
                </P>
                <P>
                  &quot;UNCLASSIFIED&quot; money (committee transfers, joint-fundraising
                  splits, donations lacking employer data — a real 32% median share of
                  total funding across the Senate) is scored neutrally rather than
                  penalized. It is a residual we cannot attribute at all, not evidence of
                  concentration in one source — the same &quot;missing data defaults to
                  neutral&quot; principle applied everywhere else on this page.
                </P>
              </div>

              <div>
                <Label>Constituent Alignment (33%)</Label>
                <Gist>
                  checks whether a member&apos;s voting and coalition-building actually match what
                  their state or district elected them to do. Voting with your party is <em>not</em> penalized
                  on its own — for a safe-seat member, that often IS representing your
                  constituents. The score only moves below neutral when there&apos;s a clear,
                  readable sign of a mismatch: an extreme position for a seat that isn&apos;t
                  safe for that extreme, or an unusually narrow, one-party-only legislative
                  network in a competitive seat.
                </Gist>
                <P>
                  Measures how a member&apos;s voting compares to what their state elected them
                  to do — not raw defection from party. Each member&apos;s contested-vote break
                  rate is scored against a seat-specific expectation derived from state
                  partisan lean (Cook PVI<Cite id="4">Carson et al. 2010</Cite>): an aligned
                  safe seat expects near-base-rate dissent (~3%), a swing seat ~8%, and a
                  seat whose electorate leans toward the opposing party up to ~20%. Matching
                  the expectation scores ~50 — a typical partisan for that seat. The score is
                  deliberately asymmetric around that expectation (v6.6), under one governing
                  principle: it moves off neutral only for <em>readable</em> evidence a member is
                  representing their constituents, and treats behavior whose meaning can&apos;t be
                  read as neutral. Below-expected loyalty is <em>not</em> penalized: it floors at
                  neutral, never below. A low defection rate is unreadable — it may be faithful
                  representation of the coalition that elected the member (not the geographic
                  median voter), it is the structural norm for both parties in the modern
                  Senate, and being &quot;out of step&quot; is a matter of ideological position, not a
                  loyalty rate — so we decline to score the rate itself rather than penalize it.
                  This loyalty floor was the only behavioral change in v6.6, and it is
                  deterministic: a below-expected loyalist with no other signal available scores
                  exactly 50 on this component. v6.7 adds one legible exception: if a member&apos;s
                  cosponsorship-derived ideology score places them in their own party&apos;s most
                  extreme third, and their seat isn&apos;t safely aligned for that extremity, they
                  are discounted below neutral (scaled by how unsafe the seat is — not penalized
                  at all in a genuinely safe seat, where that extremity is the structural norm
                  for both parties). This targets ideological POSITION, not the loyalty rate
                  itself, so it doesn&apos;t reopen the rate-is-unreadable problem above — a member
                  can be maximally loyal and still be flagged if their position is a clear
                  outlier for their seat. This discount&apos;s maximum strength was reduced in v6.8
                  after a fairness audit found it overlapped with coalition breadth (below) more
                  than intended — see the{" "}
                  <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70"> scoring changelog</a>
                  {" "}and the Known Limitations note below. Above-expected crossing is the readable side: it earns credit only
                  where it plausibly moves toward the seat&apos;s political center, discounted by
                  seat lean (full credit in opposed and swing seats, near-neutral in deep
                  aligned seats, since there the center sits with the party). A further discount
                  for members positioned on their party&apos;s ideological flank — whose crossings
                  more likely point <em>away</em> from the center (Kirkland &amp; Slapin 2017) — was
                  designed but is <em>not</em> shipped, and checking it against live data found a
                  deeper problem than a missing calibration number: the members who actually
                  cross party lines most often all read as ideologically centrist on this
                  platform&apos;s own cosponsorship-based ideology measure, not flank-extreme — the
                  opposite of what the discount assumes. Crossing behavior and this measure of
                  ideology turn out to be linked rather than independent, so this specific fix
                  is shelved, not just uncalibrated. This is a
                  deliberately humble use of the delegate model, with partisan lean standing in
                  for issue-level constituent opinion — a measurable, disclosed simplification (see
                  <a href="#known-limitations" className="underline underline-offset-2 hover:text-matrix-green/70"> Known Limitations</a>
                  {" "}below, including why this measures the rate and direction of a member&apos;s
                  deviation rather than the distance between their position and their
                  constituency&apos;s, so it cannot yet positively credit representation achieved
                  through congruent loyalty). Note on composition: confirmation votes on
                  nominations make up a large share of recent Senate roll calls and count at
                  full weight — they are genuine, whipped party-line tests.
                </P>
                <P>
                  Before v4.2 this dimension was called Independent Voting and rewarded raw
                  defection; it also exempted party-line votes on policy areas related to a
                  member&apos;s top donor industries. That exemption is removed: donor
                  industries are not a proxy for state interests, and it shielded exactly
                  the votes most suspect for donor influence.
                </P>
                <P>
                  The score blends seat-relative vote alignment (80%, or 100% when
                  cosponsorship data is unavailable) with coalition breadth (20%, when
                  available). Coalition breadth asks whether a member also legislates for
                  the constituents who didn&apos;t vote for them: the rate at which they
                  attract cosponsors from the other party and lend their name to the other
                  party&apos;s bills, normalized to the chamber median (following the Lugar
                  Center Bipartisan Index method; Harbridge 2015). A below-median rate is,
                  since v6.8, discounted toward neutral the same way below-expected loyalty
                  already is above: in a safe seat, a narrow, mostly within-party coalition
                  may be faithful representation of the coalition that elected the member,
                  not a failure to represent — the discount fades to zero in a swing or
                  opposed seat, where a narrow coalition is genuinely legible evidence.
                  Through v6.4 this
                  dimension also included a Donor Independence component (25%, a
                  heuristic based on the money associated with donor-vote topical
                  overlaps) — removed in 2026-07 after finding it measured a close cousin
                  of the Funding Independence signal (both keyed off total money raised
                  and donor-industry concentration) while itself reducing to one of four
                  fixed values for 85% of senators, since no data source discloses
                  per-bill donor positions. Its freed weight now goes entirely to
                  seat-relative vote alignment. We follow the methodological caution of
                  Ansolabehere et al. (2003)
                  <Cite id="18">Ansolabehere et al. 2003</Cite>
                  {" "}in interpreting donation-vote correlations generally: correlation
                  does not prove causation.
                  <Cite id="5">Stratmann 2005</Cite>
                </P>
              </div>

              <div>
                <Label>Legislative Effectiveness (34%)</Label>
                <Gist>
                  measures whether a member is actually getting legislative work done —
                  introducing bills that matter, moving them through Congress, and building a
                  network of cosponsors other members trust. Introducing a substantive bill
                  earns real credit even before it passes, matching how political scientists
                  actually measure legislative productivity.
                </Gist>
                <P>
                  Measures whether a member is producing tangible legislative outcomes,
                  following Volden &amp; Wiseman&apos;s (2014)
                  <Cite id="34">Volden &amp; Wiseman 2014</Cite>
                  {" "}real published methodology: each sponsored bill is weighted by
                  significance (5x for substantive bills — S./H.R./joint resolutions;
                  1x for commemorative simple/concurrent resolutions) and credited
                  cumulatively across every stage it reaches — introducing a bill earns
                  real credit on its own, not just bills that go on to pass a chamber or
                  become law. Two components: bill significance &amp; advancement (70%) —
                  this cumulative stage-credit per congress served, compared against an
                  expected credit for a sponsor of this chamber/majority-minority status;
                  and legislative leadership (30%) — cosponsorship-network PageRank, see
                  below.
                </P>
                <P>
                  Because introduction itself earns credit, a member who sponsors many
                  substantive bills can score well even before any of them advance
                  further — this is Volden &amp; Wiseman&apos;s real design, not a bug: their
                  published methodology counts a bill&apos;s contribution at every stage it
                  reaches, and most sponsored bills never advance at all (our own corpus
                  measures Senate majority sponsors advancing bills at 3.6% vs. 2.4% for
                  minority sponsors; House 6.4% vs. 2.4%). The expected-credit baseline
                  accounts for that majority/minority gap, so scoring everyone against one
                  absolute threshold doesn&apos;t silently penalize whichever party is out of
                  power. The score explanation on each profile breaks the substantive-bill
                  count into introduced-only / advanced-further / became-law so the
                  volume-vs-advancement split is visible as real numbers.
                </P>
                <P>
                  Both the expected-credit baseline and the majority/minority-status
                  adjustment above are calibrated separately for the House and Senate
                  (v6.9) — the two chambers&apos; real bill volumes and advancement rates
                  genuinely differ, and comparing every member against one shared,
                  pooled-across-chambers figure previously understated House members&apos;
                  effectiveness and overstated the Senate&apos;s. See the{" "}
                  <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70">scoring changelog</a>{" "}
                  for the live-population numbers behind that fix.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Known Limitations ── */}
          <Section title="KNOWN LIMITATIONS &amp; DISCLOSURES" id="known-limitations">
            <P className="text-matrix-green/50 text-xs">
              Every item below is an open engineering problem, not a settled tradeoff we&apos;ve
              made peace with — several started as disclosures here and were later fixed
              outright (see the v6.8 entry below, and the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70">scoring changelog</a>{" "}
              for the full history). Where a limitation is fixable, we fix it and remove the
              disclosure. Where it isn&apos;t — no dataset exists, or fixing it would require an
              editorial judgment call the platform&apos;s no-hardcoded-conclusions rule resists —
              we name the specific reason why, so it can be revisited if that changes.
            </P>
            <Gist>
              Democratic and Republican senators finance their campaigns differently on
              average, so Funding Independence scores differ by party on average too — not
              because the formula treats parties differently, but because the underlying
              fundraising behavior really is different.
            </Gist>
            <P>
              <em className="text-matrix-green/80">Scores correlate with funding style,
              and funding style correlates with party.</em> In current data, Democratic
              senators take roughly half the PAC share of Republican senators (median
              ~10% vs ~17%) and raise about twice the small-donor share (~24% vs ~12%).
              Because Funding Independence measures those behaviors directly, average
              scores differ by party. The formulas are identical for everyone and contain
              no party term; the gap reflects measured funding behavior, not editorial
              judgment.
            </P>
            <Gist>
              a bigger campaign naturally looks more &quot;independent&quot; by percentage even
              with the same PAC dollars, simply because the total got bigger. We also check
              absolute PAC dollars, but no single number fully separates &quot;independent&quot;
              from &quot;big.&quot;
            </Gist>
            <P>
              <em className="text-matrix-green/80">Fundraising scale still matters.</em>{" "}
              Larger campaigns naturally have smaller PAC <em>shares</em> because PAC
              checks are legally capped while individual money is not. We mitigate this
              by scoring absolute PAC dollars alongside the share, but no single number
              fully separates &quot;independent&quot; from &quot;big.&quot;
            </P>
            <Gist>
              election cycles run different lengths for different members (and for the House
              vs. the Senate), so funding scores are technically comparing different-sized
              snapshots of time across members.
            </Gist>
            <P>
              <em className="text-matrix-green/80">Comparison windows differ by tenure
              and chamber.</em> Funding metrics cover a member&apos;s two most recent
              election periods — roughly 8 years for a veteran senator, 2 for a freshman,
              4 for House members — so cross-member comparisons weigh different spans of
              time.
            </P>
            <Gist>
              when we flag a donor whose industry overlaps with a vote, that shows where money
              and legislative activity intersect — it is not proof the donation influenced the
              vote.
            </Gist>
            <P>
              <em className="text-matrix-green/80">Donor-vote connections are semantic
              overlaps, not lobbying records.</em> They aggregate employee and PAC money
              associated with an organization and match it to vote topics by embedding
              similarity. They indicate where money and votes intersect; they do not
              establish influence.
            </P>
            <Gist>
              a senator whose donors cluster in one industry scores the same whether that
              industry is their state&apos;s home industry or an out-of-state special interest.
              That&apos;s deliberate, not an oversight — see below for why.
            </Gist>
            <P>
              <em className="text-matrix-green/80">Concentrated industry funding is scored
              as capture risk even when it plausibly reflects a state&apos;s real economic
              base.</em> A senator whose donations concentrate in, say, the auto industry in
              Michigan or agriculture in Kansas scores the same on Funding Independence&apos;s
              industry-concentration component as one captured by an unrelated
              out-of-state interest — this platform does not check whether a donor
              industry is also a major local employer. That is a deliberate choice, not an
              oversight: we considered and rejected a &quot;this industry matters to the
              state&quot; exemption for the same reason the v4.2 donor-industry voting
              exemption was removed (see the
              <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70"> scoring changelog</a>)
              — local economic dominance plausibly gives an industry <em>more</em> leverage
              over a senator, not less, so exempting it would weaken the signal exactly
              where large-scale capture is most consequential. No public dataset can
              separate &quot;this funding reflects genuine local interest&quot; from
              &quot;this funding is capture that happens to correlate with local economic
              weight&quot; — concentration is scored as risk, full stop, following the same
              industrial-organization logic (Rhoades 1993) the HHI metric is built on.
            </P>
            <Gist>
              we estimate what a senator&apos;s state &quot;expects&quot; from how the state votes
              for president overall, not opinion on the specific bill in front of them — a
              broad stand-in for local opinion, not a precise one. We looked for a better
              public alternative and didn&apos;t find one that wasn&apos;t itself stale or a black box
              (see below).
            </Gist>
            <P>
              <em className="text-matrix-green/80">Presidential-vote PVI doesn&apos;t capture
              issue-specific constituent opinion.</em> A senator&apos;s expected break rate
              (see Constituent Alignment above) is calibrated to how their state votes for
              president, not to opinion on the specific issue a given vote concerns — a
              state&apos;s presidential lean says little about, say, local opinion on public
              land use in Utah or water rights in Arizona. We looked for a real, freely
              available substitute: the best candidate found (Tausanovitch &amp; Warshaw&apos;s
              survey-based ideology estimates by district/state) still only produces a single
              composite left-right score, the same kind of proxy PVI already is — not
              per-issue opinion — and its public data is already several years stale. Actual
              issue-level constituent opinion at this scale would require building
              multilevel-regression-and-poststratification (MRP) modeling in-house over raw
              survey microdata: a statistics pipeline, not a lookup, and a genuine black box
              relative to every other formula on this page. We chose not to build one rather
              than trade this platform&apos;s auditability for a partial, hard-to-explain fix.
            </P>
            <Gist>
              the score can catch a senator who&apos;s clearly out of step with their state, but
              it can&apos;t yet give extra credit to a senator who is genuinely, provably in step
              with theirs — both look identical (a neutral score) today. This is on our list to
              fix; see below for exactly what&apos;s blocking it.
            </Gist>
            <P>
              <em className="text-matrix-green/80">Constituent Alignment still cannot positively
              credit congruent loyalty.</em> As of v6.7 the dimension can flag a below-expected
              loyalist whose <em>position</em> is a clear outlier for their seat (the
              position-mismatch discount, above) — but it still has no way to reward the mirror
              case: a senator whose party&apos;s positions already match a lopsided state, voting
              loyally in a way that is genuinely representative, scores the same neutral ~50 as
              an unreadable loyalist, not higher. Penalizing a legible mismatch is a lower
              evidentiary bar than crediting a legible match — the platform is confident an
              extreme position in an unsafe seat is not neutral, but is not yet confident enough
              in any target to say a given position affirmatively <em>is</em> what a seat wants.
              The political-science construct that would capture the positive case directly is
              positional distance — how close a member&apos;s revealed ideological position sits to
              their seat&apos;s expected position (Canes-Wrone, Brady &amp; Cogan 2002; Bafumi &amp;
              Herron 2010). The platform already computes both ingredients (a party-blind
              cosponsorship-ideology score and state partisan lean), so this is a data problem,
              not a data-availability one: it needs a party- or coalition-relative target — every
              member sits more extreme than their state&apos;s raw median, so the median itself is
              the wrong yardstick — and edges toward an authored benchmark this platform&apos;s
              no-hardcoded-conclusions rule resists. Disclosed here rather than papered over.
            </P>
            <Gist>
              two of the checks that can lower Constituent Alignment are both computed from
              the same underlying data, so they were catching the same problem twice. v6.8
              (below) cut that overlap substantially, but the two will never be fully separate
              signals with the data sources available here.
            </Gist>
            <P>
              <em className="text-matrix-green/80">The position-mismatch discount and coalition
              breadth are not fully independent signals.</em> A 2026-07-21 audit found the two
              components of Constituent Alignment&apos;s below-expected-loyalty case correlate at
              r=-0.76 (58% shared variance, n=99) — both are computed from the same underlying
              cosponsorship network (one is an SVD position on it, the other its cross-party edge
              rate), so a member with a narrow, within-party cosponsorship pattern moves both
              signals together rather than providing two genuinely separate pieces of evidence.
              v6.8 reduced the position-mismatch discount&apos;s strength and seat-safety-scaled
              coalition breadth&apos;s below-median case to cut how much this double-counted a
              single underlying fact, but the two measures remain drawn from the same data
              source and will never be fully orthogonal within this platform&apos;s current data —
              a genuinely independent second signal (e.g. a roll-call-based ideal point distinct
              from cosponsorship patterns) isn&apos;t available here. See the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70"> scoring changelog</a>{" "}
              for the full account.
            </P>
          </Section>

          {/* ── Sponsorship Analysis ── */}
          <Section title="SPONSORSHIP ANALYSIS (LEADERSHIP &amp; IDEOLOGY)">
            <P>
              Every senator and representative also receives two metrics derived from
              cosponsorship networks — the pattern of which members sign onto each
              other&apos;s bills (within each chamber&apos;s own network; House and Senate
              cosponsorship are separate graphs). Ideology is purely informational
              context. Legislative Leadership is
              <em className="text-matrix-green/80"> not</em> purely informational — it
              already feeds into Legislative Effectiveness above at 30% weight; the
              number shown on a member&apos;s card is the same underlying score, displayed
              directly (with a tenure adjustment, see below) rather than hidden inside
              the composite.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Legislative Leadership (0-100)</Label>
                <P>
                  Measures legislative influence using the PageRank algorithm
                  <Cite id="32">Brin &amp; Page 1998</Cite>
                  applied to cosponsorship networks. When Senator A cosponsors Senator B&apos;s
                  bill, that creates a directed link in the network. PageRank computes
                  centrality: a senator whose bills attract many cosponsors — especially from
                  other influential senators — receives a higher score. This mirrors
                  GovTrack&apos;s leadership methodology.
                  <Cite id="33">Tauberer 2012</Cite>
                </P>
                <P>
                  The algorithm uses power iteration with a damping factor of 0.85 and
                  converges in ~50 iterations. Raw PageRank values are rescaled to [0, 1]
                  using a logarithmic transformation to compress the heavy-tailed distribution,
                  then displayed as 0-100.
                </P>
                <P>
                  Network centrality structurally takes years to build — a freshman
                  senator&apos;s raw score is near-zero not because they lead poorly but
                  because they haven&apos;t had time to accumulate cosponsorship connections
                  yet. Both the score component and the displayed number shrink the raw
                  value toward neutral 50 for senators with under 6 years in office,
                  confidence-scaled to a full term, so a brand-new senator reads as
                  &quot;not enough track record yet&quot; rather than &quot;bad at
                  leadership.&quot;
                </P>
                <P>
                  Raw cosponsorship-network centrality can&apos;t on its own tell a
                  substantive bill from a message bill introduced with no real chance of
                  passing — a senator who signs onto ten symbolic resolutions accrued the
                  same network weight as one who cosponsors ten bills that actually became
                  law. Since v6.2, each cosponsorship is weighted by what happened to the
                  underlying bill: full weight if it became law, reduced weight if it
                  passed a chamber or cleared committee, and further reduced (not zeroed —
                  a stalled bill is still real evidence of a working relationship) if it
                  never advanced.
                </P>
              </div>

              <div>
                <Label>Ideology Score (0-1)</Label>
                <P>
                  Computes a behavioral ideological position using Singular Value Decomposition
                  (SVD) on the cosponsorship matrix, following Tauberer (2012).
                  <Cite id="33">Tauberer 2012</Cite>
                  The second singular vector (first is trivially related to overall activity)
                  captures the primary ideological dimension — the axis along which senators
                  most differ in who they cosponsor. This is analogous to DW-NOMINATE
                  <Cite id="20">Poole &amp; Rosenthal 1985</Cite>
                  but derived from cosponsorship patterns rather than roll-call votes.
                </P>
                <P>
                  The ideology score is oriented so that lower values correspond to progressive
                  positions and higher values to conservative positions, calibrated by checking
                  the mean score of each party. It serves as a Bayesian prior for the partisan
                  depth calculation: when a senator has few recorded votes, the ideology score
                  regularizes the estimate; as vote data accumulates, the prior weight drops
                  to zero.
                  <Cite id="19">Efron &amp; Morris 1975</Cite>
                </P>
              </div>

              <div>
                <Label>Sponsorship Description</Label>
                <P>
                  Combines the leadership and ideology scores into a human-readable label
                  (e.g., &quot;progressive Democratic leader&quot; or &quot;conservative Republican
                  backbencher&quot;). The label encodes three dimensions: ideological position
                  (progressive/moderate/conservative), party affiliation, and influence tier
                  (leader/rank-and-file/backbencher).
                </P>
              </div>
            </div>
          </Section>

          {/* ── President Metrics ── */}
          <Section title="PRESIDENTIAL SCORECARD METRICS">
            <P>
              Presidents are scored on four dimensions, 0-100 scale, computed entirely
              from live, historical, and expert-survey datasets — there is no hand-set
              or seeded score anywhere in this pipeline (2026-07 rewrite). A dimension a
              president has no real data source for is left blank (N/A) rather than
              filled with a fabricated or neutral placeholder, and the overall score
              renormalizes across whichever dimensions actually apply to that president.
              Identity data (name, party, term dates) is fetched live too, from the same
              UCSB roster used for the metrics below — nothing about a president&apos;s
              profile is typed into this codebase by hand.
            </P>
            <P>
              <em className="text-matrix-green/80">Independence and Follow-Through were
              removed entirely (2026-07)</em>, not just disclosed as limitations. Both
              were always a one-time hand-set number with no live formula and, unlike
              every dimension below, no realistic path to one: Independence&apos;s
              obvious data source (OpenSecrets&apos; cabinet/appointee revolving-door
              tracking) was itself discontinued in 2025, and Follow-Through would need
              the same platform-text-vs-action matching technique already tried four
              times and abandoned for senators&apos; Promise Persistence (see the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-matrix-green/70">scoring changelog</a>{" "}
              — v6.0). Rather than keep presenting a hand-set number as a computed
              score, they&apos;re gone. Their combined weight first redistributed
              proportionally across the remaining four (Public Mandate 15→23%,
              Effectiveness 20→31%, Competence 15→23%, Agency Alignment 15→23%), then a
              fifth dimension — Historical Legacy — was added shortly after (also
              2026-07, following review that found presidents like Lincoln landing in
              the bottom half of the ranking despite every individual number being
              defensible on its own terms: nothing in the first four dimensions could
              credit &ldquo;preserved the Union, ended slavery&rdquo; at all).
            </P>
            <P>
              Historical Legacy&apos;s weight went through two revisions before landing
              at 35%, both checked against the real 47-president dataset rather than
              picked by eye. Equal fifths (20%) let the other four dimensions — which
              individually barely correlate with historian judgment at all (Spearman
              0.17 between the four mechanical dimensions alone and C-SPAN&apos;s own
              ranking) — outvote the one dimension that actually tracks it, putting
              Coolidge, McKinley, and Harding in the top 10 while Lincoln and
              Eisenhower fell out of it. Raising Historical Legacy to 50% fixed that,
              but introduced a different problem: at 50%, this platform&apos;s overall
              ranking correlated 0.96 with simply using C-SPAN&apos;s own ranking
              alone — the four mechanical dimensions were contributing almost nothing
              of their own. 35% is the point where the top of the ranking is already
              recognizable (FDR, Washington, Lincoln, Theodore Roosevelt, JFK,
              Eisenhower) while the mechanical dimensions still meaningfully move the
              rest of the list (correlation to a pure C-SPAN ranking: 0.89, not 0.96).
              Coolidge and McKinley still edge into the bottom of the top 10 at this
              weight — a disclosed, arguable disagreement with C-SPAN&apos;s own
              ranking, not something we kept tuning the weight to paper over. Each
              president&apos;s page also shows how many of the 4 dimensions actually
              have a score for them (as few as 2, for a short-tenure or currently-
              serving president) — a score built from partial data is not shown with
              the same implied confidence as one built from all 4.
            </P>
            <P>
              A closer look at Coolidge&apos;s own numbers turned up a real hole in
              Competence (executive-order activity rate), the dimension covering
              administrative execution: Coolidge and Harding have nearly identical
              EO-rates (~216/year each), yet C-SPAN&apos;s own historians rate their
              actual administrative skill 596 vs. 334 (of 1000) — almost as far apart
              as two presidents get. Across all 44 rated presidents, EO-rate
              correlates just 0.097 (p=0.53) with C-SPAN&apos;s &ldquo;Administrative
              Skill&rdquo; category — statistically no different from noise. Using
              C-SPAN&apos;s Administrative Skill score directly instead wasn&apos;t a
              clean fix either: it&apos;s one of the ten categories C-SPAN itself sums
              into the same Final Score already driving Historical Legacy at 35%, so
              folding it into a second dimension would push this platform&apos;s true
              historian-derived weight toward ~51%, undoing the exact
              over-reliance-on-C-SPAN problem the 50%&rarr;35% revision above was
              built to avoid. Competence is removed entirely (2026-07) — same
              standard as Independence/Follow-Through: no defensible live signal, no
              fabricated one in its place. Its 16.25% is split evenly across the three
              remaining mechanical dimensions (21.67% each); Coolidge drops from the
              top 10 to #12, Harding to #26, McKinley to #17, while Lincoln and
              Eisenhower both stay in the top 10 — the same qualitative target that
              justified 35% still holds.
            </P>
            <P>
              A closer look at how that 35% actually gets applied found it wasn&apos;t
              the real operative number for most presidents. The renormalization used
              to spread flatly across whichever dimensions had data — so a president
              missing Agency Alignment (everyone before Clinton, ~36 of 47) had
              Historical Legacy&apos;s EFFECTIVE weight rise to ~44.7%, and the four
              non-elected successors missing both Agency Alignment and Public Mandate
              (Tyler, Fillmore, Arthur, Andrew Johnson) had it rise to ~61.8%. 35% was
              only the true weight for 4 of 47 presidents. This is fixed (2026-07):
              Historical Legacy is now held at exactly 35% whenever at least two
              mechanical dimensions are present, with the mechanical dimensions
              renormalizing only among themselves for the rest. Below that floor — a
              single mechanical dimension alone — it falls back to the old flat
              renormalization instead, since one number isn&apos;t reliable enough to
              carry 65% of a score by itself: Fillmore&apos;s Effectiveness is 100/100
              purely from a Gold-Rush-era GDP boom he had little to do with, which
              would have swapped his real (near-bottom, 19/100) historian rating for a
              top-10 placement under a flat 65% share. Re-checked against the real
              dataset under this corrected scheme: 35% still keeps Lincoln and
              Eisenhower in the top 10 and Coolidge/Harding/McKinley out of it, so the
              headline number didn&apos;t need to change — only how consistently it
              gets applied.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Public Mandate (21.67%)</Label>
                <P>
                  Reflects approval trajectory and coalition retention. Gallup, this
                  platform&apos;s original approval source, ended presidential approval
                  tracking entirely in February 2026 after 88 years; approval data now
                  comes from the American Presidency Project (presidency.ucsb.edu),
                  which is still updated for the sitting president, aggregating
                  AP-NORC/CNN-SSRS/Marist/Pew/Verasight. This covers every president
                  from Truman onward — 70% average approval over the term, 30% the
                  trend from term-start to term-end, both scored against real
                  population statistics computed from every president&apos;s actual
                  polling history. Presidents before Truman have no polling era at all,
                  so their Public Mandate uses UCSB&apos;s historical election-margin
                  data instead — the average margin of victory across their own
                  election win(s), the pre-polling-era proxy. The five presidents who
                  never won a presidential election in their own right have neither and
                  show N/A for this dimension, not a fabricated number.
                </P>
              </div>

              <div>
                <Label>Effectiveness (21.67%)</Label>
                <P>
                  Measures tangible economic outcomes: GDP growth (60%) and job
                  creation (40%). GDP growth is computed for the full presidency —
                  BEA/FRED for the modern era, MeasuringWorth&apos;s real-GDP series
                  (1790-present) for earlier presidents, both producing the same
                  &ldquo;average annual growth over the term&rdquo; figure, with the
                  term&apos;s first calendar year excluded when per-year data allows it
                  (that year mostly reflects the outgoing administration&apos;s
                  policy). Job creation comes from BLS nonfarm payroll data, which only
                  exists from 1939 onward — presidents before that are scored on GDP
                  growth alone, renormalized to 100% of the formula&apos;s weight, not
                  defaulted on the missing component.
                </P>
              </div>

              <div>
                <Label>Agency Alignment (21.67%)</Label>
                <P>
                  Measures how well executive agency actions align with stated
                  presidential priorities, via Federal Register rulemaking data — the
                  count of final and proposed rules published during the term, and what
                  fraction were finalized rather than left pending. This is a
                  digitization wall, not a conceptual one: notice-and-comment
                  rulemaking was a real, functioning practice well before the 1990s, but
                  no machine-readable record of it exists that far back — checked
                  directly (2026-07) rather than assumed: federalregister.gov&apos;s API
                  returns zero results for any pre-1994 president, and govinfo.gov&apos;s
                  own structured Federal Register data starts at year 2000. Earlier
                  issues exist only as scanned page images with no structured
                  document-type or agency tagging, and reconstructing rulemaking counts
                  from those would mean OCR&apos;ing and classifying decades of raw
                  scanned text — the same kind of unreliable pipeline already rejected
                  for Follow-Through and Competence&apos;s court-success-rate. Every
                  president before Clinton shows N/A for this dimension, excluded from
                  their overall score entirely rather than scored on a proxy.
                </P>
              </div>

              <div>
                <Label>Historical Legacy (35%)</Label>
                <P>
                  Covers what none of the other three dimensions can: crisis leadership,
                  moral authority, vision, and similar historical-consequence judgments
                  that don&apos;t reduce to GDP growth, approval polling, or rulemaking
                  volume. Sourced from C-SPAN&apos;s Presidential
                  Historians Survey — ~142 professional historians in the 2021 cycle
                  (the most recent; the 2025 cycle was explicitly postponed by C-SPAN,
                  citing the risk of turning &ldquo;historical analysis&rdquo; into
                  &ldquo;punditry&rdquo; with a former president returning to office),
                  scored across ten categories and aggregated into one point total. This
                  is categorically different from the hand-set Independence/Follow-
                  Through values removed elsewhere: a real, external, periodically-run
                  survey with a documented methodology, not a single number invented for
                  this platform — the same &ldquo;trust a well-documented external
                  institution&rdquo; category as citing BLS or Federal Register data,
                  just survey-based rather than administrative-record-based. Only rates
                  presidents whose terms were complete as of the 2021 cycle — every
                  currently-serving or just-departed president shows N/A here, genuinely
                  unrated by the survey&apos;s own cadence, not a fetch gap.
                </P>
                <P>
                  Being real, external, and methodologically documented does not make
                  this survey unbiased, and we don&apos;t present it as neutral ground
                  truth. Political scientists who study these historian-ranking surveys
                  have documented real, specific patterns in them: professional
                  historians as a field skew toward favoring presidents who expanded
                  federal/executive power, which plausibly inflates FDR, Wilson, and LBJ
                  relative to how a more ideologically mixed panel might rate them; and
                  historians are reluctant to rank very recent presidents at all until
                  enough distance has passed to assess their legacy, which is the direct
                  reason Obama and George W. Bush&apos;s scores may still be unsettled
                  and Biden and the current president have none. Weighting this survey
                  at 35% means this platform&apos;s ranking inherits those biases at
                  roughly that same strength, not zero.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Supreme Court ── */}
          <Section title="SUPREME COURT JUSTICE SCORECARDS">
            <P>
              Justices are scored on impartiality and ideological consistency using
              case-level voting data from the Oyez Project and official Supreme Court
              records. Case opinions link directly to the official supremecourt.gov
              slip opinion PDFs.
            </P>
            <P>
              Justice scoring evaluates whether a justice applies consistent legal
              principles across cases or shifts positions based on the political
              valence of the parties involved. This is analogous to the independence
              metric used for senators but adapted to the judicial context where party
              loyalty is replaced by jurisprudential consistency.
            </P>
          </Section>

          {/* ── Action Center ── */}
          <Section title="ACTION CENTER">
            <P>
              The Action Center surfaces the most important civic issues of the day
              using automated news analysis. It is designed to inform, not persuade —
              every summary is non-partisan and presents facts without editorial framing.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">NEWS ANALYSIS PIPELINE</h3>
                <P>
                  RSS feeds from eight news sources — AP News, NPR (Politics and World),
                  PBS NewsHour, BBC World, The Hill, Politico, and Roll Call — are parsed
                  hourly; opinion and editorial sections are filtered out of every feed.
                  Under common media-bias ratings this mix spans center to lean-left, with
                  no right-of-center outlet currently included — a disclosed limitation of
                  the source diet, not a neutral sample of all coverage. Each article is filtered
                  for U.S. policy relevance using embedding cosine similarity against policy
                  area prototypes — the same sentence-transformer model used throughout the
                  platform. Articles that pass the relevance threshold are clustered by semantic
                  similarity to group coverage of the same story across sources.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">TRENDING TOPIC INTEGRATION</h3>
                <P>
                  Clusters are ranked using a weighted combination of coverage breadth (40%) —
                  how many independent sources cover the story — and trending relevance (60%) —
                  whether the topic aligns with what the public is actively discussing. Trending
                  signals are drawn from Google Trends and policy-relevant Reddit communities,
                  cross-referenced with the news clusters via embedding similarity.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">NON-PARTISAN SUMMARIZATION</h3>
                <P>
                  The top-ranked issues are summarized by the LLM with explicit instructions
                  to present objective facts, avoid opinion or editorial framing, and recommend
                  actions that do not assume which side of an issue the reader supports.
                  Recommended actions include contacting representatives, attending public
                  hearings, and reviewing primary source documents — not advocating for or
                  against any policy position.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">CROSS-REFERENCING</h3>
                <P>
                  When a ranked politician is involved in a trending issue, the Action Center
                  links directly to their scorecard. Related government documents from the
                  Explore database are matched using semantic search. Source articles include
                  direct links to the original reporting.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">GOVERNMENT ACTIVITY TABS</h3>
                <P>
                  Dedicated tabs for all three branches of government — Legislative (Senate
                  and House), Executive, and Judicial — display the most recent government
                  documents: floor speeches, executive orders, proposed rules, court opinions,
                  and notices, pulled directly from the Explore database.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">NATIONAL MONITORS</h3>
                <P>
                  When an issue persists in the news across multiple days, the system
                  automatically creates a National Monitor — a dedicated tracking page
                  for that ongoing concern. Monitors build a sourced timeline of
                  developments, detect when separate news stories are facets of the same
                  underlying event using embedding similarity, and merge duplicate
                  monitors automatically. Monitors transition to &quot;watching&quot; status
                  when coverage subsides and reactivate when new developments appear.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">YEAR-IN-REVIEW TIMELINE</h3>
                <P>
                  Each day&apos;s top issue is permanently recorded in a timeline that
                  accumulates throughout the calendar year. The Timeline tab provides
                  a month-by-month chronological view of what mattered most, with
                  top policy themes calculated for each month and the year as a whole.
                  At year&apos;s end, this becomes a complete &quot;Year in Review&quot; of the
                  issues that shaped civic life.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">ELECTIONS TAB</h3>
                <P>
                  The Elections tab displays upcoming election dates, Senate races with
                  incumbent scores linked to their scorecards, and an interactive U.S.
                  map for selecting states. State-specific information helps users
                  understand their local races in the context of national trends.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">INTERACTIVE GLOBAL NEWS MAP</h3>
                <P>
                  The World tab features a 3D interactive globe that visualizes U.S.-related
                  international news coverage. Countries mentioned in current news feeds are
                  highlighted with points scaled by article count. Clicking a country scrolls
                  to recent headlines about U.S. relations with that nation, linking to the
                  original source articles.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Party Alignment ── */}
          <Section title="CONTENT-BASED PARTY ALIGNMENT">
            <P>
              A bill&apos;s partisan alignment is determined by analyzing <em className="text-matrix-green/80">what the bill
              does</em>, not how senators voted on it. This is a deliberate architectural
              decision grounded in political science methodology.
            </P>
            <P>
              The standard approach in political science — roll-call-based ideology
              estimation (DW-NOMINATE)
              <Cite id="20">Poole &amp; Rosenthal 1985</Cite>
              — assumes sincere voting. But as Clinton, Jackman &amp; Rivers (2004) note,
              this assumption is routinely violated by logrolling (vote trading), whip
              pressure, omnibus packaging, and tactical compromises.
              <Cite id="21">Clinton, Jackman &amp; Rivers 2004</Cite>
              A senator might vote for a bill they ideologically oppose to secure support
              for a different bill, or because party leadership made it a litmus test.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">HOW IT WORKS</h3>
                <P>
                  We implement a nearest-centroid classifier (Rocchio 1971)
                  <Cite id="22">Manning, Raghavan &amp; Schütze 2008</Cite>
                  in sentence-embedding space. Each party&apos;s known platform positions on
                  each policy area (taxes, healthcare, environment, etc.) are embedded as
                  centroids using the same sentence-transformer model used throughout the
                  pipeline. Bill text is then embedded and compared to both party centroids
                  via cosine similarity.
                </P>
                <P>
                  The <em className="text-matrix-green/80">stance direction</em> (pro/anti) disambiguates cases where both
                  parties have positions on the same topic: a &quot;pro&quot; environment bill
                  (strengthen EPA enforcement) aligns with the Democratic platform, while
                  an &quot;anti&quot; environment bill (roll back regulations) aligns with the
                  Republican platform. This encodes the saliency-plus-direction model
                  from manifesto research.
                  <Cite id="23">Laver &amp; Garry 2000</Cite>
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">TWO-SIGNAL FUSION</h3>
                <P>
                  Content analysis is the <em className="text-matrix-green/80">primary</em> signal for party alignment.
                  Vote tallies from roll-call data serve as a <em className="text-matrix-green/80">secondary</em> refinement.
                  When both agree, confidence is high. When they disagree, content wins
                  unless the vote data shows a clear party-line split (which is itself
                  informative — the bill was important enough to whip). This follows
                  Snyder &amp; Groseclose (2000) who demonstrated that vote outcomes reflect
                  party discipline as much as ideology.
                  <Cite id="24">Snyder &amp; Groseclose 2000</Cite>
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">ADAPTIVE LEARNING</h3>
                <P>
                  Platform position descriptions are seed prototypes bootstrapped from
                  published party platforms. As the pipeline processes bills, sponsor
                  party data from Congress.gov serves as supervised ground truth — bills
                  sponsored by a single party are labeled examples that refine the
                  classifier over time. This follows the self-training paradigm.
                  <Cite id="25">Yarowsky 1995</Cite>
                </P>
              </div>
            </div>
          </Section>

          {/* ── Classification Pipeline ── */}
          <Section title="CLASSIFICATION AND NLP PIPELINE">
            <P>
              The pipeline classifies thousands of entities (bills, donors, industries, votes)
              per run. We use a tiered strategy that reserves expensive techniques for cases
              where cheaper methods fail, following the computational parsimony principle.
              <Cite id="12">Jurafsky &amp; Martin 2023</Cite>
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">BILL POLICY AREA CLASSIFICATION</h3>
                <P>
                  Bills and votes are classified into 15 policy areas (healthcare, defense,
                  energy, etc.) using a tiered adaptive strategy:
                </P>
                <ul className="space-y-2 mt-2 text-sm text-matrix-green/60">
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">1.</span>
                    <span>
                      <span className="text-neon-pink/60">Learning store exact match</span> — bills
                      classified in prior pipeline runs are recalled instantly by ID.
                      This is the experience replay pattern.
                      <Cite id="10">Lin 1992</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">2.</span>
                    <span>
                      <span className="text-neon-pink/60">kNN against reference corpus</span> — the
                      k=7 most similar previously-classified bills in ChromaDB are retrieved
                      and the policy area is assigned by similarity-weighted majority vote.
                      <Cite id="9">Cover &amp; Hart 1967</Cite>
                      This is retrieval-augmented classification: the reference corpus
                      grows with each pipeline run, improving accuracy over time.
                      <Cite id="26">Lewis et al. 2020</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">3.</span>
                    <span>
                      <span className="text-neon-pink/60">Embedding similarity against policy descriptions</span> —
                      cosine similarity between bill text embeddings and pre-computed policy
                      area description embeddings. This is the cold-start fallback using
                      nearest-centroid classification.
                      <Cite id="7">Reimers &amp; Gurevych 2019</Cite>
                    </span>
                  </li>
                </ul>
                <P>
                  The policy taxonomy is based on the Congressional Research Service (CRS)
                  policy area scheme used by Congress.gov. The approach follows the text-as-data
                  paradigm reviewed in Grimmer &amp; Stewart (2013).
                  <Cite id="27">Grimmer &amp; Stewart 2013</Cite>
                  Stance derivation (pro/anti/neutral) uses embedding cosine similarity
                  against direction prototypes — the bill text is compared to semantic
                  signatures of supportive, restrictive, and reform-oriented legislative
                  language, following the Comparative Agendas Project coding tradition.
                  <Cite id="28">Baumgartner &amp; Jones 1993</Cite>
                  Zero LLM calls are used for bill classification.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">DONOR AND INDUSTRY CLASSIFICATION</h3>
                <P>
                  Donor classification uses a five-tier strategy:
                </P>
                <ul className="space-y-2 mt-2 text-sm text-matrix-green/60">
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">1.</span>
                    <span>
                      <span className="text-neon-pink/60">FEC metadata</span> — structured fields
                      from the Federal Election Commission API encode committee type and designation
                      codes, providing ground-truth classification for PACs vs. individual donors.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">2.</span>
                    <span>
                      <span className="text-neon-pink/60">Semantic detection</span> — embedding
                      cosine similarity against category prototypes replaces ~200 lines of
                      hardcoded string patterns. This generalizes to unseen entities because
                      distributed representations capture semantic meaning.
                      <Cite id="29">Bengio et al. 2003</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">3.</span>
                    <span>
                      <span className="text-neon-pink/60">Learning store lookup</span> — previously
                      classified entities are recalled instantly by name.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">4.</span>
                    <span>
                      <span className="text-neon-pink/60">Embedding cosine similarity</span> — donor
                      names are compared against pre-computed industry description embeddings.
                      Industry descriptions include exemplar company names as anchoring tokens,
                      following the zero-shot classification setup.
                      <Cite id="30">Yin, Hay &amp; Roth 2019</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">5.</span>
                    <span>
                      <span className="text-neon-pink/60">k-Nearest Neighbor (kNN)</span> — remaining
                      unclassified donors are classified by the k=7 most similar already-labeled
                      entities using distance-weighted majority voting.
                      <Cite id="9">Cover &amp; Hart 1967</Cite>
                      This mirrors prototypical networks for few-shot learning
                      <Cite id="14">Snell et al. 2017</Cite>
                      where classification is performed by comparing query embeddings to
                      accumulated real examples.
                    </span>
                  </li>
                </ul>
                <P>
                  The kNN approach was chosen over LLM-based classification after empirical
                  testing showed the LLM hallucinated invalid categories (producing labels like
                  &quot;SPORTS&quot; or &quot;RESTAURANT&quot; outside the valid taxonomy) and was
                  orders of magnitude slower. The kNN classifier processes ~5,000 donors in under
                  5 seconds versus 40+ minutes for the LLM, with more consistent results.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">LEARNING STORE AND ADAPTIVE CLASSIFICATION</h3>
                <P>
                  All classifications are persisted in a learning store (SQLite table) that
                  functions as an evolving knowledge base. On subsequent pipeline runs, previously
                  classified entities are retrieved instantly without recomputation. This is
                  analogous to experience replay in reinforcement learning
                  <Cite id="10">Lin 1992</Cite> — past decisions inform future ones,
                  improving both speed and accuracy over time.
                </P>
                <P>
                  The learning store also feeds into the self-training loop
                  <Cite id="25">Yarowsky 1995</Cite> — high-confidence classifications from
                  prior runs become labeled examples for kNN and reference corpus retrieval in
                  future runs. The system literally gets better each time the pipeline runs.
                </P>
                <P>
                  To prevent stale data from persisting when analysis algorithms are updated,
                  the pipeline implements <em className="text-matrix-green/80">version-aware
                  artifact management</em>. At the start of each run, a SHA-256 fingerprint of
                  all analysis source files is compared to the stored hash from the previous run.
                  If the code is unchanged, all learning data is preserved to promote self-training.
                  If the code has changed, stale artifacts (LLM results, learned classifications,
                  kNN reference corpus) are automatically cleared so updated algorithms start fresh.
                  The API cache (raw data from government APIs) is never cleared.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">SEMANTIC SEARCH (EXPLORE)</h3>
                <P>
                  The Explore feature uses dense passage retrieval
                  <Cite id="11">Karpukhin et al. 2020</Cite> to enable free-text search over
                  government documents (floor speeches, executive orders, bills). Documents are
                  chunked, embedded with Snowflake Arctic-XS, and stored in ChromaDB for approximate
                  nearest-neighbor retrieval. This outperforms keyword search (BM25) for
                  conceptual queries like &quot;climate policy&quot; where exact term overlap is low.
                </P>
              </div>
            </div>
          </Section>

          {/* ── AI Usage ── */}
          <Section title="HOW AI IS USED">
            <P>
              Civitas uses two types of AI models, each for the task it is best suited for.
              AI is never used to generate scores directly — all scores are computed by
              deterministic, auditable formulas.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">EMBEDDING MODEL (CLASSIFICATION + SEARCH)</h3>
                <P>
                  <Label>Snowflake Arctic-XS</Label> (22M parameters)
                  <Cite id="8">Wang et al. 2020</Cite>
                  handles all classification tasks: bill policy areas, donor industries,
                  party alignment, motion types, and semantic search retrieval.
                  Sentence-transformers produce dense vector representations where cosine
                  similarity correlates with semantic similarity
                  <Cite id="7">Reimers &amp; Gurevych 2019</Cite> — making them ideal for
                  classification-by-comparison tasks where category definitions exist.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">LLM (NARRATIVE SYNTHESIS)</h3>
                <P>
                  <Label>LFM2.5-1.2B-Instruct</Label> via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> handles tasks requiring natural
                  language understanding and multi-step reasoning:
                </P>
                <div className="space-y-2 mt-2">
                  <Row label="Campaign promise extraction" value="Parses platform text from senator websites to identify specific policy commitments and assess whether votes support or contradict them. House positions never touch the LLM: they are derived from sponsored bills and evaluated with deterministic embedding similarity only." />
                  <Row label="Voting pattern narrative" value="Generates human-readable summaries of a senator's voting patterns across policy areas" />
                  <Row label="Key vote reasoning" value="Explains why specific votes were flagged as significant given a senator's donor profile and party dynamics" />
                  <Row label="PAC identification" value="Identifies the parent organization and industry behind opaque PAC names using world knowledge" />
                  <Row label="Explore summaries" value="On-demand summaries of how a government document relates to a user's search query" />
                </div>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">WHAT AI DOES NOT DO</h3>
                <div className="space-y-2">
                  <Row label="Score calculation" value="All sub-scores use deterministic formulas with no LLM input. The math is fully auditable." />
                  <Row label="Bill classification" value="Policy areas, party alignment, and stance are all embedding-based — no LLM in the loop." />
                  <Row label="Donor classification" value="FEC metadata + embeddings + kNN handle all donor and industry classification." />
                  <Row label="Data fabrication" value="The LLM only analyzes data already fetched from official APIs. It does not generate or invent facts." />
                  <Row label="Partisan framing" value="Prompts are explicitly structured to avoid editorial framing. The LLM analyzes behavior, not ideology." />
                </div>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">WHY THESE TECHNIQUES WERE CHOSEN</h3>
                <P>
              We follow a strict hierarchy: structured metadata first, then embedding
                  similarity, then kNN, then LLM — reserving each more expensive technique only
                  for tasks the cheaper ones cannot handle. The pipeline contains zero hardcoded
                  keyword lists, regex patterns, or string-matching heuristics for classification
                  decisions. Every classification is made mathematically via embedding cosine
                  similarity against natural-language prototypes.
                  <Cite id="12">Jurafsky &amp; Martin 2023</Cite>
                </P>
                <P>
                  Embeddings (not LLM) for classification: sentence embeddings excel at
                  text classification tasks when labeled examples or category descriptions exist.
                  They are deterministic, fast, and avoid the hallucination risks inherent in
                  generative models.
                  <Cite id="13">Minaee et al. 2021</Cite>
                  The kNN classifier further leverages accumulated labeled data as a growing
                  reference set — a well-established approach in few-shot and
                  semi-supervised learning settings.
                  <Cite id="14">Snell et al. 2017</Cite>
                </P>
                <P>
                  Content analysis (not votes) for party alignment: roll-call votes confound
                  ideology with legislative strategy. Analyzing what a bill does relative to
                  published party platforms recovers ideological alignment more accurately,
                  following the manifesto analysis tradition.
                  <Cite id="31">Laver, Benoit &amp; Garry 2003</Cite>
                </P>
                <P>
                  LLM for narrative synthesis: tasks like promise-vote cross-referencing and PAC
                  identification require world knowledge and multi-step reasoning that
                  embeddings alone cannot provide. These are inherently generative tasks
                  suited to language models.
                  <Cite id="15">Wei et al. 2022</Cite>
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">MODEL AND ARCHITECTURE</h3>
                <P>
                  The inference model is <Label>LFM2.5-1.2B-Instruct</Label>, a compact
                  open-weight language model running natively via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> compiled with ARM-specific optimizations
                  (cortex-a76, dot-product, fp16). This provides faster inference compared
                  to containerized runtimes, generating ~14 tokens/second on the Raspberry Pi 5 CPU.
                  Results are cached in a local database so each unique analysis is computed at most once.
                </P>
                <P>
                  The embedding model is <Label>Snowflake Arctic-XS</Label>
                  <Cite id="8">Wang et al. 2020</Cite>, a 22M-parameter sentence transformer.
                  It handles all classification (bills, donors, industries, party alignment),
                  semantic search, and nearest-neighbor retrieval. Both models run entirely
                  on-device with no external API calls.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Data Sources ── */}
          <Section title="DATA SOURCES AND APIs">
            <P>
              All data is sourced from official US government APIs and public records.
              No data is purchased, scraped from paywalled sources, or fabricated.
            </P>

            <div className="space-y-2 mt-4">
              <h3 className="text-xs text-matrix-green/50 tracking-widest">CONGRESSIONAL DATA (SENATE &amp; HOUSE)</h3>
              <Row label="Congress.gov API" value="Bill text, voting records, member data, sponsored legislation, and bill sponsor party affiliation — both chambers" />
              <Row label="FEC API (fec.gov)" value="Campaign finance data: individual contributions, PAC donations, committee filings, disbursements, and committee type codes — both chambers" />
              <Row label="GovInfo API" value="Full bill text for policy area classification, Congressional Record floor proceedings for advocacy analysis — both chambers" />
              <Row label="Senate.gov" value="Official senator websites scraped for platform text and campaign promises, roll-call vote records with per-member votes — Senate only; House campaign promises are instead derived from sponsored legislation (see AI Usage above), since House platform text isn't available the same way" />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-matrix-green/50 tracking-widest">PRESIDENTIAL DATA</h3>
              <Row label="Federal Register API" value="Executive order counts and metadata from federalregister.gov (Clinton onward, no API key required)" />
              <Row label="BLS API" value="Bureau of Labor Statistics public API — total nonfarm employment payrolls for jobs-created calculations" />
              <Row label="C-SPAN Historians Survey" value="Presidential Historians Survey (2021) used as basis for historical president scoring" />
              <Row label="Gallup Historical Data" value="Average approval ratings for modern presidents (Truman onward)" />
              <Row label="BEA NIPA Tables" value="Bureau of Economic Analysis GDP growth data" />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-matrix-green/50 tracking-widest">EXPLORE FEATURE</h3>
              <Row label="Congressional Record (GovInfo)" value="Senate and House floor proceedings — speaker-attributed transcripts from daily CREC packages" />
              <Row label="Federal Register" value="Executive orders, presidential memoranda, and proclamations with full text and metadata" />
              <Row label="Semantic Search" value="Documents embedded with Snowflake Arctic-XS into ChromaDB for dense passage retrieval" />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-matrix-green/50 tracking-widest">SUPREME COURT DATA</h3>
              <Row label="Oyez Project API" value="Case metadata, justice votes, oral argument transcripts, and decision breakdowns" />
              <Row label="supremecourt.gov" value="Official slip opinion PDFs linked directly from case records" />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-matrix-green/50 tracking-widest">RATE LIMITING</h3>
              <P>
                The pipeline respects all API rate limits: Congress.gov at 1.2 requests/second,
                FEC at 0.25 req/s, GovInfo at 1.0 req/s, and BLS at 25 queries/day. Data is
                cached for 72 hours to minimize redundant API calls.
              </P>
            </div>
          </Section>

          {/* ── Environmental ── */}
          <Section title="ENVIRONMENTAL AND ETHICAL CONSIDERATIONS">
            <div className="space-y-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">LOCAL-FIRST ARCHITECTURE</h3>
                <P>
                  The entire Civitas stack runs on a single Raspberry Pi 5 (16GB RAM) with an
                  NVMe SSD. There are no cloud GPU instances, no third-party AI API calls, and
                  no data sent to external services for processing. The LLM, embedding model,
                  vector database, SQLite database, backend API, and frontend all run on the
                  same device.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">ENERGY FOOTPRINT</h3>
                <P>
                  A Raspberry Pi 5 draws approximately 5-12 watts under load. Running the full
                  data pipeline (100 senators, ~100 LLM calls) takes several hours but consumes
                  roughly the energy of a single LED light bulb. By comparison, a typical cloud
                  GPU instance (NVIDIA A100) draws 250-400 watts.
                  <Cite id="17">Patterson et al. 2021</Cite>
                  This project demonstrates that meaningful AI analysis does not require
                  industrial-scale compute. The trade-off is speed: what a cloud GPU processes in
                  minutes takes hours on a Pi. We consider that an acceptable trade for a
                  nightly batch pipeline.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">DATA PRIVACY</h3>
                <P>
                  No accounts, no cookies, no third-party analytics trackers, and no advertising
                  networks. The site does record anonymized visit counts on its own server, to
                  understand usage — a salted hash of (IP, browser, date) that rotates daily so the
                  same visitor is unrecoverable across days, plus per-page view counts. Raw IP
                  addresses and user agents are never stored. This data is never shared, sold, or
                  transmitted anywhere. All data displayed is derived exclusively from public
                  government, academic, and economic-history records. The only outbound network
                  requests are to official government APIs (congress.gov, fec.gov, api.bls.gov,
                  federalregister.gov) and, for presidential data with no government API equivalent,
                  UCSB&apos;s American Presidency Project (presidency.ucsb.edu) and MeasuringWorth&apos;s
                  historical GDP dataset (measuringworth.com).
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">OPEN-WEIGHT MODEL</h3>
                <P>
                  We deliberately chose LFM2.5, an open-weight model, over proprietary
                  alternatives like GPT-4 or Claude. This means: no per-token API costs that
                  could make the project financially unsustainable, no dependency on a third-party
                  company&apos;s continued service, full auditability of the model&apos;s behavior,
                  and no user queries or government data leaving the device.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">LIMITATIONS AND HONESTY</h3>
                <P>
                  This project has real limitations and we believe in stating them clearly:
                </P>
                <ul className="space-y-2 mt-2 text-sm text-matrix-green/60">
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>A 1.2B parameter model is less capable than larger models. It occasionally produces imprecise promise analysis. We mitigate this with caching, post-processing heuristics, and deterministic overrides where the model output can be verified against structured data.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>Presidential scoring used to include two dimensions, Independence and Follow-Through, that were a one-time hand-set number for every president with no live formula behind them at all. We removed both entirely (2026-07) rather than keep presenting a hand-set number as a computed score, and rebuilt every remaining dimension — plus each president&apos;s identity data — on real live and historical datasets, with no seeded or hand-typed fallback left anywhere in the pipeline. A third dimension, Competence, was later removed too: its only live component (executive-order activity rate) measured no relationship (Spearman 0.097) with real administrative-skill judgment. See the Presidents methodology below for the full account. Agency Alignment has no machine-readable rulemaking data before Clinton (a real digitization wall in the underlying government sources, checked directly rather than assumed) and shows N/A for earlier presidents rather than a proxy score.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>Correlation between donations and votes does not prove causation. A senator who receives PAC money and votes favorably may be doing so for policy reasons unrelated to the donation. We follow the methodological caution urged by Ansolabehere et al. (2003).<Cite id="18">Ansolabehere et al. 2003</Cite></span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>Content-based party alignment depends on the quality of platform position descriptions. While these are seeded from published party platforms and refined by sponsor data, edge cases involving bipartisan or cross-cutting legislation may be misclassified.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>FEC data has inherent reporting delays. Campaign finance filings may lag real-time donations by weeks or months.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>Embedding-based classification, while fast and consistent, lacks the world knowledge that a large model or human expert would bring. Edge cases involving shell companies or deliberately obscure entity names may be misclassified.</span>
                  </li>
                </ul>
              </div>
            </div>
          </Section>

          {/* ── Tech Stack ── */}
          <Section title="TECHNICAL STACK">
            <div className="space-y-2">
              <Row label="Hardware" value="Raspberry Pi 5 (16GB), NVMe SSD" />
              <Row label="Backend" value="Python 3.13, FastAPI, SQLAlchemy, SQLite" />
              <Row label="Frontend" value="Next.js 16, React 19, TypeScript, Tailwind CSS" />
              <Row label="Embedding Model" value="Snowflake Arctic-XS (22M params, Snowflake/HuggingFace)" />
              <Row label="LLM Runtime" value="llama.cpp (native ARM build), LFM2.5-1.2B-Instruct" />
              <Row label="Vector Database" value="ChromaDB (persistent, local)" />
              <Row label="Containers" value="Docker Compose (blue/green zero-downtime deploy via nginx)" />
              <Row label="Pipeline Schedule" value="Nightly at 3:00 AM via APScheduler" />
              <Row label="Data Caching" value="72-hour TTL with persistent SQLite cache" />
              <Row label="Learning Store" value="SQLite table for persistent classification memory, version-aware invalidation on code change" />
              <Row label="Pipeline Optimization" value="Producer-consumer threading: embedding prefetch overlaps LLM inference, context compression for prompts" />
              <Row label="API Pagination" value="Server-side paginated voting records with filter support" />
              <Row label="Sponsorship Analysis" value="PageRank (leadership) + SVD (ideology) on cosponsorship matrix" />
              <Row label="Classification" value="Zero hardcoded rules — all classifications via embedding similarity or kNN" />
              <Row label="Metric Tooltips" value="Every scorecard metric has a [?] tooltip explaining what it measures" />
              <Row label="Branches Covered" value="Senate (100), House (435), Presidents (historical + modern), Supreme Court (9 justices)" />
              <Row label="Action Center" value="Hourly news analysis with national monitors for ongoing concerns and year-in-review timeline tracking" />
              <Row label="News Sources" value="AP News, NPR (Politics + World), PBS NewsHour, BBC World, The Hill, Politico, Roll Call — opinion sections filtered; mix spans center to lean-left (no right-of-center outlet currently included)" />
              <Row label="Trending Integration" value="Google Trends RSS + Reddit policy subreddits, cross-referenced via embedding similarity" />
              <Row label="Globe Visualization" value="react-globe.gl — interactive 3D globe for international news mapping" />
            </div>
          </Section>

          {/* ── How It's Built ── */}
          <Section title="HOW IT'S BUILT">
            <P>
              Civitas is built and maintained by a single developer as a hobby project, running on
              a home server. It exists to prove that meaningful civic accountability tools do not
              require venture capital, a team of engineers, or enterprise cloud infrastructure.
              Anyone with the knowledge, time, and a modest machine can build something like this.
            </P>
            <div className="space-y-2 mt-4">
              <Row label="SERVER" value="Raspberry Pi 5 — a $80 single-board computer" />
              <Row label="LOCAL LLM" value="LFM2.5-1.2B-Instruct via Ollama/llama.cpp · runs entirely on-device, zero API cost" />
              <Row label="DATABASE" value="SQLite · no cloud database, no managed service" />
              <Row label="DEPLOYMENT" value="Blue-green via Docker Compose on a single machine" />
              <Row label="EXTERNAL APIs" value="Congress.gov, FEC.gov, Federal Register — all free and open" />
              <Row label="MONTHLY COST" value="~$5–10 (electricity)" />
              <Row label="CLOUD SERVICES" value="None" />
              <Row label="VENTURE CAPITAL" value="None" />
            </div>
            <P>
              The pipeline runs overnight, the site serves from a home IP address, and the entire
              codebase is documented above. If you want to build something similar, everything
              you need to know about the methodology is on this page.
            </P>
          </Section>

          {/* ── References ── */}
          <Section title="REFERENCES">
            <ol className="space-y-3">
              <Ref id="1">
                Bonica, A. (2014). Mapping the Ideological Marketplace. <em className="text-matrix-green/60">American Journal of Political Science</em>, 58(2), 367-386. doi:10.1111/ajps.12062
              </Ref>
              <Ref id="2">
                Naurin, E. (2011). <em className="text-matrix-green/60">Election Promises, Party Behaviour and Voter Perceptions</em>. Palgrave Macmillan. doi:10.1057/9780230304598
              </Ref>
              <Ref id="3">
                Martin, S. (2011). Using Parliamentary Questions to Measure Constituency Focus. <em className="text-matrix-green/60">Political Studies</em>, 59(2), 472-488. doi:10.1111/j.1467-9248.2011.00885.x
              </Ref>
              <Ref id="4">
                Carson, J. L., Koger, G., Lebo, M. J., &amp; Young, E. (2010). The Electoral Costs of Party Loyalty in Congress. <em className="text-matrix-green/60">American Journal of Political Science</em>, 54(3), 598-616. doi:10.1111/j.1540-5907.2010.00449.x
              </Ref>
              <Ref id="5">
                Stratmann, T. (2005). Some Talk: Money in Politics. A (Partial) Review of the Literature. <em className="text-matrix-green/60">Public Choice</em>, 124(1-2), 135-156. doi:10.1007/s11127-005-4750-3
              </Ref>
              <Ref id="6">
                Rhoades, S. A. (1993). The Herfindahl-Hirschman Index. <em className="text-matrix-green/60">Federal Reserve Bulletin</em>, 79, 188-189.
              </Ref>
              <Ref id="7">
                Reimers, N. &amp; Gurevych, I. (2019). Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks. <em className="text-matrix-green/60">Proceedings of EMNLP-IJCNLP 2019</em>, 3982-3992. doi:10.18653/v1/D19-1410
              </Ref>
              <Ref id="8">
                Wang, W., Wei, F., Dong, L., Bao, H., Yang, N., &amp; Zhou, M. (2020). MiniLM: Deep Self-Attention Distillation for Task-Agnostic Compression of Pre-Trained Transformers. <em className="text-matrix-green/60">Proceedings of NeurIPS 2020</em>. arXiv:2002.10957
              </Ref>
              <Ref id="9">
                Cover, T. &amp; Hart, P. (1967). Nearest Neighbor Pattern Classification. <em className="text-matrix-green/60">IEEE Transactions on Information Theory</em>, 13(1), 21-27. doi:10.1109/TIT.1967.1053964
              </Ref>
              <Ref id="10">
                Lin, L.-J. (1992). Self-improving reactive agents based on reinforcement learning, planning and teaching. <em className="text-matrix-green/60">Machine Learning</em>, 8(3-4), 293-321. doi:10.1007/BF00992699
              </Ref>
              <Ref id="11">
                Karpukhin, V., Oguz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., &amp; Yih, W. (2020). Dense Passage Retrieval for Open-Domain Question Answering. <em className="text-matrix-green/60">Proceedings of EMNLP 2020</em>, 6769-6781. doi:10.18653/v1/2020.emnlp-main.550
              </Ref>
              <Ref id="12">
                Jurafsky, D. &amp; Martin, J. H. (2023). <em className="text-matrix-green/60">Speech and Language Processing</em> (3rd ed. draft). Stanford University.
              </Ref>
              <Ref id="13">
                Minaee, S., Kalchbrenner, N., Cambria, E., Nikzad, N., Chenaghlu, M., &amp; Gao, J. (2021). Deep Learning-Based Text Classification: A Comprehensive Review. <em className="text-matrix-green/60">ACM Computing Surveys</em>, 54(3), 1-40. doi:10.1145/3439726
              </Ref>
              <Ref id="14">
                Snell, J., Swersky, K., &amp; Zemel, R. (2017). Prototypical Networks for Few-Shot Learning. <em className="text-matrix-green/60">Proceedings of NeurIPS 2017</em>, 4077-4087. arXiv:1703.05175
              </Ref>
              <Ref id="15">
                Wei, J., Wang, X., Schuurmans, D., Bosma, M., Ichter, B., Xia, F., Chi, E., Le, Q., &amp; Zhou, D. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models. <em className="text-matrix-green/60">Proceedings of NeurIPS 2022</em>. arXiv:2201.11903
              </Ref>
              <Ref id="16">
                Gerganov, G. (2023). llama.cpp: Inference of LLaMA model in pure C/C++. GitHub. github.com/ggerganov/llama.cpp
              </Ref>
              <Ref id="17">
                Patterson, D., Gonzalez, J., Le, Q., Liang, C., Munguia, L.-M., Rothchild, D., So, D., Texier, M., &amp; Dean, J. (2021). Carbon Emissions and Large Neural Network Training. <em className="text-matrix-green/60">arXiv:2104.10350</em>.
              </Ref>
              <Ref id="18">
                Ansolabehere, S., de Figueiredo, J. M., &amp; Snyder, J. M. (2003). Why Is There So Little Money in U.S. Politics? <em className="text-matrix-green/60">Journal of Economic Perspectives</em>, 17(1), 105-130. doi:10.1257/089533003321164976
              </Ref>
              <Ref id="19">
                Efron, B. &amp; Morris, C. (1975). Data Analysis Using Stein&apos;s Estimator and Its Generalizations. <em className="text-matrix-green/60">Journal of the American Statistical Association</em>, 70(350), 311-319. doi:10.2307/2285814
              </Ref>
              <Ref id="20">
                Poole, K. T. &amp; Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call Analysis. <em className="text-matrix-green/60">American Journal of Political Science</em>, 29(2), 357-384. doi:10.2307/2111172
              </Ref>
              <Ref id="21">
                Clinton, J., Jackman, S., &amp; Rivers, D. (2004). The Statistical Analysis of Roll Call Data. <em className="text-matrix-green/60">American Political Science Review</em>, 98(2), 355-370. doi:10.1017/S0003055404001194
              </Ref>
              <Ref id="22">
                Manning, C. D., Raghavan, P., &amp; Schütze, H. (2008). <em className="text-matrix-green/60">Introduction to Information Retrieval</em>. Cambridge University Press. Ch. 14: Vector Space Classification.
              </Ref>
              <Ref id="23">
                Laver, M. &amp; Garry, J. (2000). Estimating Policy Positions from Political Texts. <em className="text-matrix-green/60">American Journal of Political Science</em>, 44(3), 619-634. doi:10.2307/2669268
              </Ref>
              <Ref id="24">
                Snyder, J. M. &amp; Groseclose, T. (2000). Estimating Party Influence in Congressional Roll-Call Voting. <em className="text-matrix-green/60">American Journal of Political Science</em>, 44(2), 193-211. doi:10.2307/2669305
              </Ref>
              <Ref id="25">
                Yarowsky, D. (1995). Unsupervised Word Sense Disambiguation Rivaling Supervised Methods. <em className="text-matrix-green/60">Proceedings of ACL 1995</em>, 189-196. doi:10.3115/981658.981684
              </Ref>
              <Ref id="26">
                Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., Küttler, H., Lewis, M., Yih, W., Rocktäschel, T., Riedel, S., &amp; Kiela, D. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. <em className="text-matrix-green/60">Proceedings of NeurIPS 2020</em>. arXiv:2005.11401
              </Ref>
              <Ref id="27">
                Grimmer, J. &amp; Stewart, B. M. (2013). Text as Data: The Promise and Pitfalls of Automatic Content Analysis Methods for Political Texts. <em className="text-matrix-green/60">Political Analysis</em>, 21(3), 267-297. doi:10.1093/pan/mps028
              </Ref>
              <Ref id="28">
                Baumgartner, F. R. &amp; Jones, B. D. (1993). <em className="text-matrix-green/60">Agendas and Instability in American Politics</em>. University of Chicago Press.
              </Ref>
              <Ref id="29">
                Bengio, Y., Ducharme, R., Vincent, P., &amp; Jauvin, C. (2003). A Neural Probabilistic Language Model. <em className="text-matrix-green/60">Journal of Machine Learning Research</em>, 3, 1137-1155.
              </Ref>
              <Ref id="30">
                Yin, W., Hay, J., &amp; Roth, D. (2019). Benchmarking Zero-shot Text Classification: Datasets, Evaluation and Entailment Approach. <em className="text-matrix-green/60">Proceedings of EMNLP 2019</em>, 3914-3923. doi:10.18653/v1/D19-1404
              </Ref>
              <Ref id="31">
                Laver, M., Benoit, K., &amp; Garry, J. (2003). Extracting Policy Positions from Political Texts Using Words as Data. <em className="text-matrix-green/60">American Political Science Review</em>, 97(2), 311-331. doi:10.1017/S0003055403000698
              </Ref>
              <Ref id="32">
                Brin, S. &amp; Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search Engine. <em className="text-matrix-green/60">Proceedings of the 7th International World Wide Web Conference</em>, 107-117.
              </Ref>
              <Ref id="33">
                Tauberer, J. (2012). <em className="text-matrix-green/60">Open Government Data: The Book</em>. GovTrack.us methodology for ideology and leadership scoring via cosponsorship analysis. govtrack.us/about/analysis
              </Ref>
              <Ref id="34">
                Volden, C. &amp; Wiseman, A. E. (2014). <em className="text-matrix-green/60">Legislative Effectiveness in the United States Congress: The Lawmakers</em>. Cambridge University Press.
              </Ref>
            </ol>
          </Section>

          <div className="text-center mt-8">
            <p className="text-matrix-green/30 text-xs">
              Questions about our methodology? Disagree with a score? We welcome scrutiny.
              This project is built on the belief that transparency is non-negotiable.
            </p>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
