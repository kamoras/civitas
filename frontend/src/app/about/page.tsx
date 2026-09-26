import { pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";

export const metadata = pageMetadata({
  title: "About: How Civitas Scores Congress",
  description:
    "How Civitas scores members of Congress: data sources, funding independence, constituent alignment, and legislative effectiveness formulas — deterministic, auditable, and nonpartisan.",
  path: "/about",
});

// Collapsed by default (2026-08). Reader feedback that the site reads dense
// was partly about volume, not just spacing: this page measured 10,766 words
// across 102 paragraphs — roughly 33 screens of continuous scrolling, with a
// single 588-word paragraph and 48 paragraphs over 60 words. Every other page
// on the site measures under ~1,000. The 2026-07 pass at this same complaint
// added the `Gist` lead-ins, which help per section but made the page longer
// overall; nothing ever collapsed the technical depth underneath them.
//
// Nothing is removed — this is a methodology page and every word of it stays
// one click away, which is the point of publishing it. Collapsing turns the
// wall into a 15-item index a reader can actually navigate.
//
// Native <details> rather than the shared CollapsibleSection: that one is a
// client component built around useState, and this page is a server
// component with no other interactivity. <details> keeps it that way, and
// brings keyboard support and find-in-page expansion for free.
//
// `defaultOpen` covers two cases: the lead section, so the page never opens
// on a bare list of closed boxes, and any section carrying an `id`, because
// that id is a deep-link target (the leaderboard links to
// /about#known-limitations) and fragment-navigation auto-expansion of a
// closed <details> is too recent to rely on across browsers.
function Section({
  title,
  children,
  id,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  id?: string;
  defaultOpen?: boolean;
}) {
  return (
    <section className="panel mb-6" id={id}>
      <details className="group" open={defaultOpen || Boolean(id)}>
        <summary className="p-6 flex items-center gap-3 cursor-pointer list-none [&::-webkit-details-marker]:hidden hover:bg-white/[0.03] transition-colors">
          <span
            aria-hidden="true"
            className="text-ink-min font-mono text-sm group-hover:text-phos transition-colors"
          >
            <span className="group-open:hidden">+</span>
            <span className="hidden group-open:inline">−</span>
          </span>
          <h2 className="text-signal-cyan font-mono text-sm tracking-widest">{title}</h2>
        </summary>
        <div className="px-6 pb-6 space-y-4">{children}</div>
      </details>
    </section>
  );
}

function P({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <p className={`text-base text-ink leading-relaxed ${className}`}>{children}</p>;
}

// Plain-language lead-in for a P block below it: one or two jargon-free
// sentences stating what a section means in practice, before the full
// technical explanation (citations, formulas, edge cases) for readers who
// want the depth. Added 2026-07 after feedback that the methodology page's
// prose — accurate, but dense with citations and terms like "SVD" or "PVI"
// — wasn't readable for a non-technical visitor on its own.
function Gist({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-base text-signal-cyan leading-relaxed font-medium">
      <span className="text-signal-amber">In short:</span> {children}
    </p>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-ink-lo font-mono">{children}</span>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-3 text-sm">
      <span className="text-signal-amber font-mono shrink-0 sm:w-56">{label}</span>
      <span className="text-ink-lo">{value}</span>
    </div>
  );
}

function Cite({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <span className="text-ink-min text-xs">
      {" "}
      [
      <a href={`#ref-${id}`} className="text-ink-lo hover:text-phos transition-colors">
        {id}
      </a>
      ] <span className="sr-only">{children}</span>
    </span>
  );
}

function Ref({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <li id={`ref-${id}`} className="flex items-start gap-2 text-xs text-ink-lo leading-relaxed">
      <span className="text-ink-lo shrink-0">[{id}]</span>
      <span>{children}</span>
    </li>
  );
}

export default function AboutPage() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        {/* `font-sans` because this is the reading, not the data.

            The body element is `font-mono`, so anything that does not name a
            face inherits Share Tech Mono — which on these four documents meant
            103 of 105 paragraphs on /about alone. That contradicts the rule
            the palette states outright: "mono is data, IDs, timestamps, labels
            and status — the terminal voice, now meaning something because it
            is no longer also the body face" (tailwind.config.ts). It was still
            the body face here.

            Set on the container rather than on the `P` helper so raw `<p>`,
            `<li>` and inline prose are covered too. Everything that should
            stay mono on these pages — section headings, `Label`, `Row`'s key
            column, the +/- markers — already declares `font-mono` and still
            wins. The global default is left alone deliberately: the dense data
            surfaces were measured against Share Tech Mono's advance width (see
            the note in layout.tsx), and flipping it wholesale is a different
            change from fixing the prose. */}
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-10"
            eyebrow="Methodology · how every score is calculated"
            title="Methodology"
          >
            <p>
              Full transparency on how scores are calculated, where data comes from, and why each
              analytical technique was chosen. No black boxes.
            </p>
          </PageMasthead>

          {/* ── Philosophy ── */}
          <Section title="OUR APPROACH" defaultOpen>
            <P>
              Civitas is an open-data AI/ML platform that aggregates data from official U.S.
              government sources into unified transparency scorecards for senators, House
              representatives, presidents, and Supreme Court justices. Every score is computed from
              publicly available federal records. We do not editorialize, endorse, or oppose any
              candidate or party.
            </P>
            <P>
              What the scores measure: for senators and House representatives, how well they carry
              out <em className="text-ink">the will of the majority of their constituents</em> — not
              the preferences of a few wealthy donors, and not party defection for its own sake; for
              presidents, how well they serve the country; for Supreme Court justices, how well they
              serve the law regardless of party. Every scoring dimension is justified against that
              yardstick: crossing party lines is credited only where it plausibly moves toward the
              state&apos;s median voter, and the funding dimensions exist because money concentrated
              in few hands is the main channel by which representation drifts away from the
              majority.
            </P>
            <P>
              Scores reflect observable behavior — voting patterns, funding sources, legislative
              activity — not ideology. The formulas are symmetric across parties: the same voting
              record in the same seat produces the same score regardless of whether the member is a
              Democrat or Republican. The system is designed to be structurally non-partisan.
            </P>
            <P>
              Every metric on the scorecard includes a <em className="text-ink">[?] tooltip</em>{" "}
              explaining what it measures and how to interpret it. Hover on desktop or tap on
              mobile. We believe no number should be presented without context — if you see a
              metric, you should be able to understand what it means and where it came from.
            </P>
            <P>
              When data is missing or insufficient, scores default to a neutral 50 out of 100. No
              politician is penalized for something we cannot measure, and no politician receives a
              perfect score without evidence. Scores backed by little data are pulled toward that
              neutral 50 in proportion to how little data there is. This borrows the idea of
              shrinkage estimation, which keeps small samples from producing extreme estimates
              <Cite id="19">Efron &amp; Morris 1975</Cite>, but it is a simpler version: the pull
              is set by a fixed count of observations, not estimated from the data the way their
              estimator is.
            </P>
            <P>
              The Action Center extends this mission to daily civic engagement. It automatically
              surfaces trending issues from news analysis, provides objective summaries free of
              editorial opinion, and recommends non-partisan actions citizens can take to
              participate in their government — without assuming which side of any issue the reader
              supports.
            </P>
          </Section>

          {/* ── Congressional Metrics (Senate + House) ── */}
          <Section title="CONGRESSIONAL SCORECARD METRICS">
            <P>
              Every senator and House representative receives three sub-scores on a 0-100 scale,
              weighted into an overall Representation Score. Higher is better. All 100 senators and
              435 House representatives are scored with the identical framework below — same
              formulas, same data sources (FEC, Congress.gov, GovInfo), same classification
              techniques — so scores are directly comparable across both chambers. House members are
              sourced from the same Congress.gov and FEC endpoints and processed in the same nightly
              pipeline run as senators; the House leaderboard supports pagination and party
              filtering to navigate the larger membership.
            </P>
            <P>
              Campaign-promise extraction was removed entirely (2026-07), not just excluded from
              the weighted score below — four attempts at matching generic platform language
              against specific vote/bill text never got real promise data past a handful of
              evaluable cases per member, and no profile page shows kept/broken/partial data
              anymore. For the audit history behind the current weights and dimensions —
              including why Promise Persistence was removed, why Funding Diversity was folded
              into Funding Independence, and why Donor Independence was removed from Constituent
              Alignment — see the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
                scoring changelog
              </a>
              .
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Funding Independence (33%)</Label>
                <Gist>
                  rewards members whose campaigns are funded by lots of small individual donors
                  rather than PACs or a handful of big donors and industries. The more spread-out
                  and grassroots the money, the higher this score — regardless of party or chamber.
                </Gist>
                <P>
                  Measures four things: (1) PAC dependency — a blend of the share of funding
                  from PACs and how close contributing PACs are to their legal per-election cap,
                  measured against the chamber&apos;s own median member (re-measured every update),
                  since Senate and House candidates rely on PAC money at structurally different
                  rates; (2) the share of funding from small (&lt;$200,
                  unitemized) donors — the broadest possible funding base. Both shares are of
                  contributions (money given by individuals, PACs, party committees, or the
                  candidate), not total receipts: receipts also count transfers in from joint
                  fundraising committees, whose PAC and individual content isn&apos;t broken out,
                  so dividing by receipts understated PAC reliance for exactly the members who
                  fundraise most through them. Funding is measured on the member&apos;s most
                  recent completed election — the campaign that won them their current seat, not a
                  re-election campaign still in progress; (3) relative top-donor
                  concentration — what fraction of the itemized external donor pool comes from the
                  top 10 donors, with the member&apos;s own money and transfers from their own
                  committees excluded; and (4) industry concentration — the inverse
                  Herfindahl-Hirschman Index (HHI) of industry donations, where funding
                  concentrated in a single industry suggests potential regulatory capture. When too
                  little of the money can be assigned to an industry to measure that, it counts as
                  neutral. Component (4) was folded in from a separate Funding Diversity dimension in
                  2026-07 after finding the two dimensions correlated at r=0.72 across the Senate.
                  Two inputs were removed in v6.13 after we checked them against FEC&apos;s own data
                  for 2020–2024 incumbents. Outside spending (super PAC and other independent
                  expenditures supporting a member) used to count toward PAC dependency, but it
                  tracks how competitive the race is, not how dependent the member is: it was
                  highest in swing seats and lower for members who take more PAC money, and by law
                  the member cannot direct it. &quot;Source breadth&quot; was the small-donor share
                  counted a second time (it explained 79–86% of breadth&apos;s variation), plus a
                  penalty on self-funding. The study is in the project repository at
                  docs/research/funding-independence.md. PAC dependency follows
                  Stratmann (2005),
                  <Cite id="5">Stratmann 2005</Cite>
                  who found that PAC contributions are more strongly correlated with roll-call
                  alignment than individual contributions. The concentration components apply the
                  same intuition as HHI at the donor level, following Bonica (2014)
                  <Cite id="1">Bonica 2014</Cite> and the industrial-organization logic Rhoades
                  (1993)
                  <Cite id="6">Rhoades 1993</Cite> built the HHI metric on.
                </P>
                <P>
                  &quot;UNCLASSIFIED&quot; money (committee transfers, joint-fundraising splits,
                  donations lacking employer data — a real 32% median share of total funding across
                  the Senate) is scored neutrally rather than penalized. It is a residual we cannot
                  attribute at all, not evidence of concentration in one source — the same
                  &quot;missing data defaults to neutral&quot; principle applied everywhere else on
                  this page.
                </P>
              </div>

              <div>
                <Label>Constituent Alignment (33%)</Label>
                <Gist>
                  checks whether a member&apos;s voting matches what their state or district elected
                  them to do, compared with what members of their own party in similarly-leaning
                  seats actually do. Voting with your party is the norm in a safe seat, so it only
                  counts against a member when they are more loyal than their own party&apos;s
                  members in comparable seats — and breaking more often than them counts in their
                  favor. Every part of this design was tested against how voters actually respond.
                </Gist>
                <P>
                  Measures how far a member&apos;s voting sits from what their seat asks of it — not
                  raw defection from party. Each member&apos;s break rate on party-labeled votes is
                  compared with the break rate that members of the same party and chamber show in
                  seats with the same partisan lean (Cook PVI). That expectation is measured from
                  the chamber itself every time the pipeline runs, not set by hand: a separate line
                  per party, allowed to bend at a swing seat, so a Republican in a Biden-won seat is
                  compared with how Republicans in seats like that really vote. Matching the
                  expectation scores 50. Breaking more often scores above, breaking less often
                  scores below, and the scale is set by how widely the chamber varies (the most
                  out-of-pattern tenth of members reach the ends).
                </P>
                <P>
                  What the ends mean: a 0 or 100 is a relative position, not a verdict. A 0 means
                  the member breaks with their party far less often than members of their own party
                  in seats that lean the same way; a 100 means far more often. Because the
                  expectation is fitted separately for each party, a member is only ever compared
                  with their own party&apos;s members, and both parties&apos; average scores sit
                  near 50. Which members reach an end in a given update depends on the chamber that
                  year, and a handful can by chance come mostly from one party.
                </P>
                <P>
                  Why symmetric, and why no special treatment for safe seats: through v6.12 a member
                  more loyal than expected was held at neutral, on the argument that loyalty is
                  &quot;unreadable&quot;, and credit for breaking or for a centrist position shrank
                  in safe seats. We tested both choices against 2,545 U.S. House re-election results
                  (1994–2010), using the approach of the studies that established this construct:
                  does the measure predict how the incumbent does with their own voters once the
                  district&apos;s partisanship, the national tide and seniority are accounted for
                  <Cite id="4">Carson et al. 2010</Cite>
                  <Cite id="35">Canes-Wrone et al. 2002</Cite>? It does, and it contradicted both
                  choices. Loyalty beyond what the seat predicts carried the strongest association
                  of all (about 2 points of vote share per standard deviation in 2004), so the
                  neutral floor was throwing away real signal. And the association was the same in
                  safe seats as in competitive ones, for voting and for position alike, so neither
                  gets a safe-seat discount now. Members who break from the flank side of their
                  party (Kirkland &amp; Slapin 2017) did not fare worse for it, so their breaks are
                  not discounted either. The full study, including where the evidence is weak (the
                  association fades in the 2008 and 2010 elections as House races nationalized, and
                  the Senate sample is too small to confirm or reject it), is in the project
                  repository at docs/research/constituent-alignment.md. Confirmation votes on
                  nominations make up a large share of recent Senate roll calls and count at full
                  weight — they are genuine, whipped party-line tests.
                </P>
                <P>
                  Before v4.2 this dimension was called Independent Voting and rewarded raw
                  defection; it also exempted party-line votes on policy areas related to a
                  member&apos;s top donor industries. That exemption is removed: donor industries
                  are not a proxy for state interests, and it shielded exactly the votes most
                  suspect for donor influence.
                </P>
                <P>
                  The score blends seat-relative vote alignment (70%, or 100% when roll-call
                  ideal-point data is unavailable) with position congruence (30%, when available):
                  the member&apos;s first-dimension roll-call position from Voteview, compared with
                  what a same-party member of a similarly-leaning seat typically holds, fit per
                  chamber and per party from the current data. Per-party fits avoid the swing-seat
                  artifact a single pooled fit would create (Bafumi &amp; Herron 2010), and
                  predicted re-election results slightly better than the alternatives in our test.
                  The position used is the congress-specific Nokken-Poole estimate
                  <Cite id="36">Nokken &amp; Poole 2004</Cite>, not career-long DW-NOMINATE, which
                  only lets a member drift along a straight line over their whole career. Scores
                  cover the current term, not the career, and the congress-specific position also
                  predicted results better. A position toward the party&apos;s flank scores below
                  neutral and a position toward the seat&apos;s center scores above, by the same
                  amount either way — our test found the two directions matter equally. The 70/30
                  split is a design choice, not a fitted one: the vote component showed the larger
                  association in the one election where both could be tested, so it keeps the
                  larger share. Coalition breadth (20%
                  here from v5 through v6.10) has moved to Legislative Effectiveness: bipartisan
                  coalition-building is a legislative-effectiveness signal, not a
                  constituent-alignment one — demand for bipartisanship varies with the seat&apos;s
                  own makeup (Harbridge &amp; Malhotra 2011), so a bipartisan member of a lopsided
                  seat can be bipartisan and misaligned at once. Through v6.4 this dimension also
                  included a Donor Independence component (25%, a heuristic based on the money
                  associated with donor-vote topical overlaps) — removed in 2026-07 after finding it
                  measured a close cousin of the Funding Independence signal (both keyed off total
                  money raised and donor-industry concentration) while itself reducing to one of
                  four fixed values for 85% of senators, since no data source discloses per-bill
                  donor positions. Its freed weight now goes entirely to seat-relative vote
                  alignment. We follow the methodological caution of Ansolabehere et al. (2003)
                  <Cite id="18">Ansolabehere et al. 2003</Cite> in interpreting donation-vote
                  correlations generally: correlation does not prove causation.
                  <Cite id="5">Stratmann 2005</Cite>
                </P>
              </div>

              <div>
                <Label>Legislative Effectiveness (34%)</Label>
                <Gist>
                  measures whether a member is actually getting legislative work done — introducing
                  bills that matter, moving them through Congress, and building a network of
                  cosponsors other members trust. Introducing a substantive bill earns real credit
                  even before it passes, matching how political scientists actually measure
                  legislative productivity.
                </Gist>
                <P>
                  Measures whether a member is producing tangible legislative outcomes, following
                  Volden &amp; Wiseman&apos;s (2014)
                  <Cite id="34">Volden &amp; Wiseman 2014</Cite> real published methodology: each
                  sponsored bill is weighted by significance (5x for substantive bills —
                  S./H.R./joint resolutions; 1x for commemorative simple/concurrent resolutions) and
                  credited cumulatively across every stage it reaches — introducing a bill earns
                  real credit on its own, not just bills that go on to pass a chamber or become law.
                  Three components: bill significance &amp; advancement (60%) — this cumulative
                  stage-credit per congress served, compared against an expected credit for a
                  sponsor of the same status in the same chamber. That expectation is measured
                  fresh on every run from the chamber itself — the median credit this congress
                  among members of the same status (majority or minority), with the chamber&apos;s
                  current majority party read from its roster — so a
                  member&apos;s standing doesn&apos;t drift as bills accumulate over a congress, or
                  reset when a new one begins; legislative leadership (25%) —
                  cosponsorship-network PageRank, see below; and bipartisan coalition attraction
                  (15%, v6.11 — moved here from Constituent Alignment): the share of cosponsors a
                  member attracts to their own bills from the other party,
                  chamber-median-normalized. Harbridge-Yong, Volden &amp; Wiseman (2023) show that
                  attracting cross-party cosponsors robustly predicts lawmaking success for majority
                  and minority members alike — and that it is specifically the <em>attraction</em>{" "}
                  of bipartisan cosponsors, not the offering of cosponsorships across the aisle,
                  that carries the effect, so this component uses a receive-only rate rather than
                  the blended give-and-receive bipartisanship figure shown on profiles. Their
                  evidence is a strong association rather than a clean causal proof, and the measure
                  is an input to effectiveness rather than realized output — both disclosed reasons
                  its weight stays modest. When cosponsorship data is missing, the split reverts to
                  exactly the prior 70/30.
                </P>
                <P>
                  Because introduction itself earns credit, a member who sponsors many substantive
                  bills can score well even before any of them advance further — this is Volden
                  &amp; Wiseman&apos;s real design, not a bug: their published methodology counts a
                  bill&apos;s contribution at every stage it reaches, and most sponsored bills never
                  advance at all (a July 2026 measurement of our corpus found Senate majority
                  sponsors advancing bills at 3.6% vs. 2.4% for minority sponsors; House 6.4% vs.
                  2.4%). Each member is therefore compared with the typical member of the same
                  status — majority or minority — in their chamber, as in Volden &amp;
                  Wiseman&apos;s own benchmarks, so whichever party is out of power isn&apos;t
                  judged against the majority&apos;s record. Until September 2026 the expectation
                  was instead the chamber median scaled by the ratio of the two advancement rates.
                  Most credit comes from introducing bills, which majority status doesn&apos;t
                  change, so that ratio over-corrected: it put minority members well above neutral
                  and majority members well below (the House&apos;s gap between the parties reached
                  18 points on production data). This part of the score now centers each status on
                  its own typical member; differences between the parties&apos; averages that
                  remain come from the leadership and coalition parts.
                  The score explanation on each profile breaks the substantive-bill count into
                  introduced-only / advanced-further / became-law so the volume-vs-advancement split
                  is visible as real numbers.
                </P>
                <P>
                  Both the expected-credit baseline and the majority/minority-status adjustment
                  above are calibrated separately for the House and Senate (v6.9) — the two
                  chambers&apos; real bill volumes and advancement rates genuinely differ, and
                  comparing every member against one shared, pooled-across-chambers figure
                  previously understated House members&apos; effectiveness and overstated the
                  Senate&apos;s. See the{" "}
                  <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
                    scoring changelog
                  </a>{" "}
                  for the live-population numbers behind that fix.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Known Limitations ── */}
          <Section title="KNOWN LIMITATIONS &amp; DISCLOSURES" id="known-limitations">
            <P className="text-ink-lo text-xs">
              Every item below is an open engineering problem, not a settled tradeoff we&apos;ve
              made peace with — several started as disclosures here and were later fixed outright
              (see the v6.8 entry below, and the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
                scoring changelog
              </a>{" "}
              for the full history). Where a limitation is fixable, we fix it and remove the
              disclosure. Where it isn&apos;t — no dataset exists, or fixing it would require an
              editorial judgment call the platform&apos;s no-hardcoded-conclusions rule resists — we
              name the specific reason why, so it can be revisited if that changes.
            </P>
            <Gist>
              Democratic and Republican senators finance their campaigns differently on average, so
              Funding Independence scores differ by party on average too — not because the formula
              treats parties differently, but because the underlying fundraising behavior really is
              different.
            </Gist>
            <P>
              <em className="text-ink">
                Scores correlate with funding style, and funding style correlates with party.
              </em>{" "}
              In the July 2026 data (measured as shares of total receipts, before shares moved to
              contributions in v6.13), Democratic senators took roughly half the PAC share of
              Republican senators (median ~10% vs ~17%) and raised about twice the small-donor
              share (~24% vs ~12%). Because Funding Independence measures those behaviors directly, average scores
              differ by party. The formulas are identical for everyone and contain no party term;
              the gap reflects measured funding behavior, not editorial judgment.
            </P>
            <Gist>
              a bigger campaign naturally looks more &quot;independent&quot; by percentage even with
              the same PAC dollars, simply because the total got bigger. We also check absolute PAC
              dollars, but no single number fully separates &quot;independent&quot; from
              &quot;big.&quot;
            </Gist>
            <P>
              <em className="text-ink">Fundraising scale still matters.</em> Larger campaigns
              naturally have smaller PAC <em>shares</em> because PAC checks are legally capped while
              individual money is not. We mitigate this by scoring absolute PAC dollars alongside
              the share, but no single number fully separates &quot;independent&quot; from
              &quot;big.&quot;
            </P>
            <Gist>
              election cycles run different lengths for different members (and for the House vs. the
              Senate), so funding scores are technically comparing different-sized snapshots of time
              across members.
            </Gist>
            <P>
              <em className="text-ink">Comparison windows differ by tenure and chamber.</em> Funding
              metrics cover a member&apos;s two most recent election periods — roughly 8 years for a
              veteran senator, 2 for a freshman, 4 for House members — so cross-member comparisons
              weigh different spans of time.
            </P>
            <Gist>
              when we flag a donor whose industry overlaps with a vote, that shows where money and
              legislative activity intersect — it is not proof the donation influenced the vote.
            </Gist>
            <P>
              <em className="text-ink">
                Donor-vote connections are semantic overlaps, not lobbying records.
              </em>{" "}
              They aggregate employee and PAC money associated with an organization and match it to
              vote topics by embedding similarity. They indicate where money and votes intersect;
              they do not establish influence.
            </P>
            <Gist>
              a senator whose donors cluster in one industry scores the same whether that industry
              is their state&apos;s home industry or an out-of-state special interest. That&apos;s
              deliberate, not an oversight — see below for why.
            </Gist>
            <P>
              <em className="text-ink">
                Concentrated industry funding is scored as capture risk even when it plausibly
                reflects a state&apos;s real economic base.
              </em>{" "}
              A senator whose donations concentrate in, say, the auto industry in Michigan or
              agriculture in Kansas scores the same on Funding Independence&apos;s
              industry-concentration component as one captured by an unrelated out-of-state interest
              — this platform does not check whether a donor industry is also a major local
              employer. That is a deliberate choice, not an oversight: we considered and rejected a
              &quot;this industry matters to the state&quot; exemption for the same reason the v4.2
              donor-industry voting exemption was removed (see the
              <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
                {" "}
                scoring changelog
              </a>
              ) — local economic dominance plausibly gives an industry <em>more</em> leverage over a
              senator, not less, so exempting it would weaken the signal exactly where large-scale
              capture is most consequential. No public dataset can separate &quot;this funding
              reflects genuine local interest&quot; from &quot;this funding is capture that happens
              to correlate with local economic weight&quot; — concentration is scored as risk, full
              stop, following the same industrial-organization logic (Rhoades 1993) the HHI metric
              is built on.
            </P>
            <Gist>
              we estimate what a senator&apos;s state &quot;expects&quot; from how the state votes
              for president overall, not opinion on the specific bill in front of them — a broad
              stand-in for local opinion, not a precise one. We looked for a better public
              alternative and didn&apos;t find one that wasn&apos;t itself stale or a black box (see
              below).
            </Gist>
            <P>
              <em className="text-ink">
                Presidential-vote PVI doesn&apos;t capture issue-specific constituent opinion.
              </em>{" "}
              A senator&apos;s expected break rate (see Constituent Alignment above) is measured
              against seats that vote like theirs for president, not to opinion on the specific issue a given
              vote concerns — a state&apos;s presidential lean says little about, say, local opinion
              on public land use in Utah or water rights in Arizona. We looked for a real, freely
              available substitute: the best candidate found (Tausanovitch &amp; Warshaw&apos;s
              survey-based ideology estimates by district/state) still only produces a single
              composite left-right score, the same kind of proxy PVI already is — not per-issue
              opinion — and its public data is already several years stale. Actual issue-level
              constituent opinion at this scale would require building
              multilevel-regression-and-poststratification (MRP) modeling in-house over raw survey
              microdata: a statistics pipeline, not a lookup, and a genuine black box relative to
              every other formula on this page. We chose not to build one rather than trade this
              platform&apos;s auditability for a partial, hard-to-explain fix.
            </P>
            <Gist>
              the score gives credit to a member who is genuinely in step with their seat, but
              &quot;in step&quot; is measured against how the seat votes for president, which is
              not the same thing as the voters who actually elected the member.
            </Gist>
            <P>
              <em className="text-ink">
                Constituent Alignment measures congruence with the seat&apos;s partisan lean, not
                with the member&apos;s own voters.
              </em>{" "}
              The target is party-relative and measured from the live chamber — what a same-party
              member of a similarly-leaning seat typically does (Canes-Wrone, Brady &amp; Cogan
              2002) — which avoids the mistake of expecting members to sit at their seat&apos;s raw
              median, where no member of either party sits (Bafumi &amp; Herron 2010). It is still
              one-dimensional, and members track their <em>reelection</em> constituency (primary
              voters and copartisans) as well as the geographic one (Fenno 1978; Clinton 2006).
              Through v6.12 that was the stated reason for shrinking credit and penalties in safe
              seats. Tested against House re-election results, safe-seat voters responded to both
              just as much as competitive-seat voters did, so v6.13 removed the scaling. Issue-level
              opinion data (e.g. MRP estimates or CES roll-call-matched items) remains the named
              next step for this dimension.
            </P>
            <Gist>
              two of the checks that used to lower Constituent Alignment were computed from the same
              underlying data, so they were catching the same problem twice. v6.8 cut that overlap;
              v6.11 removes it structurally — the two signals no longer live in the same dimension,
              and the position check now uses an independent data source where available. A smaller
              cousin of the same caveat now applies inside Legislative Effectiveness.
            </Gist>
            <P>
              <em className="text-ink">
                Cosponsorship-derived signal overlap — mostly resolved, one residual.
              </em>{" "}
              A 2026-07-21 audit found Constituent Alignment&apos;s position-mismatch discount and
              coalition breadth correlate at r=-0.76 (58% shared variance, n=99) — both were
              projections of the same cosponsorship network. v6.8 reduced the double-count; v6.11
              removes its structural basis: coalition breadth has left Constituent Alignment
              entirely (it now scores legislative effectiveness, where the evidence supports it),
              and the position signal is measured from roll-call ideal points (Voteview, ingested
              automatically every pipeline run behind ingestion gates) — the genuinely independent
              second signal this disclosure previously said wasn&apos;t available. The
              cosponsorship-based discount it replaced was removed entirely in v6.13. The residual: within Legislative
              Effectiveness, the leadership component (cosponsorship PageRank) and the new
              bipartisan-coalition-attraction component are both computed from the cosponsorship
              network (network centrality vs. cross-party share — related data, different measures).
              Their combined weight is capped at 40% for that reason, and their live correlation is
              a standing post-run check. See the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
                {" "}
                scoring changelog
              </a>{" "}
              for the full account.
            </P>

            <Gist>
              State ballot pages show the statewide slice of a ballot — Senate and House
              contests and statewide measures. They do not show your governor&apos;s race,
              your state legislature, your county and city offices, or local measures.
            </Gist>
            <P>
              Two separate causes, one fixable and one not. The governor&apos;s race and other
              statewide executive contests — 36 governorships, 30 attorneys general and
              more in 2026 — are missing because this platform has no state-office data
              source at all yet. That is an ingestion gap, and it is fixable. Everything
              below the state level is missing for a structural reason: ballots are printed
              per precinct, so there is no such artifact as &ldquo;the ballot&rdquo; for a
              whole state, and the only way to show your actual ballot is to take your home
              address and send it to a third-party lookup service. We are not going to do
              that, so instead every page enumerates what it omits and links you to your own
              election office. See{" "}
              <a href="#state-ballots" className="underline underline-offset-2 hover:text-phos">State Ballots &amp; Ballot Measures</a>.
            </P>
            <Gist>
              Ballot-measure coverage depends on an optional third-party source, and some
              states may show as not yet covered.
            </Gist>
            <P>
              Measures come from Vote Smart&apos;s public API. Where a state has not been
              ingested, or an ingest failed, the page says exactly that rather than showing
              an empty section — an empty section would read as &ldquo;this state has no
              measures&rdquo;, which is a materially different and potentially damaging claim.
              The fix is per-state ingestion directly from Secretary of State offices, which
              is authoritative but needs roughly fifty separate adapters against fifty
              independently-redesigned government sites. Until then, the official lookup link
              on every state page is the complete answer and the measures are a convenience.
            </P>
          </Section>

          {/* ── Sponsorship Analysis ── */}
          <Section title="SPONSORSHIP ANALYSIS (LEADERSHIP &amp; IDEOLOGY)">
            <P>
              Every senator and representative also receives two metrics derived from cosponsorship
              networks — the pattern of which members sign onto each other&apos;s bills (within each
              chamber&apos;s own network; House and Senate cosponsorship are separate graphs).
              Ideology is purely informational context. Legislative Leadership is
              <em className="text-ink"> not</em> purely informational — it already feeds into
              Legislative Effectiveness above at 25% weight (30% in the fallback split used when
              cosponsorship data is missing); the number shown on a member&apos;s card is the same
              underlying score, displayed directly (with a tenure adjustment, see below) rather than
              hidden inside the composite.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Legislative Leadership (0-100)</Label>
                <P>
                  Measures legislative influence using the PageRank algorithm
                  <Cite id="32">Brin &amp; Page 1998</Cite>
                  applied to cosponsorship networks. When Senator A cosponsors Senator B&apos;s
                  bill, that creates a directed link in the network. PageRank computes centrality: a
                  senator whose bills attract many cosponsors — especially from other influential
                  senators — receives a higher score. This mirrors GovTrack&apos;s leadership
                  methodology.
                  <Cite id="33">Tauberer 2012</Cite>
                </P>
                <P>
                  The algorithm uses power iteration with a damping factor of 0.85 and converges in
                  ~50 iterations. Raw PageRank values are rescaled to [0, 1] using a logarithmic
                  transformation to compress the heavy-tailed distribution, then displayed as 0-100.
                  That places each member within their own chamber (the median member sits near 50);
                  it is not a percentile, and the chamber&apos;s lowest member scores 0, not a
                  neutral default. Each cosponsorship is weighted by how far the bill got (became
                  law, advanced, or stalled); a bill whose outcome isn&apos;t known counts as the
                  typical bill whose outcome is, not as one that became law.
                </P>
                <P>
                  Network centrality structurally takes years to build — a freshman senator&apos;s
                  raw score is near-zero not because they lead poorly but because they haven&apos;t
                  had time to accumulate cosponsorship connections yet. Both the score component and
                  the displayed number shrink the raw value toward neutral 50 for senators with
                  under 6 years in office, confidence-scaled to a full term, so a brand-new senator
                  reads as &quot;not enough track record yet&quot; rather than &quot;bad at
                  leadership.&quot;
                </P>
                <P>
                  Raw cosponsorship-network centrality can&apos;t on its own tell a substantive bill
                  from a message bill introduced with no real chance of passing — a senator who
                  signs onto ten symbolic resolutions accrued the same network weight as one who
                  cosponsors ten bills that actually became law. Since v6.2, each cosponsorship is
                  weighted by what happened to the underlying bill: full weight if it became law,
                  reduced weight if it passed a chamber or cleared committee, and further reduced
                  (not zeroed — a stalled bill is still real evidence of a working relationship) if
                  it never advanced.
                </P>
              </div>

              <div>
                <Label>Ideology Score (0-1)</Label>
                <P>
                  Computes a behavioral ideological position using Singular Value Decomposition
                  (SVD) on the cosponsorship matrix, following Tauberer (2012).
                  <Cite id="33">Tauberer 2012</Cite>
                  The second singular vector (first is trivially related to overall activity)
                  captures the primary ideological dimension — the axis along which senators most
                  differ in who they cosponsor. This is analogous to DW-NOMINATE
                  <Cite id="20">Poole &amp; Rosenthal 1985</Cite>
                  but derived from cosponsorship patterns rather than roll-call votes.
                </P>
                <P>
                  The ideology score is oriented so that lower values correspond to progressive
                  positions and higher values to conservative positions, calibrated by checking the
                  mean score of each party. It serves as a prior for the partisan depth
                  calculation: when a senator has few recorded votes, the ideology score regularizes
                  the estimate; as vote data accumulates, the prior weight drops to zero. The prior is
                  first put on the same scale as the vote-based lean, using the relationship between
                  the two across senators with full voting records. The resulting label (deep,
                  moderate or centrist) says where a senator falls within their own party, the most
                  partisan third, the middle third or the least partisan third, so a fixed cut-off
                  can&apos;t label one party&apos;s members as more extreme than the other&apos;s just
                  because the two parties&apos; leans sit on different ranges.
                  <Cite id="19">Efron &amp; Morris 1975</Cite>
                </P>
              </div>

              <div>
                <Label>Sponsorship Description</Label>
                <P>
                  Combines the leadership and ideology scores into a human-readable label (e.g.,
                  &quot;progressive Democratic leader&quot; or &quot;conservative Republican
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
              Presidents are scored on four dimensions, 0-100 scale, computed entirely from live,
              historical, and expert-survey datasets — there is no hand-set or seeded score anywhere
              in this pipeline (2026-07 rewrite). A dimension a president has no real data source
              for is left blank (N/A) rather than filled with a fabricated or neutral placeholder,
              and the overall score renormalizes across whichever dimensions actually apply to that
              president. Identity data (name, party, term dates) is fetched live too, from the same
              UCSB roster used for the metrics below — nothing about a president&apos;s profile is
              typed into this codebase by hand.
            </P>
            <P>
              <em className="text-ink">
                Independence and Follow-Through were removed entirely (2026-07)
              </em>
              , not just disclosed as limitations. Both were always a one-time hand-set number with
              no live formula and, unlike every dimension below, no realistic path to one:
              Independence&apos;s obvious data source (OpenSecrets&apos; cabinet/appointee
              revolving-door tracking) was itself discontinued in 2025, and Follow-Through would
              need the same platform-text-vs-action matching technique already tried four times and
              abandoned for senators&apos; Promise Persistence (see the{" "}
              <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
                scoring changelog
              </a>{" "}
              — v6.0). Rather than keep presenting a hand-set number as a computed score,
              they&apos;re gone. Their combined weight first redistributed proportionally across the
              remaining four (Public Mandate 15→23%, Effectiveness 20→31%, Competence 15→23%, Agency
              Alignment 15→23%), then a fifth dimension — Historical Legacy — was added shortly
              after (also 2026-07, following review that found presidents like Lincoln landing in
              the bottom half of the ranking despite every individual number being defensible on its
              own terms: nothing in the first four dimensions could credit &ldquo;preserved the
              Union, ended slavery&rdquo; at all).
            </P>
            <P>
              Historical Legacy&apos;s weight went through two revisions before landing at 35%, both
              checked against the real 47-president dataset rather than picked by eye. Equal fifths
              (20%) let the other four dimensions — which individually barely correlate with
              historian judgment at all (Spearman 0.17 between the four mechanical dimensions alone
              and C-SPAN&apos;s own ranking) — outvote the one dimension that actually tracks it,
              putting Coolidge, McKinley, and Harding in the top 10 while Lincoln and Eisenhower
              fell out of it. Raising Historical Legacy to 50% fixed that, but introduced a
              different problem: at 50%, this platform&apos;s overall ranking correlated 0.96 with
              simply using C-SPAN&apos;s own ranking alone — the four mechanical dimensions were
              contributing almost nothing of their own. 35% is the point where the top of the
              ranking is already recognizable (FDR, Washington, Lincoln, Theodore Roosevelt, JFK,
              Eisenhower) while the mechanical dimensions still meaningfully move the rest of the
              list (correlation to a pure C-SPAN ranking: 0.89, not 0.96). Coolidge and McKinley
              still edge into the bottom of the top 10 at this weight — a disclosed, arguable
              disagreement with C-SPAN&apos;s own ranking, not something we kept tuning the weight
              to paper over. Each president&apos;s page also shows how many of the 4 dimensions
              actually have a score for them (as few as 2, for a short-tenure or currently- serving
              president) — a score built from partial data is not shown with the same implied
              confidence as one built from all 4.
            </P>
            <P>
              A closer look at Coolidge&apos;s own numbers turned up a real hole in Competence
              (executive-order activity rate), the dimension covering administrative execution:
              Coolidge and Harding have nearly identical EO-rates (~216/year each), yet
              C-SPAN&apos;s own historians rate their actual administrative skill 596 vs. 334 (of
              1000) — almost as far apart as two presidents get. Across all 44 rated presidents,
              EO-rate correlates just 0.097 (p=0.53) with C-SPAN&apos;s &ldquo;Administrative
              Skill&rdquo; category — statistically no different from noise. Using C-SPAN&apos;s
              Administrative Skill score directly instead wasn&apos;t a clean fix either: it&apos;s
              one of the ten categories C-SPAN itself sums into the same Final Score already driving
              Historical Legacy at 35%, so folding it into a second dimension would push this
              platform&apos;s true historian-derived weight toward ~51%, undoing the exact
              over-reliance-on-C-SPAN problem the 50%&rarr;35% revision above was built to avoid.
              Competence is removed entirely (2026-07) — same standard as
              Independence/Follow-Through: no defensible live signal, no fabricated one in its
              place. Its 16.25% is split evenly across the three remaining mechanical dimensions
              (21.67% each); Coolidge drops from the top 10 to #12, Harding to #26, McKinley to #17,
              while Lincoln and Eisenhower both stay in the top 10 — the same qualitative target
              that justified 35% still holds.
            </P>
            <P>
              A closer look at how that 35% actually gets applied found it wasn&apos;t the real
              operative number for most presidents. The renormalization used to spread flatly across
              whichever dimensions had data — so a president missing Agency Alignment (everyone
              before Clinton, ~36 of 47) had Historical Legacy&apos;s EFFECTIVE weight rise to
              ~44.7%, and the four non-elected successors missing both Agency Alignment and Public
              Mandate (Tyler, Fillmore, Arthur, Andrew Johnson) had it rise to ~61.8%. 35% was only
              the true weight for 4 of 47 presidents. This is fixed (2026-07): Historical Legacy is
              now held at exactly 35% whenever at least two mechanical dimensions are present, with
              the mechanical dimensions renormalizing only among themselves for the rest. Below that
              floor — a single mechanical dimension alone — it falls back to the old flat
              renormalization instead, since one number isn&apos;t reliable enough to carry 65% of a
              score by itself: Fillmore&apos;s Effectiveness is 100/100 purely from a Gold-Rush-era
              GDP boom he had little to do with, which would have swapped his real (near-bottom,
              19/100) historian rating for a top-10 placement under a flat 65% share. Re-checked
              against the real dataset under this corrected scheme: 35% still keeps Lincoln and
              Eisenhower in the top 10 and Coolidge/Harding/McKinley out of it, so the headline
              number didn&apos;t need to change — only how consistently it gets applied.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Public Mandate (21.67%)</Label>
                <P>
                  Reflects approval trajectory and coalition retention. Gallup, this platform&apos;s
                  original approval source, ended presidential approval tracking entirely in
                  February 2026 after 88 years; approval data now comes from the American Presidency
                  Project (presidency.ucsb.edu), which is still updated for the sitting president,
                  aggregating AP-NORC/CNN-SSRS/Marist/Pew/Verasight. This covers every president
                  from Truman onward — 70% average approval over the term, 30% the trend from
                  term-start to term-end, both scored against real population statistics computed
                  from every president&apos;s actual polling history. Presidents before Truman have
                  no polling era at all, so their Public Mandate uses UCSB&apos;s historical
                  election-margin data instead — the average margin of victory across their own
                  election win(s), the pre-polling-era proxy. The five presidents who never won a
                  presidential election in their own right have neither and show N/A for this
                  dimension, not a fabricated number.
                </P>
              </div>

              <div>
                <Label>Effectiveness (21.67%)</Label>
                <P>
                  Measures economic outcomes: GDP growth (60%) and job creation (40%). GDP growth is
                  computed for the full presidency from real-GDP series back to 1790, as the average
                  annual growth over the term with the first calendar year excluded (that year mostly
                  reflects the outgoing administration&apos;s policy). Job creation comes from BLS
                  payroll data, which only exists from 1939 onward — presidents before that are
                  scored on GDP growth alone, not defaulted on the missing component. Both are
                  compared with the other presidents&apos; actual figures, measured on every update,
                  rather than with fixed cut-offs. GDP growth is compared within its era, before and
                  after 1947: estimates from before the war exaggerate booms and busts (Romer 1989),
                  and term growth varied two and a half times as much then, so one scale for both
                  pinned one in eight earlier presidents at 0 or 100. Jobs stay in absolute numbers,
                  since the percentage rate falls over time with the workforce&apos;s own slower
                  growth. A limit worth knowing: since 1946, 60% of the variation in a
                  president&apos;s term growth is shared with 13 other advanced economies over the
                  same years, so this dimension mostly measures the economy a president presided
                  over, not one they created. The study is in the project repository at
                  docs/research/president-scores.md.
                </P>
              </div>

              <div>
                <Label>Agency Alignment (21.67%)</Label>
                <P>
                  Measures how far executive agencies carry through the rulemaking they start: the
                  share of Federal Register rulemaking documents during the term that were final
                  rules rather than proposals, compared with the other administrations since 1994.
                  It used to also reward the number of rules issued. That was removed: rule volume
                  follows an administration&apos;s view of regulation, with the record low in 2019 and
                  the record high in 2024, so scoring more rules as better scored a policy
                  preference. Coverage starts with Clinton. This is a digitization wall, not a conceptual one: notice-and-comment
                  rulemaking was a real, functioning practice well before the 1990s, but no
                  machine-readable record of it exists that far back — checked directly (2026-07)
                  rather than assumed: federalregister.gov&apos;s API returns zero results for any
                  pre-1994 president, and govinfo.gov&apos;s own structured Federal Register data
                  starts at year 2000. Earlier issues exist only as scanned page images with no
                  structured document-type or agency tagging, and reconstructing rulemaking counts
                  from those would mean OCR&apos;ing and classifying decades of raw scanned text —
                  the same kind of unreliable pipeline already rejected for Follow-Through and
                  Competence&apos;s court-success-rate. Every president before Clinton shows N/A for
                  this dimension, excluded from their overall score entirely rather than scored on a
                  proxy.
                </P>
              </div>

              <div>
                <Label>Historical Legacy (35%)</Label>
                <P>
                  Covers what none of the other three dimensions can: crisis leadership, moral
                  authority, vision, and similar historical-consequence judgments that don&apos;t
                  reduce to GDP growth, approval polling, or rulemaking. Sourced from
                  C-SPAN&apos;s Presidential Historians Survey — ~142 professional historians in the
                  2021 cycle (the most recent; the 2025 cycle was explicitly postponed by C-SPAN,
                  citing the risk of turning &ldquo;historical analysis&rdquo; into
                  &ldquo;punditry&rdquo; with a former president returning to office), scored across
                  ten categories and aggregated into one point total. This is categorically
                  different from the hand-set Independence/Follow- Through values removed elsewhere:
                  a real, external, periodically-run survey with a documented methodology, not a
                  single number invented for this platform — the same &ldquo;trust a well-documented
                  external institution&rdquo; category as citing BLS or Federal Register data, just
                  survey-based rather than administrative-record-based. Only rates presidents whose
                  terms were complete as of the 2021 cycle — every currently-serving or
                  just-departed president shows N/A here, genuinely unrated by the survey&apos;s own
                  cadence, not a fetch gap.
                </P>
                <P>
                  Being real, external, and methodologically documented does not make this survey
                  unbiased, and we don&apos;t present it as neutral ground truth. Political
                  scientists who study these historian-ranking surveys have documented real,
                  specific patterns in them: professional historians as a field skew toward favoring
                  presidents who expanded federal/executive power, which plausibly inflates FDR,
                  Wilson, and LBJ relative to how a more ideologically mixed panel might rate them;
                  and historians are reluctant to rank very recent presidents at all until enough
                  distance has passed to assess their legacy, which is the direct reason Obama and
                  George W. Bush&apos;s scores may still be unsettled and Biden and the current
                  president have none. Weighting this survey at 35% means this platform&apos;s
                  ranking inherits those biases at roughly that same strength, not zero.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Supreme Court ── */}
          <Section title="SUPREME COURT JUSTICE SCORECARDS">
            <P>
              Justices are scored on impartiality and ideological consistency using case-level
              voting data from the Oyez Project and official Supreme Court records. Case opinions
              link directly to the official supremecourt.gov slip opinion PDFs.
            </P>
            <P>
              Justice scoring evaluates whether a justice applies consistent legal principles across
              cases or shifts positions based on the political valence of the parties involved. This
              is analogous to the independence metric used for senators but adapted to the judicial
              context where party loyalty is replaced by jurisprudential consistency.
            </P>
            <P>
              The score has two parts. Ideological consistency (44%) measures how differently a
              justice agrees with the two sets of colleagues, those appointed by the same
              party and those appointed by the other, weighting close decisions most because they
              reveal the most. Independence (56%) measures how often, in split decisions, a justice
              sides with the other party&apos;s appointees against their own. Until v6.13 there were
              two more. &quot;Judicial restraint&quot; scored how often a justice dissents. Tested
              on the Rehnquist Court&apos;s 1994–2004 votes, dissent frequency turned out to measure
              distance from the Court&apos;s median justice, who is in nearly every majority. In a
              simulated 6–3 Court it put the smaller bloc about 21 points behind just for being
              outvoted, so it was removed. &quot;Bipartisan agreement&quot; measured the same thing
              as independence (a 0.86 correlation), so its weight moved there. Each
              justice&apos;s dissent rate is still shown as a plain statistic. The study is in the
              project repository at docs/research/justice-scores.md.
            </P>
          </Section>

          {/* ── State ballots ── */}
          <Section title="STATE BALLOTS &amp; BALLOT MEASURES" id="state-ballots">
            <P>
              Each state has a ballot page at <span className="text-signal-cyan">/elections/states/&lt;ST&gt;</span> showing
              the federal contests on that state&apos;s ballot and its statewide ballot
              measures. Every candidate race links through to full candidate detail —
              FEC fundraising totals, filing status, and live news coverage.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  WHY IT IS NOT &ldquo;YOUR BALLOT&rdquo;
                </h3>
                <P>
                  A U.S. ballot is defined per <em>ballot style</em> — the exact combination
                  of contests you personally are eligible to vote on. Precincts split by
                  district boundaries carry several styles, and a single county can print
                  dozens. Nationally there are tens of thousands. Only a few things are
                  genuinely uniform across an entire state: the U.S. Senate contest,
                  statewide ballot measures, and statewide offices.
                </P>
                <P>
                  So these pages cover the statewide slice and say so plainly, at the top
                  of the page rather than in a footnote. Your U.S. House district, state
                  legislative districts, county and city offices, judicial questions, and
                  local measures are not shown, and each page links you to your own
                  election office for the rest. We could show a true personal ballot only
                  by asking for your home address and sending it to a third-party service —
                  which is exactly what this platform is built not to do.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  WHETHER A CANDIDATE LIST IS THE BALLOT
                </h3>
                <P>
                  Four quite different things can fill a candidate list, and they used to
                  look identical: a state&apos;s certified November ballot; nominees derived
                  from primary results (which cannot see a Libertarian, Green or
                  independent who never ran in one); the people on a primary ballot; and —
                  when nothing has confirmed anything — every active FEC filer, some of
                  whom will never appear on any ballot.
                </P>
                <P>
                  The calendar decides whether that last one is still an honest answer.
                  Before a primary it is the best available: nobody knows the ballot yet.
                  After one it means the ballot <em>has</em> been decided and we do not
                  have it. On 26 September 2026, 39 states had certified candidates and
                  eleven were still showing FEC filers months after their own primary —
                  Ohio by 144 days, New York by 95, with one New York race listing 25
                  filers for a ballot holding about two. Those pages said nominees
                  &ldquo;aren&apos;t confirmed yet&rdquo;, which told a reader a settled
                  contest was still open.
                </P>
                <P>
                  Each state page now leads with what its lists actually are, and says
                  plainly when a primary has already passed. A state whose ballot is
                  certified says nothing at all — a notice on every page is one readers
                  learn to skip.
                </P>
                <P>
                  Re-probing those eleven live found three different causes, not one. Nine
                  rely on a national source that does not publish general-election
                  candidates until close to the election. New Hampshire was not broken at
                  all — its results are deliberately held for 21 days after its primary and
                  were 18 days old. South Dakota was the only genuine fault: its
                  election-night site serves whichever election is current, and a July
                  runoff carrying only two state offices pushed the June federal results
                  out of view. South Dakota now reads its Secretary of State&apos;s list of
                  who is on the November ballot instead, which also shows an independent
                  candidate that primary results can never reveal.
                </P>
                <P>
                  A second look at the nine found that three of them publish their
                  November ballot directly after all: Louisiana&apos;s results portal,
                  South Carolina&apos;s candidate-tracking system and Missouri&apos;s
                  certification of candidates to its counties. All three are now read
                  as the ballot itself, third-party and independent candidates included.
                </P>
                <P>
                  Reading the ballot rather than primary results also catches a nominee
                  who is replaced after the primary. In Maine, Graham Platner won the
                  Democratic Senate primary, withdrew in July, and the party nominated
                  Troy Jackson; in South Carolina a special primary replaced the June
                  winner for Senate. Where a state&apos;s source is its certified ballot, it
                  is treated as the final word: anyone not on it is taken off the page.
                </P>
                <P>
                  Everyone on a certified ballot is shown, including candidates who never
                  filed with the FEC — they appear with &ldquo;no FEC filing&rdquo; instead
                  of fundraising figures. Colorado, Virginia and Tennessee now read their
                  certified candidate lists too (Tennessee&apos;s federal races alone list 36
                  independents), and Wisconsin its official primary canvass, a plain
                  file on an otherwise blocked site. Until a state certifies its list, its
                  primary results stand in, and the page says &ldquo;nominees&rdquo; rather
                  than claiming the whole ballot. The remaining five states sit behind bot
                  challenges on their election sites, or in Oklahoma&apos;s case an API that
                  requires logging in. We do not work around either.
                </P>
                <P>
                  A Senate race only appears where the FEC&apos;s election calendar lists
                  one. Before that check, a handful of people who file paperwork for
                  offices in many states at once were enough to invent a Senate
                  &ldquo;special election&rdquo; in New York and Hawaii, neither of which
                  votes for a senator in 2026.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  FINDING YOUR DISTRICT BY POINTING, NOT TYPING
                </h3>
                <P>
                  You can pick the county you live in and the page narrows to the district
                  covering it. The index is built from what is already on the page, so
                  nothing is typed, sent or stored, and no lookup service is involved.
                  About 13% of US counties span more than one district — those offer the
                  two or three as a second tap rather than sending you elsewhere. A text
                  filter remains for anyone who prefers it.
                </P>
                <P>
                  Where a county is not enough — a county split between districts, or a
                  city holding several — the page also draws a map of the districts
                  themselves, from the Census Bureau&apos;s 119th-Congress boundary file.
                  Each is shaded by the same lean rule as the list (redder leans
                  Republican, bluer Democratic, paler is closer); hovering previews a
                  district&apos;s race and clicking narrows the page to it. The map is
                  served with the site, so it too sends nothing anywhere.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  AN OPTIONAL TOWN SELECTOR — WITHOUT YOUR ADDRESS
                </h3>
                <P>
                  Each state page also offers an optional town selector for a small,
                  hand-picked list of towns, showing local races (city council, school
                  board, local measures) the statewide content above cannot. This is
                  NOT the address-based personal ballot the paragraph above rules out.
                  It never asks for or sends your address anywhere. Instead it looks up
                  a fixed, public address we chose for that town — its own town hall —
                  so every visitor who picks the same town gets the identical lookup.
                  Nothing about you or your visit ever leaves this server.
                </P>
                <P>
                  It is still an approximation, and we say so next to the selector: a
                  town can contain more than one precinct, so a race tied to your
                  specific street may not appear, or a race tied to a different part of
                  town might. Town, not county or state, is the level chosen to keep
                  that error small — precinct results are resolved to the exact address
                  looked up, so a wider &ldquo;representative&rdquo; address would have
                  been a substantially larger error. Unset the town selector, or use
                  the official lookup link above, for the real thing.
                </P>
                <P>
                  This source also only carries data close to an election&apos;s own
                  date — a general election months out is not yet indexed anywhere in
                  it, for any town, in any state. If a town shows &ldquo;could not load
                  local races right now&rdquo;, that is very likely why, not a wrong
                  address or a broken selector; local races for the current general
                  should appear as Election Day approaches.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  MEASURES ARE QUOTED, NEVER REWRITTEN
                </h3>
                <P>
                  Each measure shows its official ballot title, official summary, fiscal
                  impact statement, and the state&apos;s own description of what a YES and a
                  NO vote do — all reproduced verbatim, with a link to the source. Nothing
                  on these pages is written by an AI model, and there is no plain-language
                  rewrite. See <span className="text-signal-cyan">HOW AI IS USED</span> below
                  for why we ruled that out rather than shipping it.
                </P>
                <P>
                  Where a state publishes no yes/no description, we show none. We never
                  infer it: the intuitive reading (&ldquo;yes enacts the thing&rdquo;) is
                  exactly backwards on a veto referendum, where approving <em>retains</em> the
                  law being challenged.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  WHO WROTE THE WORDS YOU ARE READING
                </h3>
                <P>
                  Ballot titles are among the most litigated documents in American election
                  law, precisely because their wording is contested — courts have voided
                  measures over language a legislature wrote. So each quote names its
                  author (&ldquo;Drafted by the Georgia General Assembly&rdquo;, &ldquo;Prepared
                  by the Legislative Analyst&rsquo;s Office&rdquo;). Naming the drafter is more
                  neutral than presenting the quote bare, because who wrote it is what tells
                  you how to weigh it.
                </P>
                <P>
                  We do not reproduce the arguments for and against printed in official voter
                  guides. Those are written by campaign committees, not the state, and in some
                  states the slots are purchased — presenting them as a matched pair would
                  manufacture a balance that may not exist.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  &ldquo;NO MEASURES&rdquo; VS. &ldquo;WE DON&rsquo;T KNOW YET&rdquo;
                </h3>
                <P>
                  An empty section on a page about your ballot reads as &ldquo;there is
                  nothing to research&rdquo; — which would be a damaging thing to imply about
                  a state that has seventeen amendments pending. So the two cases are tracked
                  separately and displayed differently: a state where our source explicitly
                  reports no statewide measures says so, and a state we have not yet ingested
                  says <em>that</em>, and points you at the official lookup instead.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  MEASURES REMOVED FROM THE BALLOT
                </h3>
                <P>
                  Measures are certified and struck continuously through a cycle — courts have
                  overturned roughly 2.3% of state ballot measures since 1995. When a measure
                  leaves the ballot we mark it removed and keep showing it for a period, rather
                  than deleting it. If you read about a measure here last month, you need to be
                  told it is gone; a card that silently disappears cannot tell you anything.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Action Center ── */}
          <Section title="ACTION CENTER">
            <P>
              The Action Center surfaces the most important civic issues of the day using automated
              news analysis. It is designed to inform, not persuade — every summary is non-partisan
              and presents facts without editorial framing.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">NEWS ANALYSIS PIPELINE</h3>
                <P>
                  Eight RSS feeds from seven newsrooms — AP News, NPR (Politics and World desks,
                  counted as one source), PBS NewsHour, BBC World, The Hill, Politico, and Roll Call
                  — are parsed hourly; opinion and editorial sections are filtered out of every
                  feed. Under common media-bias ratings this mix spans center to lean-left, with no
                  right-of-center outlet currently included — a disclosed limitation of the source
                  diet, not a neutral sample of all coverage. Each article is filtered for U.S.
                  policy relevance using embedding cosine similarity against policy area prototypes
                  — the same sentence-transformer model used throughout the platform. Articles that
                  pass the relevance threshold are clustered by semantic similarity to group
                  coverage of the same story across sources.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  TRENDING TOPIC INTEGRATION
                </h3>
                <P>
                  Clusters are ranked using a weighted combination of civic actionability (40%) —
                  whether officials are named and how closely the story resembles the ingested
                  corpus of civic documents — coverage breadth (35%), how many independent newsrooms
                  cover the story, and trending relevance (25%), whether the topic aligns with what
                  the public is actively discussing. Actionability leads because it is what makes an
                  issue something a citizen can act on; trending is weighted least because it is the
                  most volatile of the three signals. Trending signals are drawn from Google Trends
                  and Bluesky, cross-referenced with the news clusters via embedding similarity.
                  Reddit was a third source until September 2026, when it began requiring
                  authentication that this platform does not have; it was retired rather than left
                  returning nothing, because a dead source that looks like a quiet one is worse
                  than no source at all.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  THE MODEL DOES NOT WRITE THE SENTENCE
                </h3>
                <P>
                  Issue text is not generated. A model is asked only to LOCATE an assertion in one
                  article — who did something, and what they did — and the platform then checks that
                  both spans appear in the source word for word, that the source asserts one OF the
                  other rather than merely containing both, and that the span runs to the end of its
                  clause. Only then is the sentence rendered. The headline is the top article&apos;s
                  real headline; each fact carries the outlet it came from.
                </P>
                <P>
                  This replaced asking a model to write neutrally and then checking whether it had.
                  That approach failed in ways instructions cannot fix: it published an endorsement,
                  and it turned officials calling for an end to a war into a report that the war had
                  ended. Neither was a lapse in phrasing — a paraphrase can be faithful to its
                  source and still unfit to repeat. Copying cannot invent a word that is not there,
                  so the whole class of error is structurally unavailable rather than filtered.
                </P>
                <P>
                  The cost is silence. A cluster with no attributable assertion produces no issue at
                  all, and some days carry fewer issues than others. Recommended actions remain
                  procedural — contacting representatives, attending public hearings, reading
                  primary sources — never advocacy for or against a position.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">CROSS-REFERENCING</h3>
                <P>
                  When a ranked politician is involved in a trending issue, the Action Center links
                  directly to their scorecard. Related government documents from the Explore
                  database are matched using semantic search. Source articles include direct links
                  to the original reporting.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  GOVERNMENT ACTIVITY TABS
                </h3>
                <P>
                  Dedicated tabs for all three branches of government — Legislative (Senate and
                  House), Executive, and Judicial — display the most recent government documents:
                  floor speeches, executive orders, proposed rules, court opinions, and notices,
                  pulled directly from the Explore database.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">NATIONAL MONITORS</h3>
                <P>
                  When an issue persists in the news across multiple days, the system automatically
                  creates a National Monitor — a dedicated tracking page for that ongoing concern.
                  Monitors build a sourced timeline of developments, detect when separate news
                  stories are facets of the same underlying event using embedding similarity, and
                  merge duplicate monitors automatically. Monitors transition to
                  &quot;watching&quot; status when coverage subsides and reactivate when new
                  developments appear.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  YEAR-IN-REVIEW TIMELINE
                </h3>
                <P>
                  Each day&apos;s top issue is permanently recorded in a timeline that accumulates
                  throughout the calendar year. The Timeline tab provides a month-by-month
                  chronological view of what mattered most, with top policy themes calculated for
                  each month and the year as a whole. At year&apos;s end, this becomes a complete
                  &quot;Year in Review&quot; of the issues that shaped civic life.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">ELECTIONS TAB</h3>
                <P>
                  The Elections tab displays upcoming election dates, Senate races with incumbent
                  scores linked to their scorecards, and an interactive U.S. map for selecting
                  states. Selecting a state opens that state&apos;s ballot page (below).
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  INTERACTIVE GLOBAL NEWS MAP
                </h3>
                <P>
                  The World tab features a 3D interactive globe that visualizes U.S.-related
                  international news coverage. Countries mentioned in current news feeds are
                  highlighted with points scaled by article count. Clicking a country scrolls to
                  recent headlines about U.S. relations with that nation, linking to the original
                  source articles.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Party Alignment ── */}
          <Section title="CONTENT-BASED PARTY ALIGNMENT">
            <P>
              A bill&apos;s partisan alignment is determined by analyzing{" "}
              <em className="text-ink">what the bill does</em>, not how senators voted on it. This
              is a deliberate architectural decision grounded in political science methodology.
            </P>
            <P>
              The standard approach in political science — roll-call-based ideology estimation
              (DW-NOMINATE)
              <Cite id="20">Poole &amp; Rosenthal 1985</Cite>— assumes sincere voting. But as
              Clinton, Jackman &amp; Rivers (2004) note, this assumption is routinely violated by
              logrolling (vote trading), whip pressure, omnibus packaging, and tactical compromises.
              <Cite id="21">Clinton, Jackman &amp; Rivers 2004</Cite>A senator might vote for a bill
              they ideologically oppose to secure support for a different bill, or because party
              leadership made it a litmus test.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">HOW IT WORKS</h3>
                <P>
                  We implement a nearest-centroid classifier (Rocchio 1971)
                  <Cite id="22">Manning, Raghavan &amp; Schütze 2008</Cite>
                  in sentence-embedding space. Each party&apos;s known platform positions on each
                  policy area (taxes, healthcare, environment, etc.) are embedded as centroids using
                  the same sentence-transformer model used throughout the pipeline. Bill text is
                  then embedded and compared to both party centroids via cosine similarity.
                </P>
                <P>
                  The <em className="text-ink">stance direction</em> (pro/anti) disambiguates cases
                  where both parties have positions on the same topic: a &quot;pro&quot; environment
                  bill (strengthen EPA enforcement) aligns with the Democratic platform, while an
                  &quot;anti&quot; environment bill (roll back regulations) aligns with the
                  Republican platform. This encodes the saliency-plus-direction model from manifesto
                  research.
                  <Cite id="23">Laver &amp; Garry 2000</Cite>
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">TWO-SIGNAL FUSION</h3>
                <P>
                  Content analysis is the <em className="text-ink">primary</em> signal for party
                  alignment. Vote tallies from roll-call data serve as a{" "}
                  <em className="text-ink">secondary</em> refinement. When both agree, confidence is
                  high. When they disagree, content wins unless the vote data shows a clear
                  party-line split (which is itself informative — the bill was important enough to
                  whip). This follows Snyder &amp; Groseclose (2000) who demonstrated that vote
                  outcomes reflect party discipline as much as ideology.
                  <Cite id="24">Snyder &amp; Groseclose 2000</Cite>
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">ADAPTIVE LEARNING</h3>
                <P>
                  Platform position descriptions are seed prototypes bootstrapped from published
                  party platforms. As the pipeline processes bills, sponsor party data from
                  Congress.gov serves as supervised ground truth — bills sponsored by a single party
                  are labeled examples that refine the classifier over time. This follows the
                  self-training paradigm.
                  <Cite id="25">Yarowsky 1995</Cite>
                </P>
              </div>
            </div>
          </Section>

          {/* ── Classification Pipeline ── */}
          <Section title="CLASSIFICATION AND NLP PIPELINE">
            <P>
              The pipeline classifies thousands of entities (bills, donors, industries, votes) per
              run. We use a tiered strategy that reserves expensive techniques for cases where
              cheaper methods fail, following the computational parsimony principle.
              <Cite id="12">Jurafsky &amp; Martin 2023</Cite>
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  BILL POLICY AREA CLASSIFICATION
                </h3>
                <P>
                  Bills and votes are classified into 18 policy areas (healthcare, defense, energy,
                  etc., plus a procedural catch-all for non-substantive motions) using a tiered
                  adaptive strategy:
                </P>
                <ul className="space-y-2 mt-2 text-sm text-ink-lo">
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">1.</span>
                    <span>
                      <span className="text-ink-lo">Learning store exact match</span> — bills
                      classified in prior pipeline runs are recalled instantly by ID. This is the
                      experience replay pattern.
                      <Cite id="10">Lin 1992</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">2.</span>
                    <span>
                      <span className="text-ink-lo">kNN against reference corpus</span> — the k=7
                      most similar previously-classified bills in the vector store are retrieved and
                      the policy area is assigned by similarity-weighted majority vote.
                      <Cite id="9">Cover &amp; Hart 1967</Cite>
                      This is retrieval-augmented classification: the reference corpus grows with
                      each pipeline run, improving accuracy over time.
                      <Cite id="26">Lewis et al. 2020</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">3.</span>
                    <span>
                      <span className="text-ink-lo">
                        Embedding similarity against policy descriptions
                      </span>{" "}
                      — cosine similarity between bill text embeddings and pre-computed policy area
                      description embeddings. This is the cold-start fallback using nearest-centroid
                      classification.
                      <Cite id="7">Reimers &amp; Gurevych 2019</Cite>
                    </span>
                  </li>
                </ul>
                <P>
                  The policy taxonomy is based on the Congressional Research Service (CRS) policy
                  area scheme used by Congress.gov. The approach follows the text-as-data paradigm
                  reviewed in Grimmer &amp; Stewart (2013).
                  <Cite id="27">Grimmer &amp; Stewart 2013</Cite>
                  Stance derivation (pro/anti/neutral) uses embedding cosine similarity against
                  direction prototypes — the bill text is compared to semantic signatures of
                  supportive, restrictive, and reform-oriented legislative language, following the
                  Comparative Agendas Project coding tradition.
                  <Cite id="28">Baumgartner &amp; Jones 1993</Cite>
                  Zero LLM calls are used for bill classification.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  DONOR AND INDUSTRY CLASSIFICATION
                </h3>
                <P>Donor classification uses a five-tier strategy:</P>
                <ul className="space-y-2 mt-2 text-sm text-ink-lo">
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">1.</span>
                    <span>
                      <span className="text-ink-lo">FEC metadata</span> — structured fields from the
                      Federal Election Commission API encode committee type and designation codes,
                      providing ground-truth classification for PACs vs. individual donors.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">2.</span>
                    <span>
                      <span className="text-ink-lo">Semantic detection</span> — embedding cosine
                      similarity against category prototypes replaces ~200 lines of hardcoded string
                      patterns. This generalizes to unseen entities because distributed
                      representations capture semantic meaning.
                      <Cite id="29">Bengio et al. 2003</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">3.</span>
                    <span>
                      <span className="text-ink-lo">Learning store lookup</span> — previously
                      classified entities are recalled instantly by name.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">4.</span>
                    <span>
                      <span className="text-ink-lo">Embedding cosine similarity</span> — donor names
                      are compared against pre-computed industry description embeddings. Industry
                      descriptions include exemplar company names as anchoring tokens, following the
                      zero-shot classification setup.
                      <Cite id="30">Yin, Hay &amp; Roth 2019</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">5.</span>
                    <span>
                      <span className="text-ink-lo">k-Nearest Neighbor (kNN)</span> — remaining
                      unclassified donors are classified by the k=7 most similar already-labeled
                      entities using distance-weighted majority voting.
                      <Cite id="9">Cover &amp; Hart 1967</Cite>
                      This mirrors prototypical networks for few-shot learning
                      <Cite id="14">Snell et al. 2017</Cite>
                      where classification is performed by comparing query embeddings to accumulated
                      real examples.
                    </span>
                  </li>
                </ul>
                <P>
                  The kNN approach was chosen over LLM-based classification after empirical testing
                  showed the LLM hallucinated invalid categories (producing labels like
                  &quot;SPORTS&quot; or &quot;RESTAURANT&quot; outside the valid taxonomy) and was
                  orders of magnitude slower. The kNN classifier processes ~5,000 donors in under 5
                  seconds versus 40+ minutes for the LLM, with more consistent results.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  LEARNING STORE AND ADAPTIVE CLASSIFICATION
                </h3>
                <P>
                  All classifications are persisted in a learning store (SQLite table) that
                  functions as an evolving knowledge base. On subsequent pipeline runs, previously
                  classified entities are retrieved instantly without recomputation. This is
                  analogous to experience replay in reinforcement learning
                  <Cite id="10">Lin 1992</Cite> — past decisions inform future ones, improving both
                  speed and accuracy over time.
                </P>
                <P>
                  The learning store also feeds into the self-training loop
                  <Cite id="25">Yarowsky 1995</Cite> — high-confidence classifications from prior
                  runs become labeled examples for kNN and reference corpus retrieval in future
                  runs. kNN&apos;s own guesses are stored but never reused as examples, so an
                  early mistake can&apos;t teach later runs to repeat it.
                </P>
                <P>
                  To prevent stale data from persisting when analysis algorithms are updated, the
                  pipeline implements{" "}
                  <em className="text-ink">version-aware artifact management</em>. At the start of
                  each run, a SHA-256 fingerprint of all analysis source files (ignoring comments
                  and documentation) is compared to the
                  stored hash from the previous run. If the code is unchanged, all learning data is
                  preserved to promote self-training. If the code has changed, stale artifacts (LLM
                  results, learned classifications, kNN reference corpus) are automatically cleared
                  so updated algorithms start fresh. The API cache (raw data from government APIs)
                  is never cleared.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  SEMANTIC SEARCH (EXPLORE)
                </h3>
                <P>
                  The Explore feature uses dense passage retrieval
                  <Cite id="11">Karpukhin et al. 2020</Cite> to enable free-text search over
                  government documents — Senate and House floor speeches, presidential actions
                  (executive orders, proclamations, memoranda), Supreme Court opinions, and Federal
                  Register rulemaking documents. Bill text is <em>not</em> indexed here; it is used
                  separately, title-only, for the kNN bill-classification step in the scoring
                  pipeline. Each document gets a single embedding (no chunking) over its title,
                  summary, and first 800 characters of body, encoded with Snowflake all-MiniLM-L6-v2
                  and stored in sqlite-vec for nearest-neighbour retrieval. This outperforms keyword
                  search (BM25) for conceptual queries like &quot;climate policy&quot; where exact
                  term overlap is low.
                </P>
              </div>
            </div>
          </Section>

          {/* ── AI Usage ── */}
          <Section title="HOW AI IS USED">
            <P>
              Civitas uses two types of AI models, each for the task it is best suited for. AI is
              never used to generate scores directly — all scores are computed by deterministic,
              auditable formulas.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  EMBEDDING MODELS (CLASSIFICATION + SEARCH)
                </h3>
                <P>
                  Two sentence-transformers, both 384-dimensional and both around 22M parameters.{" "}
                  <Label>Snowflake Arctic-XS</Label> handles classification: bill policy areas,
                  donor industries, party alignment, motion types, and the k-nearest-neighbour
                  reference corpus.
                  <Label> all-MiniLM-L6-v2</Label>
                  <Cite id="8">Wang et al. 2020</Cite>
                  handles the semantic-search index and the Action Center&apos;s similarity gates.
                  Sentence-transformers produce dense vector representations where cosine similarity
                  correlates with semantic similarity
                  <Cite id="7">Reimers &amp; Gurevych 2019</Cite> — making them ideal for
                  classification-by-comparison tasks where category definitions exist.
                </P>
                <P>
                  The split is measured, not accidental. Arctic is retrieval-<em>asymmetric</em>: it
                  packs same-register text into a narrow raw-cosine band (~0.55-0.87), which left
                  several similarity thresholds unable to separate genuine matches from noise.
                  Against this platform&apos;s own live failure cases, all-MiniLM-L6-v2 measured
                  roughly 4x the separation margin on document anchoring and 3x on policy relevance.
                  Classification stays on Arctic because its thresholds were calibrated against that
                  model&apos;s geometry — moving a classification gate to a different embedding
                  space without re-measuring the threshold is how thresholds quietly stop meaning
                  anything.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  LLM (NATURAL LANGUAGE SYNTHESIS)
                </h3>
                <P>
                  <Label>LFM2.5-1.2B-Instruct</Label> via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> handles tasks requiring natural language
                  understanding and multi-step reasoning. Per-senator/rep voting-pattern
                  narratives, key-vote reasoning, and LLM-based PAC identification used to run
                  here too — all three were removed in 2026-07 after live audits found the
                  output unreliable regardless of prompting approach:
                </P>
                <div className="space-y-2 mt-2">
                  <Row
                    label="Action Center issues"
                    value="Synthesizes clustered news coverage into a structured issue: title, summary, and grounded facts"
                  />
                  <Row
                    label="Justice profile summary"
                    value="Writes a short jurisprudential profile for each of the 9 Supreme Court justices from pre-computed voting statistics"
                  />
                  <Row
                    label="Explore summaries"
                    value="On-demand summaries of how a government document relates to a user's search query"
                  />
                </div>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">WHAT AI DOES NOT DO</h3>
                <div className="space-y-2">
                  <Row
                    label="Score calculation"
                    value="All sub-scores use deterministic formulas with no LLM input. The math is fully auditable."
                  />
                  <Row
                    label="Bill classification"
                    value="Policy areas, party alignment, and stance are all embedding-based — no LLM in the loop."
                  />
                  <Row
                    label="Donor classification"
                    value="FEC metadata + embeddings + kNN handle all donor and industry classification."
                  />
                  <Row
                    label="Data fabrication"
                    value="The LLM only analyzes data already fetched from official APIs. It does not generate or invent facts."
                  />
                  <Row
                    label="Partisan framing"
                    value="Prompts are explicitly structured to avoid editorial framing. The LLM analyzes behavior, not ideology."
                  />
                  <Row
                    label="Ballot measures"
                    value="No AI touches ballot content at any stage. Titles, summaries, fiscal statements and yes/no descriptions are reproduced verbatim from official sources — see below for why we ruled out a plain-language rewrite."
                  />
                </div>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  WHY BALLOT MEASURES GET NO AI SUMMARY
                </h3>
                <P>
                  Ballot language is genuinely hard to read — statewide measures averaged a
                  grade-21 reading level in 2025, the highest since Ballotpedia began
                  tracking, and research finds voters skip measures whose titles read harder.
                  A plain-language layer would be genuinely useful. We built the rest of the
                  feature and left that part out anyway.
                </P>
                <P>
                  The reason is that the one error that matters most here is undetectable by
                  the checks we run. Our safeguards verify that every number, name and claim
                  in generated text appears in the source material. A model that writes
                  &ldquo;a YES vote repeals this tax&rdquo; when the official text says approval
                  <em> retains</em> it passes every one of those checks, because every word it
                  used <em>is</em> in the source — the checks confirm where words came from,
                  not that a statement points the same direction as its source. On a page that
                  can change how somebody votes, an error class we cannot detect is not one we
                  are willing to ship, so the official words stand on their own with the
                  drafter named.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  WHY THESE TECHNIQUES WERE CHOSEN
                </h3>
                <P>
                  We follow a strict hierarchy: structured metadata first, then embedding
                  similarity, then kNN, then LLM — reserving each more expensive technique only for
                  tasks the cheaper ones cannot handle. The pipeline contains zero hardcoded keyword
                  lists, regex patterns, or string-matching heuristics for classification decisions.
                  Every classification is made mathematically via embedding cosine similarity
                  against natural-language prototypes.
                  <Cite id="12">Jurafsky &amp; Martin 2023</Cite>
                </P>
                <P>
                  Embeddings (not LLM) for classification: sentence embeddings excel at text
                  classification tasks when labeled examples or category descriptions exist. They
                  are deterministic, fast, and avoid the hallucination risks inherent in generative
                  models.
                  <Cite id="13">Minaee et al. 2021</Cite>
                  The kNN classifier further leverages accumulated labeled data as a growing
                  reference set — a well-established approach in few-shot and semi-supervised
                  learning settings.
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
                  LLM for natural language synthesis: tasks like clustering same-story news
                  coverage into a structured issue require multi-step reasoning that embeddings
                  alone cannot provide. These are inherently generative tasks suited to language
                  models.
                  <Cite id="15">Wei et al. 2022</Cite>
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">MODEL AND ARCHITECTURE</h3>
                <P>
                  The inference model is <Label>LFM2.5-1.2B-Instruct</Label>, a compact open-weight
                  language model running natively via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> compiled with ARM-specific optimizations
                  (cortex-a76, dot-product, fp16). This provides faster inference compared to
                  containerized runtimes, generating ~14 tokens/second on the Raspberry Pi 5 CPU.
                  Results are cached in a local database so each unique analysis is computed at most
                  once.
                </P>
                <P>
                  The embedding models are <Label>Snowflake Arctic-XS</Label> for classification
                  (bills, donors, industries, party alignment) and
                  <Label> all-MiniLM-L6-v2</Label>
                  <Cite id="8">Wang et al. 2020</Cite> for the search index and similarity gates —
                  both 384-dimensional, both in the 22M-parameter class, so carrying two costs
                  little. Vectors are stored in sqlite-vec, a single-file SQLite extension that runs
                  on the Pi with no separate vector server. Every model runs entirely on-device with
                  no external API calls.
                </P>
              </div>
            </div>
          </Section>

          {/* ── Data Sources ── */}
          <Section title="DATA SOURCES AND APIs">
            <P>
              All data is sourced from official US government APIs and public records. No data is
              purchased, scraped from paywalled sources, or fabricated.
            </P>

            <div className="space-y-2 mt-4">
              <h3 className="text-xs text-ink-lo tracking-widest">
                CONGRESSIONAL DATA (SENATE &amp; HOUSE)
              </h3>
              <Row
                label="Congress.gov API"
                value="Bill text, voting records, member data, sponsored legislation, and bill sponsor party affiliation — both chambers"
              />
              <Row
                label="FEC API (fec.gov)"
                value="Campaign finance data: individual contributions, PAC donations, committee filings, disbursements, and committee type codes — both chambers"
              />
              <Row
                label="GovInfo API"
                value="Full bill text for policy area classification, Congressional Record floor proceedings for advocacy analysis — both chambers"
              />
              <Row
                label="Senate.gov"
                value="Official senator websites scraped for platform text (used for the platform summary) and roll-call vote records with per-member votes — Senate only. Campaign-promise extraction from this same platform text was tried four times and removed entirely in 2026-07 (see AI Usage above), for both chambers"
              />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-ink-lo tracking-widest">PRESIDENTIAL DATA</h3>
              <Row
                label="Federal Register API"
                value="Executive order counts and metadata from federalregister.gov (Clinton onward, no API key required)"
              />
              <Row
                label="BLS API"
                value="Bureau of Labor Statistics public API — total nonfarm employment payrolls for jobs-created calculations"
              />
              <Row
                label="C-SPAN Historians Survey"
                value="Presidential Historians Survey (2021 cycle) — the basis for the Historical Legacy dimension"
              />
              <Row
                label="American Presidency Project (UCSB)"
                value="Presidential roster and identity data, approval polling for modern presidents (Truman onward), and pre-polling-era election margins. Replaced Gallup, which ended presidential approval tracking in February 2026"
              />
              <Row
                label="BEA NIPA Tables / FRED"
                value="Bureau of Economic Analysis GDP growth data for the modern era"
              />
              <Row
                label="MeasuringWorth"
                value="Real-GDP series (1790-present) for presidents predating BEA coverage"
              />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-ink-lo tracking-widest">EXPLORE FEATURE</h3>
              <Row
                label="Congressional Record (GovInfo)"
                value="Senate and House floor proceedings — speaker-attributed transcripts from daily CREC packages"
              />
              <Row
                label="Federal Register"
                value="Executive orders, presidential memoranda, and proclamations with full text and metadata, plus proposed and final rules — including ones still open for public comment, surfaced with their comment link and deadline"
              />
              <Row
                label="Oyez / supremecourt.gov"
                value="Supreme Court opinions, indexed alongside the legislative and executive documents"
              />
              <Row
                label="Semantic Search"
                value="Documents embedded with all-MiniLM-L6-v2 into a sqlite-vec table for dense passage retrieval — one embedding per document, no chunking"
              />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-ink-lo tracking-widest">ELECTIONS &amp; BALLOT MEASURES</h3>
              <Row
                label="FEC API (fec.gov)"
                value="Declared candidates for every federal race in the cycle, plus per-candidate fundraising totals, cash on hand, and disbursements"
              />
              <Row
                label="Vote Smart (votesmart.org)"
                value="Statewide ballot measures — official ballot title, official summary, fiscal impact, and the state's own yes/no descriptions, stored and displayed verbatim. Free API from a nonpartisan nonprofit; optional, and the feature reports which states it lacks rather than implying they have no measures"
              />
              <Row
                label="Secretary of State offices"
                value="The authoritative source for ballot text, linked from every measure. Direct per-state ingestion is the intended upgrade to Vote Smart"
              />
              <Row
                label="Google Civic Information API"
                value="Powers the optional town-level local-races selector on a small, hand-picked list of towns. Called with a fixed, public address we chose for that town (its own town hall), never a visitor's own address — see State Ballots &amp; Ballot Measures above. Free API; optional, and unset means the selector doesn't appear"
              />
              <Row
                label="USAGov election office directory"
                value="Where each state ballot page sends you for the parts of your ballot that are not statewide. Per-state deep links are only shown after an automated check confirms the URL still resolves"
              />
              <Row
                label="MIT Election Lab / county canvasses"
                value="Presidential returns behind the Cook-PVI-style partisan lean shown per state and district (see the methodology note on the elections pages)"
              />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-ink-lo tracking-widest">SUPREME COURT DATA</h3>
              <Row
                label="Oyez Project API"
                value="Case metadata, justice votes, oral argument transcripts, and decision breakdowns"
              />
              <Row
                label="supremecourt.gov"
                value="Official slip opinion PDFs linked directly from case records"
              />
            </div>

            <div className="space-y-2 mt-6">
              <h3 className="text-xs text-ink-lo tracking-widest">RATE LIMITING</h3>
              <P>
                The pipeline respects all API rate limits: Congress.gov at 1.2 requests/second, FEC
                at 0.25 req/s, GovInfo at 1.0 req/s, and BLS at 25 queries/day. Data is cached for
                72 hours to minimize redundant API calls.
              </P>
            </div>
          </Section>

          {/* ── Environmental ── */}
          <Section title="ENVIRONMENTAL AND ETHICAL CONSIDERATIONS">
            <div className="space-y-4">
              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  LOCAL-FIRST ARCHITECTURE
                </h3>
                <P>
                  The entire Civitas stack runs on a single Raspberry Pi 5 (16GB RAM) with an NVMe
                  SSD. There are no cloud GPU instances, no third-party AI API calls, and no data
                  sent to external services for processing. The LLM, embedding model, vector
                  database, SQLite database, backend API, and frontend all run on the same device.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">ENERGY FOOTPRINT</h3>
                <P>
                  A Raspberry Pi 5 draws approximately 5-12 watts under load. Running the full data
                  pipeline (100 senators, ~100 LLM calls) takes several hours but consumes roughly
                  the energy of a single LED light bulb. By comparison, a typical cloud GPU instance
                  (NVIDIA A100) draws 250-400 watts.
                  <Cite id="17">Patterson et al. 2021</Cite>
                  This project demonstrates that meaningful AI analysis does not require
                  industrial-scale compute. The trade-off is speed: what a cloud GPU processes in
                  minutes takes hours on a Pi. We consider that an acceptable trade for a nightly
                  batch pipeline.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">DATA PRIVACY</h3>
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
                  UCSB&apos;s American Presidency Project (presidency.ucsb.edu) and
                  MeasuringWorth&apos;s historical GDP dataset (measuringworth.com).
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">OPEN-WEIGHT MODEL</h3>
                <P>
                  We deliberately chose LFM2.5, an open-weight model, over proprietary alternatives
                  like GPT-4 or Claude. This means: no per-token API costs that could make the
                  project financially unsustainable, no dependency on a third-party company&apos;s
                  continued service, full auditability of the model&apos;s behavior, and no user
                  queries or government data leaving the device.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-ink-lo tracking-widest mb-2">
                  LIMITATIONS AND HONESTY
                </h3>
                <P>This project has real limitations and we believe in stating them clearly:</P>
                <ul className="space-y-2 mt-2 text-sm text-ink-lo">
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">-</span>
                    <span>
                      A 1.2B parameter model is less capable than larger models. It occasionally
                      produces imprecise key-vote reasoning. We mitigate this with caching,
                      post-processing heuristics, and deterministic overrides where the model output
                      can be verified against structured data.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">-</span>
                    <span>
                      Presidential scoring used to include two dimensions, Independence and
                      Follow-Through, that were a one-time hand-set number for every president with
                      no live formula behind them at all. We removed both entirely (2026-07) rather
                      than keep presenting a hand-set number as a computed score, and rebuilt every
                      remaining dimension — plus each president&apos;s identity data — on real live
                      and historical datasets, with no seeded or hand-typed fallback left anywhere
                      in the pipeline. A third dimension, Competence, was later removed too: its
                      only live component (executive-order activity rate) measured no relationship
                      (Spearman 0.097) with real administrative-skill judgment. See the Presidents
                      methodology below for the full account. Agency Alignment has no
                      machine-readable rulemaking data before Clinton (a real digitization wall in
                      the underlying government sources, checked directly rather than assumed) and
                      shows N/A for earlier presidents rather than a proxy score.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">-</span>
                    <span>
                      Correlation between donations and votes does not prove causation. A senator
                      who receives PAC money and votes favorably may be doing so for policy reasons
                      unrelated to the donation. We follow the methodological caution urged by
                      Ansolabehere et al. (2003).<Cite id="18">Ansolabehere et al. 2003</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">-</span>
                    <span>
                      Content-based party alignment depends on the quality of platform position
                      descriptions. While these are seeded from published party platforms and
                      refined by sponsor data, edge cases involving bipartisan or cross-cutting
                      legislation may be misclassified.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">-</span>
                    <span>
                      FEC data has inherent reporting delays. Campaign finance filings may lag
                      real-time donations by weeks or months.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-signal-amber shrink-0">-</span>
                    <span>
                      Embedding-based classification, while fast and consistent, lacks the world
                      knowledge that a large model or human expert would bring. Edge cases involving
                      shell companies or deliberately obscure entity names may be misclassified.
                    </span>
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
              <Row
                label="Embedding Models"
                value="Two, both 384-dim / ~22M params: Snowflake Arctic-XS for classification, all-MiniLM-L6-v2 for the search index and similarity gates"
              />
              <Row label="LLM Runtime" value="llama.cpp (native ARM build), LFM2.5-1.2B-Instruct" />
              <Row
                label="Vector Database"
                value="sqlite-vec (vec0 virtual tables in a local SQLite file, cosine distance)"
              />
              <Row
                label="Containers"
                value="Docker Swarm (single node) — zero-downtime start-first rolling updates behind an in-stack nginx reverse proxy, with automatic rollback on a failed health check"
              />
              <Row label="Pipeline Schedule" value="Nightly at 3:00 AM via APScheduler" />
              <Row label="Data Caching" value="72-hour TTL with persistent SQLite cache" />
              <Row
                label="Learning Store"
                value="SQLite table for persistent classification memory, version-aware invalidation on code change"
              />
              <Row
                label="Pipeline Optimization"
                value="Producer-consumer threading: embedding prefetch overlaps LLM inference, context compression for prompts"
              />
              <Row
                label="API Pagination"
                value="Server-side paginated voting records with filter support"
              />
              <Row
                label="Sponsorship Analysis"
                value="PageRank (leadership) + SVD (ideology) on cosponsorship matrix"
              />
              <Row
                label="Classification"
                value="Zero hardcoded rules — all classifications via embedding similarity or kNN"
              />
              <Row
                label="Metric Tooltips"
                value="Every scorecard metric has a [?] tooltip explaining what it measures"
              />
              <Row
                label="Branches Covered"
                value="Senate (100), House (435), Presidents (historical + modern), Supreme Court (9 justices)"
              />
              <Row
                label="Action Center"
                value="Hourly news analysis with national monitors for ongoing concerns and year-in-review timeline tracking"
              />
              <Row
                label="News Sources"
                value="AP News, NPR (Politics + World), PBS NewsHour, BBC World, The Hill, Politico, Roll Call — opinion sections filtered; mix spans center to lean-left (no right-of-center outlet currently included)"
              />
              <Row
                label="Trending Integration"
                value="Google Trends RSS + Bluesky trending, cross-referenced via embedding similarity"
              />
              <Row
                label="Globe Visualization"
                value="react-globe.gl — interactive 3D globe for international news mapping"
              />
            </div>
          </Section>

          {/* ── How It's Built ── */}
          <Section title="HOW IT'S BUILT">
            <P>
              Civitas is built and maintained by a single developer as a hobby project, running on a
              home server. It exists to prove that meaningful civic accountability tools do not
              require venture capital, a team of engineers, or enterprise cloud infrastructure.
              Anyone with the knowledge, time, and a modest machine can build something like this.
            </P>
            <div className="space-y-2 mt-4">
              <Row label="SERVER" value="Raspberry Pi 5 — a $80 single-board computer" />
              <Row
                label="LOCAL LLM"
                value="LFM2.5-1.2B-Instruct via llama.cpp (Ollama as fallback) · runs entirely on-device, zero API cost"
              />
              <Row label="DATABASE" value="SQLite · no cloud database, no managed service" />
              <Row label="DEPLOYMENT" value="Docker Swarm rolling updates on a single machine" />
              <Row
                label="EXTERNAL APIs"
                value="Congress.gov, FEC.gov, Federal Register — all free and open"
              />
              <Row label="MONTHLY COST" value="~$5–10 (electricity)" />
              <Row label="CLOUD SERVICES" value="None" />
              <Row label="VENTURE CAPITAL" value="None" />
            </div>
            <P>
              The pipeline runs overnight, the site serves from a home IP address, and the entire
              codebase is documented above. If you want to build something similar, everything you
              need to know about the methodology is on this page.
            </P>
          </Section>

          {/* ── References ── */}
          <Section title="REFERENCES">
            <ol className="space-y-3">
              <Ref id="1">
                Bonica, A. (2014). Mapping the Ideological Marketplace.{" "}
                <em className="text-ink-lo">American Journal of Political Science</em>, 58(2),
                367-386. doi:10.1111/ajps.12062
              </Ref>
              <Ref id="2">
                Naurin, E. (2011).{" "}
                <em className="text-ink-lo">
                  Election Promises, Party Behaviour and Voter Perceptions
                </em>
                . Palgrave Macmillan. doi:10.1057/9780230304598
              </Ref>
              <Ref id="3">
                Martin, S. (2011). Using Parliamentary Questions to Measure Constituency Focus.{" "}
                <em className="text-ink-lo">Political Studies</em>, 59(2), 472-488.
                doi:10.1111/j.1467-9248.2011.00885.x
              </Ref>
              <Ref id="4">
                Carson, J. L., Koger, G., Lebo, M. J., &amp; Young, E. (2010). The Electoral Costs
                of Party Loyalty in Congress.{" "}
                <em className="text-ink-lo">American Journal of Political Science</em>, 54(3),
                598-616. doi:10.1111/j.1540-5907.2010.00449.x
              </Ref>
              <Ref id="5">
                Stratmann, T. (2005). Some Talk: Money in Politics. A (Partial) Review of the
                Literature. <em className="text-ink-lo">Public Choice</em>, 124(1-2), 135-156.
                doi:10.1007/s11127-005-4750-3
              </Ref>
              <Ref id="6">
                Rhoades, S. A. (1993). The Herfindahl-Hirschman Index.{" "}
                <em className="text-ink-lo">Federal Reserve Bulletin</em>, 79, 188-189.
              </Ref>
              <Ref id="7">
                Reimers, N. &amp; Gurevych, I. (2019). Sentence-BERT: Sentence Embeddings using
                Siamese BERT-Networks.{" "}
                <em className="text-ink-lo">Proceedings of EMNLP-IJCNLP 2019</em>, 3982-3992.
                doi:10.18653/v1/D19-1410
              </Ref>
              <Ref id="8">
                Wang, W., Wei, F., Dong, L., Bao, H., Yang, N., &amp; Zhou, M. (2020). MiniLM: Deep
                Self-Attention Distillation for Task-Agnostic Compression of Pre-Trained
                Transformers. <em className="text-ink-lo">Proceedings of NeurIPS 2020</em>.
                arXiv:2002.10957
              </Ref>
              <Ref id="9">
                Cover, T. &amp; Hart, P. (1967). Nearest Neighbor Pattern Classification.{" "}
                <em className="text-ink-lo">IEEE Transactions on Information Theory</em>, 13(1),
                21-27. doi:10.1109/TIT.1967.1053964
              </Ref>
              <Ref id="10">
                Lin, L.-J. (1992). Self-improving reactive agents based on reinforcement learning,
                planning and teaching. <em className="text-ink-lo">Machine Learning</em>, 8(3-4),
                293-321. doi:10.1007/BF00992699
              </Ref>
              <Ref id="11">
                Karpukhin, V., Oguz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., &amp;
                Yih, W. (2020). Dense Passage Retrieval for Open-Domain Question Answering.{" "}
                <em className="text-ink-lo">Proceedings of EMNLP 2020</em>, 6769-6781.
                doi:10.18653/v1/2020.emnlp-main.550
              </Ref>
              <Ref id="12">
                Jurafsky, D. &amp; Martin, J. H. (2023).{" "}
                <em className="text-ink-lo">Speech and Language Processing</em> (3rd ed. draft).
                Stanford University.
              </Ref>
              <Ref id="13">
                Minaee, S., Kalchbrenner, N., Cambria, E., Nikzad, N., Chenaghlu, M., &amp; Gao, J.
                (2021). Deep Learning-Based Text Classification: A Comprehensive Review.{" "}
                <em className="text-ink-lo">ACM Computing Surveys</em>, 54(3), 1-40.
                doi:10.1145/3439726
              </Ref>
              <Ref id="14">
                Snell, J., Swersky, K., &amp; Zemel, R. (2017). Prototypical Networks for Few-Shot
                Learning. <em className="text-ink-lo">Proceedings of NeurIPS 2017</em>, 4077-4087.
                arXiv:1703.05175
              </Ref>
              <Ref id="15">
                Wei, J., Wang, X., Schuurmans, D., Bosma, M., Ichter, B., Xia, F., Chi, E., Le, Q.,
                &amp; Zhou, D. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large
                Language Models. <em className="text-ink-lo">Proceedings of NeurIPS 2022</em>.
                arXiv:2201.11903
              </Ref>
              <Ref id="16">
                Gerganov, G. (2023). llama.cpp: Inference of LLaMA model in pure C/C++. GitHub.
                github.com/ggerganov/llama.cpp
              </Ref>
              <Ref id="17">
                Patterson, D., Gonzalez, J., Le, Q., Liang, C., Munguia, L.-M., Rothchild, D., So,
                D., Texier, M., &amp; Dean, J. (2021). Carbon Emissions and Large Neural Network
                Training. <em className="text-ink-lo">arXiv:2104.10350</em>.
              </Ref>
              <Ref id="18">
                Ansolabehere, S., de Figueiredo, J. M., &amp; Snyder, J. M. (2003). Why Is There So
                Little Money in U.S. Politics?{" "}
                <em className="text-ink-lo">Journal of Economic Perspectives</em>, 17(1), 105-130.
                doi:10.1257/089533003321164976
              </Ref>
              <Ref id="19">
                Efron, B. &amp; Morris, C. (1975). Data Analysis Using Stein&apos;s Estimator and
                Its Generalizations.{" "}
                <em className="text-ink-lo">Journal of the American Statistical Association</em>,
                70(350), 311-319. doi:10.2307/2285814
              </Ref>
              <Ref id="20">
                Poole, K. T. &amp; Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call
                Analysis. <em className="text-ink-lo">American Journal of Political Science</em>,
                29(2), 357-384. doi:10.2307/2111172
              </Ref>
              <Ref id="21">
                Clinton, J., Jackman, S., &amp; Rivers, D. (2004). The Statistical Analysis of Roll
                Call Data. <em className="text-ink-lo">American Political Science Review</em>,
                98(2), 355-370. doi:10.1017/S0003055404001194
              </Ref>
              <Ref id="22">
                Manning, C. D., Raghavan, P., &amp; Schütze, H. (2008).{" "}
                <em className="text-ink-lo">Introduction to Information Retrieval</em>. Cambridge
                University Press. Ch. 14: Vector Space Classification.
              </Ref>
              <Ref id="23">
                Laver, M. &amp; Garry, J. (2000). Estimating Policy Positions from Political Texts.{" "}
                <em className="text-ink-lo">American Journal of Political Science</em>, 44(3),
                619-634. doi:10.2307/2669268
              </Ref>
              <Ref id="24">
                Snyder, J. M. &amp; Groseclose, T. (2000). Estimating Party Influence in
                Congressional Roll-Call Voting.{" "}
                <em className="text-ink-lo">American Journal of Political Science</em>, 44(2),
                193-211. doi:10.2307/2669305
              </Ref>
              <Ref id="25">
                Yarowsky, D. (1995). Unsupervised Word Sense Disambiguation Rivaling Supervised
                Methods. <em className="text-ink-lo">Proceedings of ACL 1995</em>, 189-196.
                doi:10.3115/981658.981684
              </Ref>
              <Ref id="26">
                Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., Küttler,
                H., Lewis, M., Yih, W., Rocktäschel, T., Riedel, S., &amp; Kiela, D. (2020).
                Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.{" "}
                <em className="text-ink-lo">Proceedings of NeurIPS 2020</em>. arXiv:2005.11401
              </Ref>
              <Ref id="27">
                Grimmer, J. &amp; Stewart, B. M. (2013). Text as Data: The Promise and Pitfalls of
                Automatic Content Analysis Methods for Political Texts.{" "}
                <em className="text-ink-lo">Political Analysis</em>, 21(3), 267-297.
                doi:10.1093/pan/mps028
              </Ref>
              <Ref id="28">
                Baumgartner, F. R. &amp; Jones, B. D. (1993).{" "}
                <em className="text-ink-lo">Agendas and Instability in American Politics</em>.
                University of Chicago Press.
              </Ref>
              <Ref id="29">
                Bengio, Y., Ducharme, R., Vincent, P., &amp; Jauvin, C. (2003). A Neural
                Probabilistic Language Model.{" "}
                <em className="text-ink-lo">Journal of Machine Learning Research</em>, 3, 1137-1155.
              </Ref>
              <Ref id="30">
                Yin, W., Hay, J., &amp; Roth, D. (2019). Benchmarking Zero-shot Text Classification:
                Datasets, Evaluation and Entailment Approach.{" "}
                <em className="text-ink-lo">Proceedings of EMNLP 2019</em>, 3914-3923.
                doi:10.18653/v1/D19-1404
              </Ref>
              <Ref id="31">
                Laver, M., Benoit, K., &amp; Garry, J. (2003). Extracting Policy Positions from
                Political Texts Using Words as Data.{" "}
                <em className="text-ink-lo">American Political Science Review</em>, 97(2), 311-331.
                doi:10.1017/S0003055403000698
              </Ref>
              <Ref id="32">
                Brin, S. &amp; Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search
                Engine.{" "}
                <em className="text-ink-lo">
                  Proceedings of the 7th International World Wide Web Conference
                </em>
                , 107-117.
              </Ref>
              <Ref id="33">
                Tauberer, J. (2012). <em className="text-ink-lo">Open Government Data: The Book</em>
                . GovTrack.us methodology for ideology and leadership scoring via cosponsorship
                analysis. govtrack.us/about/analysis
              </Ref>
              <Ref id="34">
                Volden, C. &amp; Wiseman, A. E. (2014).{" "}
                <em className="text-ink-lo">
                  Legislative Effectiveness in the United States Congress: The Lawmakers
                </em>
                . Cambridge University Press.
              </Ref>
              <Ref id="35">
                Canes-Wrone, B., Brady, D. W., &amp; Cogan, J. F. (2002). Out of Step, Out of
                Office: Electoral Accountability and House Members&apos; Voting.{" "}
                <em className="text-ink-lo">American Political Science Review</em>, 96(1), 127-140.
              </Ref>
              <Ref id="36">
                Nokken, T. P., &amp; Poole, K. T. (2004). Congressional Party Defection in American
                History. <em className="text-ink-lo">Legislative Studies Quarterly</em>, 29(4),
                545-568.
              </Ref>
            </ol>
          </Section>

          <div className="text-center mt-8">
            <p className="text-ink-min text-xs">
              Questions about our methodology? Disagree with a score? We welcome scrutiny. This
              project is built on the belief that transparency is non-negotiable.
            </p>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
