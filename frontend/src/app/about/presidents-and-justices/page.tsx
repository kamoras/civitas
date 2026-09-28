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
  Cite,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "How Presidents and Supreme Court Justices Are Scored",
  description:
    "How Civitas scores every U.S. president — public mandate, economic effectiveness, agency follow-through and historical legacy — and how it scores Supreme Court justices' independence.",
  path: "/about/presidents-and-justices",
});

export default function PresidentsAndJusticesChapter() {
  return (
    <AboutPage
      href="/about/presidents-and-justices"
      eyebrow="Methodology · executive & judicial"
      title="Presidents & justices"
      lede={
        <p>
          Presidents are scored on how well they served the country; justices on how well they
          served the law regardless of party. Both from records, not opinions typed in by us.
        </p>
      }
    >
      <Summary>
        <Point>
          Presidents get four scores: public approval, the economy, how far their agencies carried
          through the rules they started, and historians&apos; assessment. Historians count for 35%.
        </Point>
        <Point>
          Where a president has no real data for a score — no polling before Truman, no digital
          rulemaking record before Clinton — it reads N/A and is left out of their overall score,
          rather than filled with a guess.
        </Point>
        <Point>
          Justices are scored on whether they side with colleagues differently depending on which
          party appointed them.
        </Point>
        <Point>
          Three presidential scores were removed because no real data could support them. We say
          which, and why.
        </Point>
      </Summary>

      <Section id="presidents" title="How presidents are scored">
        <P>
          Every figure — and each president&apos;s name, party and term dates — comes from live,
          historical or expert-survey data. The ranked list compares past presidents; the sitting
          president is shown on their own, since comparison with predecessors is the only meaningful
          ranking for that office.
        </P>
        <Sub title="Public Mandate (21.67%)">
          <P>
            Approval over the term: 70% the average, 30% the trend from start to finish, each scored
            against every president&apos;s actual polling history. Data comes from the American
            Presidency Project at UC Santa Barbara, which aggregates AP-NORC, CNN-SSRS, Marist, Pew
            and Verasight — Gallup, the original source, stopped tracking presidential approval in
            February 2026. Presidents before Truman, from before polling, are scored on their
            average margin of victory instead. The five who never won a presidential election in
            their own right have neither and read N/A.
          </P>
        </Sub>
        <Sub title="Effectiveness (21.67%)">
          <P>
            The economy over the term: GDP growth (60%) and job creation (40%). Growth is the
            average annual real-GDP growth over the term, leaving out the first year, which mostly
            reflects the outgoing administration. Job figures exist from 1939, so earlier presidents
            are scored on growth alone. Both are compared with other presidents&apos; actual figures
            rather than fixed cut-offs, and growth is compared within its era — before and after
            1947 — because older estimates exaggerate booms and busts
            <Cite id="romer1989" />.
          </P>
          <P>
            Worth knowing: since 1946, 60% of the variation in a president&apos;s term growth is
            shared with 13 other advanced economies over the same years. This mostly measures the
            economy a president presided over, not one they created. The study is in the project
            repository at docs/research/president-scores.md.
          </P>
        </Sub>
        <Sub title="Agency Alignment (21.67%)">
          <P>
            How far executive agencies carried through the rulemaking they started: the share of
            Federal Register rulemaking documents in the term that were final rules rather than
            proposals, compared with other administrations since 1994. It no longer rewards the
            number of rules issued — that follows an administration&apos;s view of regulation, and
            scoring more rules as better scored a policy preference.
          </P>
          <P>
            Coverage starts with Clinton. That is a digitization wall, not a judgment: the Federal
            Register&apos;s API returns nothing for earlier presidents, and older issues exist only
            as scanned page images. Earlier presidents read N/A here.
          </P>
        </Sub>
        <Sub title="Historical Legacy (35%)">
          <P>
            What none of the others can capture — crisis leadership, moral authority, vision — from
            C-SPAN&apos;s Presidential Historians Survey: about 142 historians in the 2021 survey,
            the most recent (C-SPAN postponed the 2025 survey). It rates only presidents whose terms
            were complete by 2021, so later presidents read N/A.
          </P>
          <P>
            It is a real, documented external survey, not neutral ground truth. Studies of these
            surveys find historians tend to favor presidents who expanded federal power, and hold
            off rating recent presidents. At 35%, this ranking inherits those tendencies at about
            that strength.
          </P>
        </Sub>
        <Sub title="How the parts combine">
          <P>
            Historical Legacy is held at exactly 35% whenever at least two of the other three have
            data, and those share the rest. With only one of them available, the weights spread
            evenly over whatever exists instead — one number isn&apos;t reliable enough to carry 65%
            of a score (Fillmore&apos;s economy scores 100 on a Gold Rush boom he had little to do
            with). Each president&apos;s page says how many scores their overall is built from.
          </P>
        </Sub>
        <More label="Why 35%, and why three scores were removed">
          <P>
            Independence and Follow-Through were removed in 2026-07. Both were one-time hand-set
            numbers with no live formula and no realistic path to one: Independence&apos;s obvious
            source (OpenSecrets&apos; revolving-door tracking) was discontinued in 2025, and
            Follow-Through needed the same platform-versus-action matching that failed four times
            for members of Congress. Rather than present a hand-set number as a computed score, they
            were dropped.
          </P>
          <P>
            Historical Legacy was added because nothing else could credit &ldquo;preserved the
            Union, ended slavery&rdquo;: Lincoln was landing in the bottom half. Its weight was
            checked against the real 47-presidency dataset. At 20%, the other dimensions — which
            barely correlate with historians&apos; judgment on their own (Spearman 0.17) — put
            Coolidge, McKinley and Harding in the top ten while Lincoln and Eisenhower fell out. At
            50%, the overall ranking correlated 0.96 with C-SPAN&apos;s alone, so the other
            dimensions added almost nothing. At 35% the top is recognizable and the rest still moves
            (0.89).
          </P>
          <P>
            Competence (executive-order rate) was removed next: Coolidge and Harding issued orders
            at nearly the same rate, yet historians rate their administrative skill 596 and 334 of
            1,000, and across 44 presidents the correlation was 0.097 (p = 0.53) — indistinguishable
            from noise. Using C-SPAN&apos;s own administrative-skill rating instead would have
            pushed the historians&apos; effective weight to about 51%. Its weight went evenly to the
            remaining three.
          </P>
          <P>
            Finally, Historical Legacy&apos;s 35% used to drift upward for presidents missing other
            scores — to about 45% for everyone before Clinton and 62% for four unelected successors
            — so 35% was the true weight for only 4 of 47 presidencies. It is now held fixed as
            described above; re-checked, Lincoln and Eisenhower stay in the top ten and Coolidge,
            Harding and McKinley stay out.
          </P>
        </More>
      </Section>

      <Section id="justices" title="How Supreme Court justices are scored">
        <P>
          From case-level votes in the Oyez Project and official Supreme Court records, with each
          opinion linked to its official slip-opinion PDF. The question is whether a justice applies
          consistent principles, or shifts with the politics of who appointed their colleagues.
        </P>
        <List>
          <Item label="Independence (56%)">
            How often, in split decisions, a justice sides with the other party&apos;s appointees
            against their own.
          </Item>
          <Item label="Ideological consistency (44%)">
            How differently a justice agrees with colleagues appointed by the same party versus the
            other, weighting close decisions most because they reveal the most.
          </Item>
        </List>
        <P>
          When the source lists a justice twice in one decision, the vote counts once if the entries
          agree and is left out if they don&apos;t; a vote the source omits is never filled in. Each
          justice&apos;s dissent rate is shown as a plain statistic, not scored. A short profile of
          each justice is written by the site&apos;s language model from these statistics — see{" "}
          <A href="/about/data#ai">how AI is used</A>.
        </P>
        <More label="Two parts we removed">
          <P>
            Until v6.13 there were two more. &ldquo;Judicial restraint&rdquo; scored how often a
            justice dissents; tested on the Rehnquist Court&apos;s 1994–2004 votes, that measured
            distance from the Court&apos;s median justice, who is in nearly every majority. In a
            simulated 6–3 Court it put the smaller bloc about 21 points behind just for being
            outvoted. &ldquo;Bipartisan agreement&rdquo; measured the same thing as independence (a
            0.86 correlation), so its weight moved there. The study is in the project repository at
            docs/research/justice-scores.md.
          </P>
        </More>
      </Section>
    </AboutPage>
  );
}
