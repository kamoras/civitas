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
  Cite,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "How Presidents and Supreme Court Justices Are Scored",
  description:
    "How Civitas scores every U.S. president (public mandate, economic effectiveness, agency follow-through and historical legacy), and how it scores Supreme Court justices' independence from the president who appointed them.",
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
          Presidents are scored on how well they served the country; justices on whether they favor
          the president who appointed them. Both from records, not opinions typed in by us.
        </p>
      }
    >
      <Summary>
        <Point>
          Presidents get three scores: public approval, the economy, and historians&apos;
          assessment. Historians count for half.
        </Point>
        <Point>
          Where a president has no real data for a score (no historians&apos; rating yet for recent
          presidents, no approval polling or election win for four of them), it reads N/A and is
          left out of their overall score, rather than filled with a guess. Each score on their page
          shows its actual share of their overall.
        </Point>
        <Point>
          Justices are scored on whether they side with the federal government more often while the
          president who appointed them is in office, shown with its margin of error.
        </Point>
        <Point>
          Four presidential scores were removed: three because no real data could support them, one
          because administrations didn&apos;t differ on it. We say which, and why.
        </Point>
      </Summary>

      <Section id="presidents" title="How presidents are scored">
        <P>
          Every figure, and each president&apos;s name, party and term dates, comes from live,
          historical or expert-survey data. The ranked list compares past presidents; the sitting
          president is shown on their own, since comparison with predecessors is the only meaningful
          ranking for that office. The leaderboard shows the sitting president&apos;s scores above
          the ranked list, linking to the full scorecard. Any two presidents, the sitting one
          included, can be set side by side on the{" "}
          <Link href="/compare/presidents" className="underline underline-offset-2 hover:text-phos">
            president comparison
          </Link>
          , which shows both scores and the same figures without ranking either.
        </P>
        <P>
          A president&apos;s page shows each score beside the figures it is scored on and the
          average of every president with a figure: approval against all presidents&apos; approval,
          jobs a year against every presidency since 1939, and so on. The sitting president&apos;s
          page is not ranked until the term ends.
        </P>
        <Sub title="Public Mandate (25%)">
          <P>
            Approval over the term: 70% the average, 30% the trend from start to finish, each scored
            against every completed presidency&apos;s actual polling history.
          </P>
          <P>
            The average is compared within its era. As the parties drifted apart in Congress,
            approval from the other party collapsed, from about 49% for Eisenhower to 5% for Biden,
            while approval from the president&apos;s own party rose <Cite id="jacobson2019" />
            <Cite id="donovan2020" />. Compared raw, that ranked presidents partly by when they
            served. So approval in each group (the president&apos;s party, the other party,
            independents) is compared with what presidents got from that group when Congress was as
            polarized, measured by the distance between the parties&apos; voting records{" "}
            <Cite id="mccarty2006" />, and the three differences are averaged. The relationship is
            fitted every update with a method that no single presidency can swing{" "}
            <Cite id="sen1968" />: leaving out any one moves a president&apos;s figure by at most
            2.5 points, against a spread of about 8 between presidents.
          </P>
          <P>
            The trend is compared with what presidents who started at the same level went on to do,
            because approval is bounded: across the 14 completed presidencies with polling, the
            higher the start, the further it fell. A presidency shorter than a full four-year term
            (the sitting one, or one cut short, like Kennedy&apos;s and Ford&apos;s) is compared
            with predecessors over the same number of days from the start of their terms, since
            approval falls as a term goes on: compared with whole terms, Kennedy&apos;s fall of 14
            points from a start of 76% looked twice as good as typical, when presidents starting
            there fell about as much in their first 1,001 days. Data comes from the American
            Presidency Project at UC Santa Barbara, which aggregates AP-NORC, CNN-SSRS, Marist, Pew
            and Verasight. Gallup, the original source, stopped tracking presidential approval in
            February 2026. Presidents before Truman, from before polling, are scored on their
            average margin of victory instead. The four who have neither (Tyler, Fillmore, Arthur
            and Andrew Johnson, who never won a presidential election and served before polling)
            read N/A.
          </P>
        </Sub>
        <Sub title="Effectiveness (25%)">
          <P>
            The economy over the term: GDP growth (60%) and job creation (40%), leaving out the
            first year, which mostly reflects the outgoing administration
            <Cite id="blinder2016" />. Job figures exist from 1939, so earlier presidents are scored
            on growth alone. Both are compared with other presidents&apos; actual figures rather
            than fixed cut-offs.
          </P>
          <P>
            Most of a president&apos;s growth is shared with the rest of the rich world: oil shocks,
            financial crises and the pandemic hit every advanced economy in the same years. So from
            1947 on, growth per person is compared with the median of 13 peer economies (Britain,
            France, Germany, the Netherlands, Belgium, Italy, Sweden, Denmark, Norway, Switzerland,
            Canada, Australia and Japan) over the same years
            <Cite id="bolt2024" />. Measured against them, the gap between the parties&apos; growth
            records shrinks from about 0.9 to 0.6 points a year.
          </P>
          <P>
            One adjustment keeps that comparison fair across eras. A poorer economy grows faster by
            catching up with a richer one <Cite id="baumol1986" />
            <Cite id="barro1992" />, and in the 1950s and 1960s Europe and Japan were far poorer
            than the US. Their fast growth then was not something the US missed out on, but raw it
            made every president of that era look worse. Each year, the part of the gap that the
            peers&apos; distance below US incomes predicts is set aside, using a rate measured from
            every year since 1947 <Cite id="sen1968" />. What remains still favors the most recent
            terms somewhat, because the US genuinely outgrew Europe and Japan after 2017.
          </P>
          <P>
            Before 1947 no peer series covers the terms, so growth there is total real growth,
            compared with other presidents before 1947, because older estimates exaggerate booms and
            busts <Cite id="romer1989" />. The study is in the project repository at
            docs/research/president-scores.md.
          </P>
        </Sub>
        <Sub title="Historical Legacy (50%)">
          <P>
            What none of the others can capture (crisis leadership, moral authority, vision) from
            C-SPAN&apos;s Presidential Historians Survey: about 142 historians in the 2021 survey,
            the most recent (C-SPAN postponed the 2025 survey). It rates only presidents whose terms
            were complete by 2021, so later presidents read N/A.
          </P>
          <P>
            It is a real, documented external survey, not neutral ground truth. Studies of these
            surveys find historians tend to favor presidents who expanded federal power, and hold
            off rating recent presidents. At 50%, this ranking inherits those tendencies at about
            that strength.
          </P>
        </Sub>
        <Sub title="How the parts combine">
          <P>
            Historical Legacy is held at exactly 50% whenever both of the other two have data, and
            those share the rest. With only one of them available, Historical Legacy and that one
            score share the weight in proportion to their usual weights (two thirds and one third):
            one number isn&apos;t reliable enough to carry half a score (Fillmore&apos;s economy
            scores 100 on a Gold Rush boom he had little to do with). A sitting president, unrated
            by historians, is scored on the other two equally. Each score on a president&apos;s page
            shows the share it actually carries in their overall.
          </P>
        </Sub>
        <More label="Why 50%, and why four scores were removed">
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
            checked against the real 47-presidency dataset. At 20%, the other dimensions, which
            barely correlate with historians&apos; judgment on their own (Spearman 0.17), put
            Coolidge, McKinley and Harding in the top ten while Lincoln and Eisenhower fell out. At
            50%, the overall ranking correlated 0.96 with C-SPAN&apos;s alone, so the other
            dimensions added almost nothing. At 35% the top was recognizable and the rest still
            moved (0.89). Re-measured in October 2026 on the current scores (approval judged against
            where a term starts and over the time it lasted, Agency Alignment removed), the other
            two dimensions now move the ranking more: at 35% the overall correlated 0.78 with
            C-SPAN&apos;s alone and 0.83 with the other two, so historians carried no more weight
            than the economy and approval together. At 50% it correlates 0.90 with C-SPAN&apos;s and
            0.69 with the other two: the historians lead, and the record still moves the ranking.
            The weight is now 50%.
          </P>
          <P>
            Competence (executive-order rate) was removed next: Coolidge and Harding issued orders
            at nearly the same rate, yet historians rate their administrative skill 596 and 334 of
            1,000, and across 44 presidents the correlation was 0.097 (p = 0.53): indistinguishable
            from noise. Using C-SPAN&apos;s own administrative-skill rating instead would have
            pushed the historians&apos; effective weight to about 51%. Its weight went evenly to the
            remaining three.
          </P>
          <P>
            Historical Legacy&apos;s weight used to drift upward for presidents missing other scores
            (from 35% to about 45% for everyone before Clinton and 62% for four unelected
            successors). It is now held fixed as described above.
          </P>
          <P>
            Agency Alignment (the share of the rulemakings agencies began that reached a final rule)
            was removed in October 2026. Its counts had been capped by the Federal Register&apos;s
            search, which reports at most 10,000 results, so Clinton, George W. Bush and Obama all
            read exactly 50%. Counted in full, every administration since 1994 finalized between
            59.6% and 61.8%: too little difference to score, and compared only with each other, a
            two-point gap read as nearly two standard deviations.
          </P>
        </More>
      </Section>

      <Section id="justices" title="How Supreme Court justices are scored">
        <P>
          One question: does a justice side with the federal government more often while the
          president who appointed them is in office than under other presidents? Epstein and Posner
          (2016) asked it of every justice since 1937. Comparing a justice with themselves cancels
          out their ideology and how often they side with any government.
        </P>
        <List>
          <Item label="The votes">
            Every vote in a signed decision of a case the federal government argued: Epstein and
            Posner&apos;s through 2014, and the Supreme Court Database&apos;s after. The appointing
            president is whoever was in office on the nomination date, from the Federal Judicial
            Center.
          </Item>
          <Item label="The estimate">
            How many points more often the justice sided with the government under the appointing
            president, with whether the government brought the case held fixed. One justice&apos;s
            record is a few hundred votes, so each estimate is pulled toward the average of all
            justices by how uncertain it is, and shown with its margin of error.
          </Item>
          <Item label="The score">
            100 at no favoritism either way, falling to 0 at twice the spread between justices.
            Favoring the appointing president&apos;s government and disfavoring it both lower it.
          </Item>
        </List>
        <P>
          A justice the database doesn&apos;t cover yet is not scored. Each justice&apos;s
          Martin-Quinn position (where they sit, liberal to conservative) and voting record from the
          Oyez Project are shown beside the score and not scored: where a justice sits says nothing
          about favoring the president who appointed them. When Oyez lists a justice twice in one
          decision, the vote counts once if the entries agree and is left out if they don&apos;t.
        </P>
        <P>
          Two refinements were tested and not adopted. Splitting &ldquo;other presidents&rdquo; into
          the appointer&apos;s party and the other party: across 20,737 votes, justices sided with
          other administrations of their appointer&apos;s party no more often than with the other
          party&apos;s (−1.2 points, not significant), so the loyalty is to the one president, and
          for four of today&apos;s nine justices no other president of their appointer&apos;s party
          has served yet. Scoring how often a justice votes against their usual ideological side: in
          divided decisions that rate tracks closeness to the Court&apos;s center (a −0.77
          correlation with distance from the median), the same flaw that removed the old measures.
        </P>
        <More label="Measures we removed">
          <P>
            Until September 2026 justices were scored on consistency and independence from the
            appointing party&apos;s bloc. On today&apos;s Court every Republican appointee sits
            right of every Democratic appointee, so both measured how close a justice sits to the
            Court&apos;s center: against Martin-Quinn positions for the 2024 term, they ranked
            justices by distance from the median at −0.82 and −0.75.
          </P>
          <P>
            Before that, until v6.13, there were two more. &ldquo;Judicial restraint&rdquo; scored
            how often a justice dissents; tested on the Rehnquist Court&apos;s 1994–2004 votes, that
            measured distance from the Court&apos;s median justice, who is in nearly every majority.
            In a simulated 6–3 Court it put the smaller bloc about 21 points behind just for being
            outvoted. &ldquo;Bipartisan agreement&rdquo; measured the same thing as independence (a
            0.86 correlation). Both studies are in the project repository at
            docs/research/justice-scores.md.
          </P>
        </More>
      </Section>
    </AboutPage>
  );
}
