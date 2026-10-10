import { pageMetadata } from "@/lib/site";
import {
  AboutPage,
  Summary,
  Point,
  Section,
  P,
  List,
  Item,
  More,
  A,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "How the Action Center and Congress Reports Work",
  description:
    "How Civitas picks the day's civic news and keeps it in its sources' own words, how daily Congress reports are built from the Congressional Record, and how corrections are handled.",
  path: "/about/news",
});

export default function NewsChapter() {
  return (
    <AboutPage
      href="/about/news"
      eyebrow="Methodology · news & the record"
      title="News & Congress reports"
      lede={
        <p>
          The Action Center shows the day&apos;s most important civic stories; the Congress pages
          show what each chamber did. Neither is written by us or by a model: both quote their
          sources.
        </p>
      }
    >
      <Summary>
        <Point>
          News comes from seven newsrooms, checked hourly. The mix runs from center to lean-left,
          with no right-of-center outlet at present: a limitation, stated.
        </Point>
        <Point>
          A model is used to find a claim in an article, not to write one. Every news sentence you
          read is copied word for word from its source and names the outlet; the one exception is
          labelled &ldquo;Developing&rdquo;.
        </Point>
        <Point>
          When a story has no sentence that passes those checks, nothing is published for it.
        </Point>
        <Point>
          Congress reports come from the Congressional Record&apos;s own daily summary, in its own
          wording.
        </Point>
        <Point>
          Anything withdrawn is logged publicly with its date and reason, not quietly deleted.
        </Point>
      </Summary>

      <Section id="action-center" title="How the Action Center picks stories">
        <P>
          Eight feeds from seven newsrooms are read every hour: AP News, NPR (Politics and World,
          counted as one), PBS NewsHour, BBC World, The Hill, Politico and Roll Call, with opinion
          sections filtered out. Articles are kept if they&apos;re about U.S. policy, and grouped
          into stories by headline similarity.
        </P>
        <P>
          Stories are then ranked: 40% civic actionability (are officials named, does it resemble
          the government documents we index), 35% breadth (how many independent newsrooms cover it)
          and 25% whether people are talking about it on Google Trends and Bluesky. Trending counts
          least because it is the most volatile. At most two issues publish per hourly run, in rank
          order; if a top story fails the checks below, the next one is tried. A story is passed
          over as a repeat only of one that actually published in that run: a briefing that named
          two stories once crowded both out and then published nothing itself.
        </P>
        <More label="How stories are grouped, and why it errs toward keeping them apart">
          <P>
            Headlines are compared after removing what every headline that day has in common, so
            only the topic counts. Articles join a story only if every one resembles every other. An
            earlier rule asked only that each resemble one other, so stories chained together by
            theme: on 27 September 2026 an issue titled for floods in Bangkok led with a hurricane
            near Hawaii and listed facts about a nor&apos;easter and an epidemic in Fiji. Headline
            similarity can&apos;t reliably tell the same event from the same kind of event, so
            grouping errs toward keeping stories apart: an issue may cite fewer sources, which is a
            smaller error than one built from unrelated stories.
          </P>
          <P>
            How alike two headlines must be to group them is learned from the pipeline&apos;s own
            record: each run records pairs it compared with an independent verdict (do they name the
            same people, places and numbers), and once a day the cut-off moves to where those
            verdicts say it belongs, once there are enough to trust. A new issue is treated as an
            existing one only when they share a source article, say the same thing, or carry
            near-identical headlines: naming the same people turned out to be wrong three times in
            four, and merging two stories hides one. Multi-story briefings (&ldquo;Up First&rdquo;,
            &ldquo;Morning news brief&rdquo;) are dropped at the door, because once several stories
            share one article they can&apos;t be separated later.
          </P>
          <P>
            Reddit was a trending source until September 2026, when it began requiring a login this
            site doesn&apos;t have. It was retired rather than left returning nothing: a dead source
            that looks like a quiet one is worse than none.
          </P>
        </More>
      </Section>

      <Section id="quoted" title="The model doesn’t write the sentence">
        <P>
          A model is asked only to locate an assertion in one article: who did something, and what
          they did. The site then checks that both parts appear in the article word for word, that
          the article asserts one of the other rather than merely containing both, and that the span
          runs to the end of its clause. Where the model stops short (&ldquo;Senator sues&rdquo;),
          the site reads on in the article to the end of that clause, and drops the claim if it
          can&apos;t tell where the clause ends, and a line with a bracket it opens or closes but
          doesn&apos;t match is dropped too. Only then is the sentence shown, in the source&apos;s
          own words, naming the outlet and linking the article it came from. The summary at the top
          of an issue is one of these lines, and names its outlet the same way. The headline is the
          top article&apos;s real headline, and an issue&apos;s full story is every checked
          sentence, listed under the outlet that reported it.
        </P>
        <P>
          This replaced asking a model to write neutrally and checking whether it had. That approach
          published an endorsement, and turned officials calling for an end to a war into a report
          that the war had ended. A paraphrase can be faithful to its source and still unfit to
          repeat; copying can&apos;t invent a word that isn&apos;t there. The cost is silence: a
          story with no attributable claim produces no issue, and some days carry fewer issues than
          others.
        </P>
        <P>
          One exception, labelled on the page as <strong className="text-ink-hi">Developing</strong>
          : when the Senate or House passes a bill, or an agency publishes a significant rule (on
          its publication day, not while it waits on public inspection), before the news has covered
          it, a short draft is made from the primary record itself, in a fixed template with nothing
          characterized: a vote&apos;s measure, result, tally and date, or a rule&apos;s agency,
          title, abstract and publication date. It carries a note that broader coverage hasn&apos;t
          confirmed it yet, and isn&apos;t posted anywhere else until press coverage does. A vote
          the news already covers isn&apos;t drafted, and a draft gives way once the reporting on
          its bill appears: it leaves the Action Center, and the homepage&apos;s record of recent
          issues lists the reporting in its place.
        </P>
        <P>
          Each issue ends with what you can do about it, built from the record and never advocacy
          for or against a position: contact the members of Congress the coverage names (through
          their own contact form), follow the bills involved, and read or comment on the related
          federal documents while their comment period is open. When the coverage names no member,
          it points to the directory, where you pick your own state; the site never asks where you
          live. A seat changing party on election night is the exception: its issue is the count, so
          what you can do is follow that count on its state&apos;s page.
        </P>
        <More label="Keeping the hourly refresh running">
          <P>
            Only one refresh runs at a time, even while a deploy briefly runs two copies of the
            site. The run holding that turn renews its claim every minute, and a claim unrenewed for
            ten minutes is released. A claim used to last four hours whether or not its run was
            alive, so a deploy that stopped a refresh silenced the next four: on 26 September 2026,
            a day of steady deploys, no issue was published at all. Deploys now wait for a refresh
            under way, up to a limit, and each write is saved before the next model call so the
            renewal always gets its turn.
          </P>
        </More>
      </Section>

      <Section id="action-tabs" title="The Action Center's tabs">
        <List>
          <Item label="Today">
            The day&apos;s issues, each with what you can do about it, and below them the federal
            documents whose comment period is open, soonest deadline first, each linking to its page
            in Explore and the comment form there. Every day still on the record can be paged
            through, one at a time.
          </Item>
          <Item label="Ongoing">
            When a story keeps coming back, on five separate days within two weeks and from at least
            three outlets, it becomes a national monitor with its own sourced timeline. A language
            model writes the monitor&apos;s name and description and decides nothing: a name using a
            word its articles don&apos;t falls back to the story&apos;s own headline. Until October
            2026 the model copied the example name in its instructions for most new topics, and each
            new monitor was merged into the existing one of that name, so the site showed one
            monitor for months. A day&apos;s story joins a monitor when its headline is close enough
            to the monitor&apos;s own description. A language-model check on borderline matches was
            removed after a replay showed it let 17 of 18 off-topic stories through. Two monitors
            are merged only when their titles are near-identical, since a merge can&apos;t be
            undone. A monitor goes quiet (&ldquo;watching&rdquo;) after a week without coverage and
            wakes when it returns; after a month it closes and leaves this tab, staying in the
            Archive.
          </Item>
          <Item label="Archive">
            Each day&apos;s top issue is kept permanently, building a month-by-month record with a
            summary of each finished week and month, and the civic dates coming up.
          </Item>
        </List>
        <P>
          When a scored politician is part of a story, it links to their scorecard; related
          government documents are matched from the Explore index, and only when their titles
          closely match the story&apos;s: a looser bar once linked boating safety zones in Miami to
          a story about a Florida golf club. Elections, with the countdown to the next Election Day,
          have their own page.
        </P>
      </Section>

      <Section id="congress-reports" title="Congress reports">
        <P>
          The Congress pages answer what the Senate and House did on a given day, week or month,
          from the Congressional Record&apos;s Daily Digest: the Record&apos;s own summary of each
          day, published by the Government Publishing Office the next day. Until it appears, the
          page shows each chamber&apos;s floor log: the House writes its log through the day, the
          Senate posts its log after the session ends. Record votes come from each chamber&apos;s
          roll-call files, with every member&apos;s vote, back to the start of the 119th Congress.
        </P>
        <P>
          Every entry uses the Record&apos;s own wording. The one-line summary at the top is filled
          from counts by a fixed template (&ldquo;The Senate passed 3 bills, agreed to 4 resolutions
          and took 3 record votes&rdquo;) and never describes what a bill does. A chamber that
          didn&apos;t meet shows as not in session; a source that couldn&apos;t be read shows as
          unavailable, never as an empty day. Every bill named links to its page (summary, sponsors,
          full history, text and every recorded vote), including bills whose sponsors have left.
        </P>
        <P>
          Once a day&apos;s Record is final, Civitas posts that day&apos;s count summary and the
          numbers of bills passed, never their titles, since an official short title can read as
          advocacy, and each week, the bills that became law. Posts go to the{" "}
          <A href="/feeds">feeds</A> and the Bluesky account.
        </P>
      </Section>

      <Section id="retractions" title="Corrections and retractions">
        <P>
          When something Civitas published is wrong and can&apos;t be corrected from its sources, it
          is withdrawn, not quietly deleted. Each withdrawal is listed with its date and reason in a
          public retraction log in the source code, and the issue&apos;s page says it was withdrawn
          and why. The first entry, on 27 September 2026, withdrew two issues that a since-fixed
          grouping step had built from unrelated stories.
        </P>
      </Section>
    </AboutPage>
  );
}
