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
  Facts,
  Fact,
  Cite,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "Data Sources, AI Use and Privacy",
  description:
    "Every public data source Civitas uses, exactly where AI is and isn't used, what is recorded about visits, and the single Raspberry Pi the whole site runs on.",
  path: "/about/data",
});

export default function DataChapter() {
  return (
    <AboutPage
      href="/about/data"
      eyebrow="Methodology · data, AI & infrastructure"
      title="Data, AI & how it runs"
      lede={
        <p>
          Where every number comes from, what the AI models do and don&apos;t do, what we record
          about visits, and the machine it all runs on.
        </p>
      }
    >
      <Summary>
        <Point>
          All data comes from official government sources and public records. Nothing is purchased
          or taken from behind a paywall.
        </Point>
        <Point>
          AI sorts records into categories, finds claims in news articles and writes a few short,
          labelled summaries. It never produces a score, and never writes anything about a ballot.
        </Point>
        <Point>
          Every model runs on the same small computer as the site. No data is sent to an AI company.
        </Point>
        <Point>
          No accounts, no cookies, no ad or analytics trackers. Visits are counted in a way that
          can&apos;t be traced back to anyone once the day is over.
        </Point>
      </Summary>

      <Section id="sources" title="Where the data comes from">
        <Sub title="Congress">
          <Facts>
            <Fact label="Congress.gov">
              Members, bills, sponsors and cosponsors, votes, and each bill&apos;s summary, action
              history and text.
            </Fact>
            <Fact label="FEC (fec.gov)">
              Campaign finance: individual and PAC contributions, committee filings and types,
              spending — and declared candidates for every federal race.
            </Fact>
            <Fact label="Senate.gov & House Clerk">
              Roll-call votes with every member&apos;s position, and each chamber&apos;s live floor
              log.
            </Fact>
            <Fact label="GovInfo">
              The Congressional Record, read in full each day, and its Daily Digest.
            </Fact>
            <Fact label="Voteview">
              Congress-by-congress roll-call positions (Nokken-Poole), for Constituent Alignment.
            </Fact>
            <Fact label="Cooperative Election Study">
              Approval of each member among their own constituents, by party (2024 survey, Harvard
              Dataverse) — shown on profiles, not scored.
            </Fact>
            <Fact label="Financial disclosures">
              STOCK Act trade reports and annual disclosures from the House Clerk and the
              Senate&apos;s eFD system; the sitting president&apos;s disclosures from the Office of
              Government Ethics. The president&apos;s annual report lists every transaction of its
              year and is the record for that year, and its asset lists back the president&apos;s
              holdings; the periodic reports since are scans, read by OCR, and their dates support
              no timeliness figure. One 2026 report, printed at half size, is still mostly unread.
            </Fact>
            <Fact label="Lobbying Disclosure Act registry (lda.gov)">
              Registered lobbying spending by organizations that appear among donors, and the bills
              their filings name.
            </Fact>
            <Fact label="Partisan lean">
              State and district Cook PVI, computed from official presidential returns (MIT Election
              Lab, county canvasses) and published district figures.
            </Fact>
          </Facts>
        </Sub>
        <Sub title="Presidents">
          <Facts>
            <Fact label="American Presidency Project">
              The presidential roster, approval polling from Truman on, and earlier election margins
              (UC Santa Barbara).
            </Fact>
            <Fact label="Federal Register">
              Rulemaking documents (Clinton on), executive orders, memoranda and proclamations.
            </Fact>
            <Fact label="BLS · BEA · MeasuringWorth">
              Payroll jobs from 1939, and real GDP back to 1790.
            </Fact>
            <Fact label="C-SPAN Historians Survey">The 2021 survey, for Historical Legacy.</Fact>
          </Facts>
        </Sub>
        <Sub title="Courts, elections and documents">
          <Facts>
            <Fact label="Oyez · supremecourt.gov">
              Cases, justices&apos; votes, and official slip opinions.
            </Fact>
            <Fact label="Supreme Court Database · Federal Judicial Center · Martin-Quinn">
              Every justice&apos;s votes in cases the federal government argued, with Epstein and
              Posner&apos;s coding through 2014; nomination dates; each justice&apos;s position per
              term.
            </Fact>
            <Fact label="State election offices">
              Certified candidate lists and primary results, from each state&apos;s own site; six
              states&apos; official voter guides for ballot measures.
            </Fact>
            <Fact label="Vote Smart">Ballot measures for other states, quoted verbatim.</Fact>
            <Fact label="Census Bureau · Google Civic">
              District boundaries; and lookups run only on addresses we chose — a town hall for the
              town selector, a fixed public building per district to fill in candidate lists — never
              a visitor&apos;s.
            </Fact>
            <Fact label="USAGov">
              Each state&apos;s election office, linked only after a check that the link still
              works.
            </Fact>
            <Fact label="News & trends">
              RSS feeds from seven newsrooms, Google Trends and Bluesky, for the Action Center.
            </Fact>
          </Facts>
        </Sub>
        <P>
          Every source&apos;s rate limits are respected, and responses are cached for 72 hours to
          avoid asking twice — ballot measures for only 12, since courts strike measures mid-cycle.
        </P>
      </Section>

      <Section id="ai" title="How AI is used — and where it isn’t">
        <P>
          Two kinds of model run here, each for what it&apos;s suited to. Neither ever produces a
          score: every score is a published formula.
        </P>
        <Sub title="Embedding models: sorting records into categories">
          <P>
            Small sentence-embedding models turn text into numbers whose closeness tracks closeness
            in meaning
            <Cite id="reimers2019" />. That lets the pipeline classify by comparison — this bill
            against a description of each policy area, this donor&apos;s employer against a
            description of each industry — with no hand-written keyword rules, and it generalizes to
            names the pipeline has never seen
            <Cite id="bengio2003" />. Unlike a generative model, it is deterministic and can&apos;t
            invent a category
            <Cite id="minaee2021" />. Two are used, both about 22 million parameters: Snowflake
            Arctic-XS for classification, and all-MiniLM-L6-v2
            <Cite id="wang2020" /> for search and the Action Center&apos;s similarity checks.
          </P>
          <More label="Why two models">
            <P>
              Arctic packs text written in the same style into a narrow band of similarity scores
              (about 0.55–0.87), which left several thresholds unable to tell a real match from
              noise. On this site&apos;s own failure cases, all-MiniLM-L6-v2 separated them roughly
              four times as well for matching news to documents and three times as well for policy
              relevance. Classification stays on Arctic because its thresholds were calibrated
              against that model — moving a threshold to a different model without re-measuring it
              is how thresholds quietly stop meaning anything.
            </P>
          </More>
        </Sub>
        <Sub title="A language model: locating text, a few short write-ups">
          <P>
            A small open-weight model (LFM2.5-1.2B-Instruct, run with llama.cpp
            <Cite id="gerganov2023" />) is used for:
          </P>
          <List>
            <Item label="Action Center">
              Finding an attributable claim in an article. The sentence shown is then copied from
              the article and checked word for word — see <A href="/about/news#quoted">how</A>.
            </Item>
            <Item label="Monitors and the timeline">
              Deciding whether a story is significant enough to track and whether two monitors are
              the same, and short summaries of each period on the year-in-review timeline.
            </Item>
            <Item label="Developing issues">
              A hedged draft about a bill&apos;s final passage or a significant new federal rule,
              written from the roll-call record or the Federal Register before the news covers it,
              checked against that record and labelled as developing.
            </Item>
            <Item label="Explore">A summary of a document, only when you ask for one.</Item>
            <Item label="Bluesky">
              The wording around a post; an issue post&apos;s lead is the verified quote itself.
            </Item>
          </List>
        </Sub>
        <Sub title="What AI does not do">
          <List>
            <Item label="Scores">Every score is a deterministic formula with no model input.</Item>
            <Item label="Ballot content">
              Nothing on a ballot page is model-written, at any stage (
              <A href="/about/elections#measures">why</A>).
            </Item>
            <Item label="Classifying bills and donors">
              Done by embeddings and nearest-neighbor voting. A language model was tried for donors
              and invented categories that don&apos;t exist (&ldquo;SPORTS&rdquo;,
              &ldquo;RESTAURANT&rdquo;) while taking 40+ minutes for what now takes under five
              seconds.
            </Item>
            <Item label="Member write-ups">
              Model-written voting narratives, key-vote explanations and campaign-promise checks
              were all removed in 2026-07 after audits found them unreliable however they were
              prompted.
            </Item>
          </List>
        </Sub>
      </Section>

      <Section id="classification" title="How records are classified">
        <P>
          The pipeline classifies thousands of bills, donors and votes each run, trying the cheapest
          method that works first
          <Cite id="jurafsky2023" />:
        </P>
        <Steps>
          <Step n={1} title="Structured records">
            Fields the source already provides, like the FEC&apos;s committee type codes.
          </Step>
          <Step n={2} title="Memory">
            Anything classified in an earlier run is recalled rather than recomputed
            <Cite id="lin1992" />.
          </Step>
          <Step n={3} title="Comparison with descriptions">
            Cosine similarity against a plain-English description of each category, such as each of
            the 18 policy areas (based on the Congressional Research Service&apos;s scheme) or each
            industry
            <Cite id="yin2019" />. Bill direction — does it expand, restrict or reform? — is read
            the same way
            <Cite id="baumgartner1993" />, in the text-as-data tradition
            <Cite id="grimmer2013" />.
          </Step>
          <Step n={4} title="Nearest neighbors">
            Whatever is left is labeled by a similarity-weighted vote of the seven most similar
            already-labeled items
            <Cite id="cover1967" />
            <Cite id="snell2017" />, drawn from a reference set that grows every run
            <Cite id="lewis2020" />.
          </Step>
        </Steps>
        <P>
          Confident labels become examples for future runs, so the reference set grows over time
          <Cite id="yarowsky1995" />. A nearest-neighbor guess is stored but never used as an
          example for the next guess, so one run&apos;s mistake can&apos;t teach the next to repeat
          it.
        </P>
        <More label="Keeping stale labels from outliving the code that made them">
          <P>
            At the start of every run, the pipeline fingerprints its own analysis code (ignoring
            comments). If the code has changed since the last run, every stored label and the
            reference set are cleared so the new code starts fresh; if not, they are kept. Raw data
            from government sources is never cleared — it reflects the sources, not our processing.
          </P>
          <P>
            Classification avoids hand-written keyword lists, with three narrow, documented
            exceptions: a check for a bill&apos;s leading verb, a hotel-brand tier for industries,
            and a PAC-suffix and payment-processor tier for donors. Each exists because of a
            specific, measured failure of the embedding model, and runs only as a first filter in
            front of the embedding classifier — never in place of it. The project&apos;s README
            lists each with its measurement.
          </P>
        </More>
      </Section>

      <Section id="explore" title="How Explore search ranks results">
        <P>
          Explore searches floor speeches, presidential actions, Federal Register rules and Supreme
          Court opinions. Four rankings are combined
          <Cite id="cormack2009" />:
        </P>
        <List>
          <Item label="Meaning">
            Embedding similarity to your query, which finds a document even when it uses different
            words
            <Cite id="karpukhin2020" />.
          </Item>
          <Item label="Keywords">
            A keyword index
            <Cite id="robertson2009" /> — the only way to find an executive order number or a
            docket.
          </Item>
          <Item label="Recency">A newer document is usually the more useful answer.</Item>
          <Item label="Authority">
            How often other federal documents cite this one, scored like PageRank.
          </Item>
        </List>
        <P>
          Near-duplicates are collapsed, and no single member or agency can crowd the top results —
          the rest are moved down, never dropped. Ranking weights are changed only when a
          measurement of search quality says to, never because one set of results looks better.
        </P>
      </Section>

      <Section id="privacy" title="What we record about you">
        <P>
          No accounts, no cookies, no ad networks and no third-party analytics. We count visits on
          our own server in a way designed so that, once a day is over, nobody — including us — can
          recover who visited:
        </P>
        <List>
          <Item label="Daily unique visits">
            Each visitor&apos;s IP address is scrambled with a random key that exists only for the
            current day and is then deleted. Without the key, the scrambled value can&apos;t be
            turned back into an address. Raw IP addresses and browser identification strings are
            never stored — only a coarse browser, operating system and device type (for example
            &ldquo;Firefox, Windows, desktop&rdquo;).
          </Item>
          <Item label="Page views">A count per page type, per day.</Item>
          <Item label="Load times">
            A tally of how long pages take to load, in broad ranges — with nothing about who loaded
            them.
          </Item>
        </List>
        <P>
          None of it is shared or sold. Requests this server makes go only to the public sources
          listed above, and none carries anything about a visitor.
        </P>
      </Section>

      <Section id="infrastructure" title="The computer it runs on">
        <P>
          The whole of Civitas — database, models, pipeline and website — runs on one Raspberry Pi
          5, a credit-card-sized computer with 16 GB of memory and an NVMe drive, at home. It draws
          about 5–12 watts; a cloud AI accelerator draws 250–400
          <Cite id="patterson2021" />. The trade is speed: the nightly pipeline takes hours rather
          than minutes, which is fine for a nightly job. See the{" "}
          <A href="/environmental">environmental page</A> for the full energy accounting.
        </P>
        <P>
          Using an open-weight model means no per-use fees that could make the project unaffordable,
          no dependence on another company staying in business, and nothing leaving the device.
        </P>
        <Facts>
          <Fact label="Hardware">Raspberry Pi 5 (16 GB), NVMe SSD</Fact>
          <Fact label="Backend">Python 3.13, FastAPI, SQLAlchemy, SQLite</Fact>
          <Fact label="Frontend">Next.js 16, React 19, TypeScript, Tailwind CSS</Fact>
          <Fact label="Models">
            Snowflake Arctic-XS and all-MiniLM-L6-v2 (embeddings); LFM2.5-1.2B-Instruct via
            llama.cpp
          </Fact>
          <Fact label="Search">sqlite-vec for embeddings, SQLite FTS5 for keywords</Fact>
          <Fact label="Deployment">
            Docker Swarm on one machine: zero-downtime rolling updates behind nginx, rolled back
            automatically if a health check fails
          </Fact>
          <Fact label="Schedule">
            Scores nightly; news hourly; in the 60 days before an election, ballot lists every 6
            hours and race coverage every 15 minutes
          </Fact>
          <Fact label="Accessibility checks">
            axe-core over the state ballot page and its research panels on every change; Lighthouse
            for contrast and load times
          </Fact>
          <Fact label="Energy">About 61 kWh a year for the whole site</Fact>
          <Fact label="Cloud services · outside funding">None · none</Fact>
        </Facts>
      </Section>
    </AboutPage>
  );
}
