import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import TerminalTitlebar from "@/components/TerminalTitlebar";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="terminal-window mb-6">
      <TerminalTitlebar title={`${title.toLowerCase().replace(/ /g, "_")}.txt`} />
      <div className="p-6 space-y-4">
        <h2 className="text-neon-cyan font-terminal text-sm tracking-widest">{title}</h2>
        {children}
      </div>
    </section>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-matrix-green/70 leading-relaxed">{children}</p>;
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
              Scores reflect observable behavior — voting patterns, funding sources,
              legislative activity — not ideology. A senator who votes with their party 100%
              of the time receives a lower independence score regardless of whether they are
              a Democrat or Republican. The system is designed to be structurally non-partisan.
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

          {/* ── Senate Metrics ── */}
          <Section title="SENATE SCORECARD METRICS">
            <P>
              Each senator receives five sub-scores on a 0-100 scale, weighted into an
              overall Representation Score. Higher is better.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Funding Independence (25%)</Label>
                <P>
                  Measures three dimensions: (1) PAC dependency — a blend of the share of
                  funding from PACs and the absolute PAC dollars received, so that very
                  large campaigns cannot dilute significant PAC money into a tiny
                  percentage (PAC checks are legally capped; individual money is not);
                  (2) the share of funding from small (&lt;$200, unitemized) donors — the
                  broadest possible funding base; and (3) relative top-donor
                  concentration — what fraction of the itemized external donor pool comes
                  from the top 10 donors, with the senator&apos;s own money and transfers from
                  their own committees excluded. PAC dependency follows Stratmann (2005),
                  <Cite id="5">Stratmann 2005</Cite>
                  who found that PAC contributions are more strongly correlated with
                  roll-call alignment than individual contributions. The concentration
                  component applies the same intuition as HHI at the donor level,
                  following Bonica (2014).
                  <Cite id="1">Bonica 2014</Cite>
                </P>
              </div>

              <div>
                <Label>Promise Persistence (20%)</Label>
                <P>
                  Tracks whether a senator&apos;s voting record aligns with their stated campaign
                  promises. Platform text is extracted from official senate.gov websites and
                  analyzed to identify key commitments. Votes are then cross-referenced against
                  those promises using semantic search to find relevant legislation.
                  <Cite id="2">Naurin 2011</Cite>
                </P>
                <P>
                  A confidence penalty is applied when few promises are evaluable: if only
                  1 of 10 promises could be checked against votes, the score blends toward
                  50 (neutral) rather than being inflated by a single data point. This
                  implements Bayesian shrinkage toward the prior.
                  <Cite id="19">Efron &amp; Morris 1975</Cite>
                </P>
                <P>
                  This metric also incorporates floor advocacy analysis — whether
                  a senator actively speaks on the Senate floor about their promised issues,
                  parsed from Congressional Record proceedings. This captures effort that
                  voting records miss: in a gridlocked Senate, a senator may not get bills
                  to a vote but can still demonstrate persistence through floor speeches.
                  The floor advocacy component is weighted at 15% of the promise score,
                  following research on legislative speech as a signal of commitment.
                  <Cite id="3">Martin 2011</Cite>
                </P>
              </div>

              <div>
                <Label>Independent Voting (20%)</Label>
                <P>
                  Measures willingness to break with party leadership on votes that are not
                  explained by constituent interests. We identify state-relevant policy areas
                  by analyzing the senator&apos;s top donor industries (which serve as a proxy for
                  the state&apos;s economic composition). Party-line votes on state-relevant issues
                  are excluded from the independence penalty because they may reflect genuine
                  constituent representation rather than blind party loyalty.
                  <Cite id="4">Carson et al. 2010</Cite>
                </P>
                <P>
                  The score is adjusted by state partisan lean using Cook PVI as a proxy:
                  a senator in a safe R+20 state voting with their party may be representing
                  constituents, not following orders. Raw break rates are misleading without
                  this contextual adjustment. Note on composition: confirmation votes on
                  nominations make up a large share of recent Senate roll calls, so
                  measured independence reflects both legislation and nominations —
                  both are genuine, whipped party-line tests.
                </P>
                <P>
                  The score blends two components: party independence (75%) — the rate of
                  breaking with the party on non-state-relevant votes — and donor
                  independence (25%) — a heuristic based on the money associated with
                  donor-vote topical overlaps. We follow the methodological caution of
                  Ansolabehere et al. (2003)
                  <Cite id="18">Ansolabehere et al. 2003</Cite>
                  in interpreting donation-vote correlations: correlation does not prove causation.
                  <Cite id="5">Stratmann 2005</Cite>
                </P>
              </div>

              <div>
                <Label>Funding Diversity (15%)</Label>
                <P>
                  Evaluates how traceable and diverse a senator&apos;s funding sources are.
                  The score blends donor traceability (50%) — the fraction of funding from
                  itemized ({">"}$200), disclosed sources versus anonymous small-dollar
                  contributions — with industry diversity (50%), measured as the inverse
                  Herfindahl-Hirschman Index (HHI) of industry donations. HHI is a standard
                  concentration metric from industrial organization economics;
                  <Cite id="6">Rhoades 1993</Cite>
                  in this context, funding concentrated in a single industry suggests
                  potential regulatory capture, while broad funding suggests diverse
                  constituent support.
                </P>
              </div>

              <div>
                <Label>Legislative Effectiveness (20%)</Label>
                <P>
                  Measures how effective a senator is at advancing legislation. The score
                  combines bill passage rates, cosponsorship network influence (PageRank),
                  and ability to move bills through committee and floor consideration.
                  Higher scores indicate senators who successfully shepherd bills into law
                  and attract bipartisan cosponsorship.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Known Limitations ── */}
          <Section title="KNOWN LIMITATIONS &amp; DISCLOSURES">
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
            <P>
              <em className="text-matrix-green/80">Fundraising scale still matters.</em>{" "}
              Larger campaigns naturally have smaller PAC <em>shares</em> because PAC
              checks are legally capped while individual money is not. We mitigate this
              by scoring absolute PAC dollars alongside the share, but no single number
              fully separates &quot;independent&quot; from &quot;big.&quot;
            </P>
            <P>
              <em className="text-matrix-green/80">Comparison windows differ by tenure
              and chamber.</em> Funding metrics cover a member&apos;s two most recent
              election periods — roughly 8 years for a veteran senator, 2 for a freshman,
              4 for House members — so cross-member comparisons weigh different spans of
              time.
            </P>
            <P>
              <em className="text-matrix-green/80">Donor-vote connections are semantic
              overlaps, not lobbying records.</em> They aggregate employee and PAC money
              associated with an organization and match it to vote topics by embedding
              similarity. They indicate where money and votes intersect; they do not
              establish influence.
            </P>
          </Section>

          {/* ── House Representatives ── */}
          <Section title="HOUSE REPRESENTATIVE SCORECARDS">
            <P>
              All 435 House representatives are scored using the same five-metric
              framework as the Senate: Funding Independence, Promise Persistence,
              Independent Voting, Funding Diversity, and Legislative Effectiveness. The data sources (FEC,
              Congress.gov, GovInfo) and classification techniques are identical,
              ensuring consistent, comparable scores across both chambers.
            </P>
            <P>
              House members are sourced from the same Congress.gov API and FEC
              endpoints. The pipeline processes representatives in the same nightly
              run as senators, using the same embedding-based classification,
              content-based party alignment, and deterministic scoring formulas.
              The House leaderboard supports pagination and party filtering to
              navigate the larger membership.
            </P>
          </Section>

          {/* ── Sponsorship Analysis ── */}
          <Section title="SPONSORSHIP ANALYSIS (LEADERSHIP &amp; IDEOLOGY)">
            <P>
              Beyond the four scored metrics, each senator receives two informational
              metrics derived from cosponsorship networks — the pattern of which senators
              sign onto each other&apos;s bills. These metrics are not part of the Representation
              Score but provide additional context about a senator&apos;s role and positioning.
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
              Presidents are scored on six dimensions, also 0-100 scale. Historical
              presidents (pre-Clinton) use static scores derived from the C-SPAN
              Presidential Historians Survey, Gallup approval records, and BEA/BLS
              economic data. Recent presidents (Clinton onward) have scores partially
              computed from live API data.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Independence (15%)</Label>
                <P>
                  Assesses cabinet and advisor independence from corporate and lobbyist
                  influence. Based on historical analysis of cabinet compositions — how
                  many appointees came from industry versus public service backgrounds.
                  Currently uses curated seed data; automated analysis is planned.
                </P>
              </div>

              <div>
                <Label>Follow-Through (20%)</Label>
                <P>
                  Measures the ratio of campaign promises to executive and legislative
                  action. Based on historian assessments and promise-tracking analysis.
                  Currently uses curated seed data for historical presidents.
                </P>
              </div>

              <div>
                <Label>Public Mandate (15%)</Label>
                <P>
                  Reflects approval trajectory and coalition retention. For modern
                  presidents (Truman onward), this is grounded in Gallup average approval
                  ratings. Pre-Gallup presidents are scored based on election margins and
                  historian consensus.
                </P>
              </div>

              <div>
                <Label>Effectiveness (20%) — Partially Dynamic</Label>
                <P>
                  Measures tangible economic outcomes: GDP growth and job creation. The
                  pipeline fetches real employment data from the Bureau of Labor Statistics
                  API (nonfarm payroll series CES0000000001) and calculates net jobs created
                  during each term. GDP growth averages are seeded from BEA National Income
                  and Product Accounts tables. The formula weights GDP at 60% and job
                  creation at 40%, normalized against post-WWII historical averages.
                </P>
              </div>

              <div>
                <Label>Competence (15%) — Partially Dynamic</Label>
                <P>
                  Evaluates administrative execution quality. The pipeline fetches executive
                  order counts from the Federal Register API (federalregister.gov) for each
                  presidential term. The score blends court success rate (40%), cabinet
                  stability (30%), and EO activity rate (30%), where a moderate rate of
                  executive action scores higher than extremes in either direction.
                </P>
              </div>

              <div>
                <Label>Agency Alignment (15%)</Label>
                <P>
                  Measures how well executive agency actions align with stated
                  presidential priorities. Evaluates whether federal agencies
                  pursue the policy agenda the president campaigned on, based on
                  regulatory actions and executive directives. Currently uses
                  curated seed data; automated regulatory tracking is planned.
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
                  RSS feeds from editorially independent, low-bias news sources (AP News,
                  NPR Politics, Reuters, PBS NewsHour) are parsed hourly. Each article is filtered
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
                  <Label>DeepSeek-R1 1.5B</Label> via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> handles tasks requiring natural
                  language understanding and multi-step reasoning:
                </P>
                <div className="space-y-2 mt-2">
                  <Row label="Campaign promise extraction" value="Parses platform text from senator websites to identify specific policy commitments and assess whether votes support or contradict them" />
                  <Row label="Voting pattern narrative" value="Generates human-readable summaries of a senator's voting patterns across policy areas" />
                  <Row label="Key vote reasoning" value="Explains why specific votes were flagged as significant given a senator's donor profile and party dynamics" />
                  <Row label="PAC identification" value="Identifies the parent organization and industry behind opaque PAC names using world knowledge" />
                  <Row label="Explore summaries" value="On-demand summaries of how a government document relates to a user's search query" />
                </div>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">WHAT AI DOES NOT DO</h3>
                <div className="space-y-2">
                  <Row label="Score calculation" value="All five sub-scores use deterministic formulas with no LLM input. The math is fully auditable." />
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
                  The inference model is <Label>DeepSeek-R1 1.5B</Label>, a compact open-weight
                  language model running natively via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> compiled with ARM-specific optimizations
                  (cortex-a76, dot-product, fp16). This provides ~3x faster inference compared
                  to containerized runtimes, generating ~8 tokens/second on the Raspberry Pi 5 CPU.
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
              <h3 className="text-xs text-matrix-green/50 tracking-widest">SENATE DATA</h3>
              <Row label="Congress.gov API" value="Bill text, voting records, member data, sponsored legislation, and bill sponsor party affiliation" />
              <Row label="FEC API (fec.gov)" value="Campaign finance data: individual contributions, PAC donations, committee filings, disbursements, and committee type codes" />
              <Row label="GovInfo API" value="Full bill text for policy area classification, Congressional Record floor proceedings for advocacy analysis" />
              <Row label="Senate.gov" value="Official senator websites scraped for platform text and campaign promises, roll-call vote records with per-member votes" />
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
                  No user data is collected, stored, or transmitted. The site does not use cookies,
                  analytics trackers, or advertising networks. All data displayed is derived
                  exclusively from public government records. The only network requests are to
                  official government APIs (congress.gov, fec.gov, api.bls.gov, federalregister.gov).
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">OPEN-WEIGHT MODEL</h3>
                <P>
                  We deliberately chose DeepSeek-R1, an open-weight model, over proprietary
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
                    <span>A 1.5B parameter model is less capable than larger models. It occasionally produces imprecise promise analysis. We mitigate this with caching, post-processing heuristics, and deterministic overrides where the model output can be verified against structured data.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>Historical presidential scores are editorially curated based on historian surveys, not computed from raw data. We are transparent about this distinction.</span>
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
              <Row label="LLM Runtime" value="llama.cpp (native ARM build), DeepSeek-R1 1.5B" />
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
              <Row label="News Sources" value="AP News, NPR Politics, Reuters, PBS NewsHour — editorially independent, low-bias wire services" />
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
              <Row label="LOCAL LLM" value="DeepSeek-R1 1.5B via Ollama/llama.cpp · runs entirely on-device, zero API cost" />
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
