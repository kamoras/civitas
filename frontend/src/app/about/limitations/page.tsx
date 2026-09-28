import type { ReactNode } from "react";
import { pageMetadata } from "@/lib/site";
import { AboutPage, Summary, Point, Section, P, More, Cite, A } from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "Known Limitations of Civitas Scores",
  description:
    "What Civitas's scores and pages can't tell you, stated plainly — funding and party, partisan lean as a stand-in for opinion, ballot coverage, news sources — with the reason each gap is still open.",
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

export default function LimitationsChapter() {
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
          Average scores differ by party because the parties fundraise differently — not because the
          formula treats them differently.
        </Point>
        <Point>
          &ldquo;What a seat expects&rdquo; comes from how it votes for president: a broad stand-in
          for local opinion, not a measure of it.
        </Point>
        <Point>
          Donations that line up with votes show where money and votes meet, not that one caused the
          other.
        </Point>
        <Point>
          Ballot pages show local contests only for a few hand-picked towns; anywhere else it would
          take your address, which we won&apos;t ask for. The news sources lean center to left.
        </Point>
      </Summary>

      <Section id="money" title="Money">
        <Limitation title="Scores follow funding style, and funding style follows party">
          <P>
            In July 2026 data, Democratic senators took roughly half the PAC share Republican
            senators did (median about 10% against 17%) and about twice the small-donor share (about
            24% against 12%), measured then as shares of total receipts. Funding Independence
            measures those behaviors directly, so average scores differ by party. The formulas
            contain no party term; the gap is in the fundraising.
          </P>
        </Limitation>
        <Limitation title="Bigger campaigns look more independent by percentage">
          <P>
            PAC checks are capped by law and individual money isn&apos;t, so a larger campaign
            naturally has a smaller PAC share. Scaling PAC dependency by how close each PAC came to
            its legal limit measures the depth of that money directly, but no single number fully
            separates &ldquo;independent&rdquo; from &ldquo;big&rdquo;.
          </P>
        </Limitation>
        <Limitation title="Funding windows differ by chamber">
          <P>
            Funding covers the election that won the current seat — six years of fundraising for a
            senator, two for a representative — so cross-chamber comparisons weigh different spans
            of time. FEC filings also lag donations by weeks or months.
          </P>
        </Limitation>
        <Limitation title="Donor–vote connections are overlaps, not proof of influence">
          <P>
            Profiles link a donor&apos;s industry to votes on related topics by similarity of
            subject, aggregating employee and PAC money associated with an organization. That shows
            where money and legislation intersect; it doesn&apos;t show a donation changed a vote
            <Cite id="ansolabehere2003" />. Lobbying figures from the Senate&apos;s disclosure
            database are order-of-magnitude signals, not audited totals.
          </P>
        </Limitation>
        <Limitation title="A home-state industry counts as concentration too">
          <P>
            A senator funded heavily by the auto industry in Michigan, or agriculture in Kansas,
            scores the same on industry concentration as one funded by an unrelated out-of-state
            interest. That&apos;s deliberate.
          </P>
          <More label="Why there’s no exemption for a state’s main industries">
            <P>
              We considered and rejected a &ldquo;this industry matters to the state&rdquo;
              exemption, for the same reason an earlier exemption for donor-industry votes was
              removed (see the <A href="/changelog">scoring changelog</A>): local economic dominance
              plausibly gives an industry <em>more</em> leverage over a senator, not less, so
              exempting it would weaken the signal exactly where capture matters most. No public
              dataset can separate genuine local interest from capture that happens to track local
              economic weight, so concentration is scored as risk, following the
              industrial-organization logic the measure is built on
              <Cite id="rhoades1993" />.
            </P>
          </More>
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
              The best free alternative found — survey-based ideology estimates by state and
              district (Tausanovitch and Warshaw) — is still a single left–right score, the same
              kind of stand-in, and several years stale. Real issue-by-issue opinion at this scale
              would mean building multilevel regression and poststratification over raw survey data
              in-house: a statistics pipeline, and a black box next to every other formula on these
              pages. We chose not to trade auditability for a partial fix. Issue-level opinion data
              remains the named next step.
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
            <Cite id="clinton2006" />.
          </P>
        </Limitation>
        <Limitation title="Heavy breaking reads the same whether it builds a coalition or burns one">
          <P>
            The score peaks at the seat&apos;s norm, the pattern a member&apos;s own party&apos;s
            primary voters reward. The wider electorate leans the other way — in Senate general
            elections from 1990 to 2024, members who broke more than their seat&apos;s norm did
            somewhat better, and some heavy breakers keep winning by appealing to both sides. A
            roll-call record can&apos;t tell them apart from members whose breaks cost them their
            base; that would take approval data split by party, the same survey data named above.
          </P>
        </Limitation>
        <Limitation title="Two effectiveness components share a data source">
          <P>
            Within Legislative Effectiveness, leadership and bipartisan attraction are both computed
            from the cosponsorship network — different measures of related data. Their combined
            weight is capped at 40% for that reason, and their correlation is checked after every
            run.
          </P>
          <More label="How a larger overlap was removed">
            <P>
              A 2026-07-21 audit found two parts of Constituent Alignment correlated at r = −0.76
              (58% shared variance, 99 senators), both projections of the same cosponsorship
              network. v6.8 reduced the double count; v6.11 moved coalition breadth to Legislative
              Effectiveness and switched the position measure to roll-call data from Voteview, a
              genuinely independent source; v6.13 removed the cosponsorship-based discount entirely.
            </P>
          </More>
        </Limitation>
        <Limitation title="Some bills are labelled by content">
          <P>
            Where a bill had no roll call, its party lean comes from comparing it with party
            platforms. Bipartisan or cross-cutting bills can be misread. This affects only the
            policy-area breakdown of partisan depth, never whether a member broke with their party.
          </P>
        </Limitation>
      </Section>

      <Section id="presidents" title="Presidents">
        <Limitation title="Historians’ judgment carries its biases in">
          <P>
            Historical Legacy (35%) comes from C-SPAN&apos;s historians survey. Historians as a
            field tend to favor presidents who expanded federal power, and hold off rating recent
            presidents, so this ranking inherits those tendencies at roughly that weight.
          </P>
        </Limitation>
        <Limitation title="The economy isn’t only the president’s doing">
          <P>
            Since 1946, 60% of the variation in a president&apos;s term growth is shared with 13
            other advanced economies. Effectiveness mostly measures the economy a president presided
            over. Agency Alignment has no machine-readable record before Clinton, so earlier
            presidents are scored without it.
          </P>
        </Limitation>
      </Section>

      <Section id="ballot-coverage" title="Ballots">
        <Limitation title="A state ballot page is the statewide slice">
          <P>
            Ballots are printed per precinct, so there&apos;s no single &ldquo;ballot&rdquo; for a
            whole state. Pages show federal contests, statewide measures and — where a state&apos;s
            own results or candidate list name them — statewide offices, state legislative seats
            and judgeships.
            County and city offices and local measures are shown only for a small, hand-picked list
            of towns (looked up from each town hall&apos;s address); anywhere else, showing them
            would mean taking a home address to a lookup service, and we won&apos;t do that. Every
            page lists what it omits for that state and links to the election office. See{" "}
            <A href="/about/elections">Elections &amp; ballots</A>.
          </P>
        </Limitation>
        <Limitation title="Ballot-measure coverage is still filling in">
          <P>
            Six states&apos; measures are read from their own official voter guides; the rest depend
            on Vote Smart. Where a state hasn&apos;t been covered, or an update failed, the page
            says so rather than showing an empty section — which would read as &ldquo;no
            measures&rdquo;, a different and potentially damaging claim. Reading every state
            directly means a separate reader for each state&apos;s own publication, added as each is
            researched. Until then, the official link on every page is the complete answer.
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
            says. Shell companies and deliberately obscure names can land in the wrong industry.
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
