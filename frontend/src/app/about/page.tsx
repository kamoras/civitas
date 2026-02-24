import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="terminal-window mb-6">
      <div className="terminal-titlebar">
        <span className="terminal-dot red" />
        <span className="terminal-dot yellow" />
        <span className="terminal-dot green" />
        <span className="ml-3 text-white/40 text-xs font-terminal">{title.toLowerCase().replace(/ /g, "_")}.txt</span>
      </div>
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
      <main id="main-content" className="pt-24 pb-16 px-4">
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
              Civitas is an open-data project that measures how well elected officials
              represent their constituents. Every score is computed from publicly available
              federal records. We do not editorialize, endorse, or oppose any candidate or party.
            </P>
            <P>
              Scores reflect observable behavior — voting patterns, funding sources,
              legislative activity — not ideology. A senator who votes with their party 100%
              of the time receives a lower independence score regardless of whether they are
              a Democrat or Republican. The system is designed to be structurally non-partisan.
            </P>
            <P>
              When data is missing or insufficient, scores default to a neutral 50 out of 100.
              No politician is penalized for something we cannot measure, and no politician
              receives a perfect score without evidence.
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
                  Measures the ratio of small-dollar individual donors to corporate PAC money.
                  A senator funded primarily by individual constituents scores higher than one
                  reliant on industry PACs. The formula factors in small-donor percentage and
                  PAC concentration ratio. This approach draws on campaign finance research
                  showing that donor composition strongly predicts legislative behavior.
                  <Cite id="1">Bonica 2014</Cite>
                </P>
              </div>

              <div>
                <Label>Promise Persistence (25%)</Label>
                <P>
                  Tracks whether a senator&apos;s voting record aligns with their stated campaign
                  promises. Platform text is extracted from official senate.gov websites and
                  analyzed to identify key commitments. Votes are then cross-referenced against
                  those promises using semantic search to find relevant legislation.
                  <Cite id="2">Naurin 2011</Cite>
                </P>
                <P>
                  This metric also incorporates floor advocacy analysis — whether
                  a senator actively speaks on the Senate floor about their promised issues,
                  parsed from Congressional Record proceedings using keyword matching.
                  This captures effort that voting records miss: in a gridlocked Senate,
                  a senator may not get bills to a vote but can still demonstrate persistence
                  through floor speeches. The floor advocacy component is weighted at 20% of
                  the promise score, following research on legislative speech as a signal of
                  commitment.
                  <Cite id="3">Martin 2011</Cite>
                </P>
              </div>

              <div>
                <Label>Independent Voting (25%)</Label>
                <P>
                  Measures willingness to break with party leadership on votes that directly
                  affect the senator&apos;s state. We identify state-relevant policy areas by
                  analyzing the senator&apos;s top donor industries (which serve as a proxy for
                  the state&apos;s economic composition). Party-line votes on state-relevant issues
                  are excluded from the independence penalty because they may reflect genuine
                  constituent representation rather than blind party loyalty.
                  <Cite id="4">Carson et al. 2010</Cite>
                </P>
                <P>
                  The score blends two components: party independence (60%) — the rate of
                  breaking with the party on non-state-relevant votes — and donor independence
                  (40%) — whether votes appear free from donor influence, measured by the
                  correlation between PAC funding and voting alignment on donor-relevant legislation.
                  <Cite id="5">Stratmann 2005</Cite>
                </P>
              </div>

              <div>
                <Label>Transparency (15%)</Label>
                <P>
                  Evaluates how traceable and diverse a senator&apos;s funding sources are.
                  The score blends donor traceability (50%) — the fraction of funding from
                  classifiable, named sources versus opaque or unclassified money — with
                  industry diversity (50%), measured as the inverse Herfindahl-Hirschman
                  Index (HHI) of industry donations. HHI is a standard concentration metric
                  from industrial organization economics; in this context, funding concentrated
                  in a single industry suggests regulatory capture, while broad funding suggests
                  diverse constituent support.
                  <Cite id="6">Rhoades 1993</Cite>
                </P>
              </div>

              <div>
                <Label>Accessibility (10%)</Label>
                <P>
                  Currently uses vote participation rate as a proxy for constituent engagement.
                  A senator who shows up to vote consistently scores higher. This is an admittedly
                  limited metric — we plan to incorporate town hall frequency and public
                  availability data as those sources become programmatically accessible.
                </P>
              </div>
            </div>
          </Section>

          {/* ── President Metrics ── */}
          <Section title="PRESIDENTIAL SCORECARD METRICS">
            <P>
              Presidents are scored on five dimensions, also 0-100 scale. Historical
              presidents (pre-Clinton) use static scores derived from the C-SPAN
              Presidential Historians Survey, Gallup approval records, and BEA/BLS
              economic data. Recent presidents (Clinton onward) have scores partially
              computed from live API data.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <Label>Independence (20%)</Label>
                <P>
                  Assesses cabinet and advisor independence from corporate and lobbyist
                  influence. Based on historical analysis of cabinet compositions — how
                  many appointees came from industry versus public service backgrounds.
                  Currently uses curated seed data; automated analysis is planned.
                </P>
              </div>

              <div>
                <Label>Follow-Through (25%)</Label>
                <P>
                  Measures the ratio of campaign promises to executive and legislative
                  action. Based on historian assessments and promise-tracking analysis.
                  Currently uses curated seed data for historical presidents.
                </P>
              </div>

              <div>
                <Label>Public Mandate (20%)</Label>
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
            </div>
          </Section>

          {/* ── Classification Pipeline ── */}
          <Section title="CLASSIFICATION AND NLP PIPELINE">
            <P>
              The pipeline classifies thousands of entities (bills, donors, industries, votes)
              per run. We use a tiered strategy that reserves expensive techniques for cases
              where cheaper methods fail, following the principle of computational parsimony.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">BILL POLICY AREA CLASSIFICATION</h3>
                <P>
                  Bills and votes are classified into 15 policy areas (healthcare, defense,
                  energy, etc.) using cosine similarity between sentence embeddings of bill text
                  and pre-computed embeddings of policy area descriptions.
                  <Cite id="7">Reimers &amp; Gurevych 2019</Cite>
                </P>
                <P>
                  The embedding model is <Label>all-MiniLM-L6-v2</Label>, a distilled
                  sentence transformer optimized for semantic similarity tasks. At 22M
                  parameters, it runs efficiently on constrained hardware while maintaining
                  strong performance on STS benchmarks.
                  <Cite id="8">Wang et al. 2020</Cite>
                  Zero LLM calls are used for bill classification.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">DONOR AND INDUSTRY CLASSIFICATION</h3>
                <P>
                  Donor classification uses a four-tier strategy:
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
                      <span className="text-neon-pink/60">Deterministic rules</span> — pattern
                      matching for payment processors (ActBlue, WinRed), party committees, and
                      known entity types using hand-crafted regular expressions.
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">3.</span>
                    <span>
                      <span className="text-neon-pink/60">Embedding cosine similarity</span> — donor
                      names are embedded using the same sentence transformer and compared against
                      pre-computed industry description embeddings. Names above a similarity threshold
                      of 0.35 are classified directly.
                      <Cite id="7">Reimers &amp; Gurevych 2019</Cite>
                    </span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">4.</span>
                    <span>
                      <span className="text-neon-pink/60">k-Nearest Neighbor (kNN)</span> — remaining
                      unclassified donors are classified by finding the k=7 most similar
                      already-labeled entities in the learning store using similarity-weighted
                      majority voting in embedding space. This is an instance-based learning method
                      <Cite id="9">Cover &amp; Hart 1967</Cite> that dynamically adapts to new
                      categories as labeled examples accumulate. Each pipeline run enriches the
                      reference set, making subsequent classifications faster and more accurate.
                    </span>
                  </li>
                </ul>
                <P>
                  The kNN approach was chosen over LLM-based classification after empirical
                  analysis showed the LLM hallucinated invalid categories (producing labels like
                  &quot;SPORTS&quot; or &quot;RESTAURANT&quot; outside the valid taxonomy) and was
                  orders of magnitude slower. The kNN classifier processes ~5,000 donors in under
                  5 seconds versus 40+ minutes for the LLM, with more consistent results.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">LEARNING STORE</h3>
                <P>
                  All classifications are persisted in a learning store (SQLite table) that
                  functions as an evolving knowledge base. On subsequent pipeline runs, previously
                  classified entities are retrieved instantly without recomputation. This is
                  analogous to experience replay in reinforcement learning
                  <Cite id="10">Lin 1992</Cite> — past decisions inform future ones,
                  improving both speed and accuracy over time.
                </P>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">SEMANTIC SEARCH (EXPLORE)</h3>
                <P>
                  The Explore feature uses dense passage retrieval
                  <Cite id="11">Karpukhin et al. 2020</Cite> to enable free-text search over
                  government documents (floor speeches, executive orders, bills). Documents are
                  chunked, embedded with all-MiniLM-L6-v2, and stored in ChromaDB for approximate
                  nearest-neighbor retrieval. This outperforms keyword search (BM25) for
                  conceptual queries like &quot;climate policy&quot; where exact term overlap is low.
                </P>
              </div>
            </div>
          </Section>

          {/* ── AI Usage ── */}
          <Section title="HOW AI IS USED">
            <P>
              Civitas uses a small, locally-hosted language model for specific analytical
              tasks in the senator data pipeline. AI is never used to generate scores
              directly — all scores are computed by deterministic, auditable formulas.
            </P>

            <div className="space-y-4 mt-4">
              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">WHAT THE LLM DOES</h3>
                <P>
                  The LLM handles tasks that require natural language understanding and
                  synthesis — areas where rule-based or embedding approaches are insufficient:
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
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">WHAT THE LLM DOES NOT DO</h3>
                <div className="space-y-2">
                  <Row label="Score calculation" value="All five sub-scores use deterministic formulas with no LLM input. The math is fully auditable." />
                  <Row label="Classification" value="Bill, donor, and industry classification use embedding similarity and kNN — no LLM in the classification loop." />
                  <Row label="Data fabrication" value="The LLM only analyzes data already fetched from official APIs. It does not generate or invent facts." />
                  <Row label="Partisan analysis" value="Prompts are explicitly structured to avoid partisan framing. The LLM analyzes behavior, not ideology." />
                </div>
              </div>

              <div>
                <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">WHY THESE TECHNIQUES WERE CHOSEN</h3>
                <P>
                  We follow a strict hierarchy: structured data first, then deterministic rules,
                  then embedding models, then LLM — reserving each more expensive technique only
                  for tasks the cheaper ones cannot handle. This reflects the principle that
                  simpler models should be preferred when they achieve comparable accuracy,
                  a finding consistently supported in applied NLP research.
                  <Cite id="12">Jurafsky &amp; Martin 2023</Cite>
                </P>
                <P>
                  Embeddings (not LLM) for classification: sentence embeddings excel at
                  text classification tasks when labeled examples or category descriptions exist.
                  They are deterministic, fast, and avoid the hallucination risks inherent in
                  generative models.
                  <Cite id="13">Minaee et al. 2021</Cite>
                  The kNN classifier further leverages the accumulated labeled data as a growing
                  reference set, which is a well-established approach in few-shot and
                  semi-supervised learning settings.
                  <Cite id="14">Snell et al. 2017</Cite>
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
                  The inference model is <Label>Qwen 2.5 1.5B</Label>, a compact open-weight
                  language model running natively via llama.cpp
                  <Cite id="16">Gerganov 2023</Cite> compiled with ARM-specific optimizations
                  (cortex-a76, dot-product, fp16). This provides ~3x faster inference compared
                  to containerized runtimes, generating ~8 tokens/second on the Raspberry Pi 5 CPU.
                  Results are cached in a local database so each unique analysis is computed at most once.
                </P>
                <P>
                  The embedding model is <Label>all-MiniLM-L6-v2</Label>
                  <Cite id="8">Wang et al. 2020</Cite>, a 22M-parameter sentence transformer.
                  It handles all classification (bills, donors, industries), semantic search, and
                  nearest-neighbor retrieval. Both models run entirely on-device with no external
                  API calls.
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
              <Row label="Congress.gov API" value="Bill text, voting records, member data, and legislative activity for the current Congress" />
              <Row label="FEC API (fec.gov)" value="Campaign finance data: individual contributions, PAC donations, committee filings, and disbursements" />
              <Row label="GovInfo API" value="Full bill text for policy area classification, Congressional Record floor proceedings for advocacy analysis" />
              <Row label="Senate.gov" value="Official senator websites scraped for platform text and campaign promises" />
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
              <Row label="Semantic Search" value="Documents embedded with all-MiniLM-L6-v2 into ChromaDB for dense passage retrieval" />
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
                  database, backend API, and frontend all run on the same device.
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
                  We deliberately chose Qwen 2.5, an open-weight model, over proprietary
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
                    <span>Correlation between donations and votes does not prove causation. A senator who receives PAC money and votes favorably may be doing so for policy reasons unrelated to the donation. We follow the methodological caution urged by Ansolabehere et al. (2003)<Cite id="18">Ansolabehere et al. 2003</Cite> in interpreting donation-vote correlations.</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-neon-yellow shrink-0">-</span>
                    <span>The Accessibility metric is currently limited to vote participation rate. True accessibility (town halls, responsiveness to constituents) is difficult to measure programmatically.</span>
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
              <Row label="Backend" value="Python, FastAPI, SQLAlchemy, SQLite" />
              <Row label="Frontend" value="Next.js 14, React, Tailwind CSS" />
              <Row label="Embedding Model" value="all-MiniLM-L6-v2 (22M params, sentence-transformers)" />
              <Row label="LLM Runtime" value="llama.cpp (native ARM build), Qwen 2.5 1.5B" />
              <Row label="Vector Database" value="ChromaDB (persistent, local)" />
              <Row label="Containers" value="Docker Compose (blue/green zero-downtime deploy)" />
              <Row label="Pipeline Schedule" value="Nightly at 3:00 AM via APScheduler" />
              <Row label="Data Caching" value="72-hour TTL with persistent SQLite cache" />
            </div>
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
