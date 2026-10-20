import Link from "next/link";
import { pageMetadata } from "@/lib/site";
import { COURT_FINDING, JUSTICE_RESEARCH_URL, NOT_SCORED_REASON } from "@/lib/justices";
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
  title: "How Presidents Are Shown, and Why Justices Are Not Scored",
  description:
    "What a president's page shows (approval, the economy and the historians' own rank, each beside past presidencies) and why Supreme Court justices are not scored: no method yet separates loyalty to the appointing president from career timing.",
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
          A president&apos;s page shows the record: approval polls and the economy, each beside past
          presidencies, and the historians&apos; own rank. Civitas gives no president a score.
          Justices are not scored either: the one measure we tested could not be separated from when
          in a career the appointing president served.
        </p>
      }
    >
      <Summary>
        <Point>
          A president&apos;s page leads with the historians&apos; rank, named as theirs and dated,
          then approval and the economy, each figure beside the average of past presidencies and how
          many there were.
        </Point>
        <Point>
          Where there is no record (no historians&apos; rating yet for recent presidents, no
          approval polling or election win for four of them), the page says so rather than filling
          the gap.
        </Point>
        <Point>
          A justice&apos;s page draws the record beside every sitting justice&apos;s: majority and
          dissent, one-vote cases, opinions written, years served and agreement with each colleague,
          and links to the president who made the appointment. No justice gets a score or a rank.
        </Point>
      </Summary>

      <Section id="presidents" title="What a president's page shows">
        <P>
          Every figure, and each president&apos;s name, party and term dates, comes from live,
          historical or expert-survey records. Since October 2026 no page shows a 0 to 100 score for
          a president or ranks presidents by one: a weighted blend of unlike records carries a
          judgment its weights decide. Any two presidents, the sitting one included, can be set side
          by side on the{" "}
          <Link href="/compare/presidents" className="underline underline-offset-2 hover:text-phos">
            president comparison
          </Link>
          .
        </P>
        <List>
          <Item label="Historians' rank">
            C-SPAN&apos;s Presidential Historians Survey, newest edition published (as of October
            2026, the 2021 survey of about 142 historians; C-SPAN postponed its 2025 survey). The
            rank is the survey&apos;s own order by total points, and the page names it as theirs. It
            rates only completed terms, so a later president reads &ldquo;not rated&rdquo; until an
            edition rates them. Historians as a field tend to favor presidents who expanded federal
            power.
          </Item>
          <Item label="Approval">
            The average over the term and how it moved from start to finish, from the American
            Presidency Project at UC Santa Barbara (AP-NORC, CNN-SSRS, Marist, Pew, Verasight;
            Gallup stopped tracking presidential approval in February 2026). Presidents before
            polling show their average margin of victory instead.
          </Item>
          <Item label="The economy">
            Growth, jobs, unemployment and inflation over the term, leaving out the first year,
            which mostly reflects the outgoing administration <Cite id="blinder2016" />. Before 1947
            only growth and, from 1939, jobs can be measured.
          </Item>
          <Item label="Executive orders">
            The count signed, from the Federal Register. A count, not a comparison: orders became a
            routine tool only in the 20th century.
          </Item>
        </List>
        <Sub title="What each figure sits beside">
          <P>
            Each figure is drawn as a strip with every other presidency that has it, within the era
            its series covers, and the page says how many there are. A word follows the same fixed
            rule as members&apos; peer strips: outside the middle half of the other presidencies
            reads &ldquo;above&rdquo; or &ldquo;below&rdquo;, outside the top or bottom tenth and
            far from the expected figure reads &ldquo;well above&rdquo; or &ldquo;well below&rdquo;,
            and no word is drawn with fewer than ten other presidencies or for a term still under
            way. A dashed tick marks the expected figure: the median, or for these three, what
            presidents starting from the same level saw, because raw figures would partly rank
            presidents by their luck.
          </P>
          <List>
            <Item label="Approval by party, against the era">
              As the parties drifted apart in Congress, approval from the other party collapsed and
              approval from the president&apos;s own party rose <Cite id="jacobson2019" />
              <Cite id="donovan2020" />. So approval in each group (the president&apos;s party, the
              other party, independents) sits beside what presidents got from that group when
              Congress was as polarized, measured by the distance between the parties&apos; voting
              records <Cite id="mccarty2006" />, fitted by a method no single presidency can swing{" "}
              <Cite id="sen1968" />.
            </Item>
            <Item label="Approval trend and the economy, against the start">
              Approval is bounded: the higher a term starts, the further it tends to fall. A term
              that begins near the top of a recession usually sees unemployment fall a long way, and
              inherited inflation usually lingers. So each sits beside what presidents who started
              at the same level went on to see. A presidency shorter than a full term (the sitting
              one, or one cut short) is compared with predecessors over the same number of days.
            </Item>
            <Item label="Growth, against 13 peer economies">
              Oil shocks, financial crises and the pandemic hit every advanced economy in the same
              years, so from 1947 on growth per person sits beside the median of 13 peer economies
              over the same years <Cite id="bolt2024" />, with the part of the gap that the
              peers&apos; catching up with US incomes predicts set aside <Cite id="baumol1986" />
              <Cite id="barro1992" />. Before 1947 no peer series covers the terms, so growth is
              total real growth beside other presidents before 1947 <Cite id="romer1989" />.
            </Item>
          </List>
          <P>
            Tested and left out: how often Congress voted the president&apos;s way (mostly which
            party held Congress), counts of major laws enacted (they follow Congress and the era),
            and the change in the deficit (it mostly follows the business cycle). The study is in
            the project repository at docs/research/president-scores.md.
          </P>
        </Sub>
        <More label="The retired score, for the record">
          <P>
            Until October 2026 each president had a 0 to 100 Presidential Score: the
            historians&apos; survey at 50%, approval and the economy at 25% each. With half the
            weight on the survey, the blend mostly restated the historians&apos; order, so the page
            now shows their rank directly beside the two records. The open API still serves the
            score, marked deprecated, until its version 2 (see the{" "}
            <Link href="/developers#deprecations" className="underline underline-offset-2">
              developer notes
            </Link>
            ).
          </P>
          <P>
            Earlier parts were dropped for lack of a real record: Independence and Follow-Through
            were hand-set numbers with no live source; Competence (the executive-order rate) showed
            no relationship with historians&apos; rating of administrative skill; and Agency
            Alignment (rules finalized) differed by about two points across every administration
            since 1994.
          </P>
        </More>
      </Section>

      <Section id="justices" title="Supreme Court justices: the record, not a score">
        <P>
          {NOT_SCORED_REASON} Until October 2026 justices were scored on one question, from Epstein
          and Posner (2016): does a justice side with the federal government more often while the
          president who appointed them is in office than under other presidents? A placebo test
          showed the answer could not be read as loyalty for any one justice.
        </P>
        <List>
          <Item label="The placebo">
            A justice&apos;s years under the appointing president are always their first. Moving a
            fake &ldquo;appointer&rdquo; window of the same length to later in each career still
            finds most of the effect (about 4 of 6.6 points, pooled) and most of the differences
            between justices: the spread between justices&apos; fake windows is 60 to 70% of the
            real spread, and only 11 of 25 justices with enough career to test sit outside what
            their own fake windows produce. Six of today&apos;s nine have too short a career after
            their appointer for the test to say anything.
          </Item>
          <Item label="Two fixes">
            Comparing each vote with colleagues&apos; votes on the same case removed under a third
            of the placebo effect. Fitting the career drift directly left about 3 points, no more
            than fake windows placed elsewhere in a career produce. Neither passed.
          </Item>
          <Item label="The Court as a whole">{COURT_FINDING}</Item>
        </List>
        <P>
          What each justice&apos;s page shows instead: the difference in votes for the federal
          government under the appointing president and under others, with the government&apos;s
          side of each case held fixed, and its 95% confidence interval, drawn beside every sitting
          justice&apos;s. It is information, not a measure of loyalty, and no justice is ranked by
          it. Every vote in a signed decision of a case the federal government argued: Epstein and
          Posner&apos;s through 2014, and the Supreme Court Database&apos;s after; the appointing
          president is whoever was in office on the nomination date, from the Federal Judicial
          Center.
        </P>
        <P>
          Each figure on a justice&apos;s page is drawn beside the same figure for every sitting
          justice, with the median marked and each justice&apos;s figure in a table: the share of
          cases in the majority, the majority share of cases decided by one vote (where each vote in
          the majority decided the case), opinions for the Court, dissents and concurrences written,
          years on the Court and the latest Martin-Quinn position. Agreement with each colleague and
          every case on the record (the side taken and the opinion written) follow, and the page
          links to the appointing president&apos;s page, which lists every Supreme Court appointment
          that president made, from the Federal Judicial Center&apos;s directory of federal judges
          (each given to the president in office on the nomination date). The record reads the
          current term and the three before it, and each page names the terms that have decided
          cases, since a term that has just begun has none. The{" "}
          <Link href="/court">Court page</Link> puts the sitting justices on one line, shows how
          often each pair voted together and how the Court split term by term, and lists each
          decision&apos;s majority and dissent. The voting record is read from the Supreme Court
          Database for every term its newest release covers, all orally argued cases, and from the
          Oyez Project only for a term after that, since Oyez enters votes case by case and had
          entered 13 of one term&apos;s 59 decided cases more than a year on. When Oyez lists a
          justice twice in one decision, the vote counts once if the entries agree and is left out
          if they don&apos;t.
        </P>
        <P>
          Also tested and not adopted: splitting &ldquo;other presidents&rdquo; into the
          appointer&apos;s party and the other party (across 31,650 votes, justices sided with other
          administrations of their appointer&apos;s party about as often as with the other
          party&apos;s, +1.1 points, not significant); and scoring how often a justice votes against
          their usual ideological side (in divided decisions that rate tracks closeness to the
          Court&apos;s center, a −0.77 correlation with distance from the median, the same flaw that
          removed the old measures). A president who returns to office after a gap gives a direct
          test of loyalty, but when measured in October 2026 the 43 cases since the most recent
          return were too few to decide it. The study is at{" "}
          <a href={JUSTICE_RESEARCH_URL}>docs/research/justice-scores.md</a>.
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
