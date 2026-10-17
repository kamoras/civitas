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
    "How Civitas picks the day's civic news and keeps it in its sources' own words, how daily Congress reports are built from the Congressional Record, when a trending topic gets an On the record post, and how corrections are handled.",
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
          News comes from six newsrooms, checked hourly. The mix runs from center to lean-left, with
          no right-of-center outlet at present: a limitation, stated.
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
          When a U.S. policy topic trends on Bluesky and matches an official record, an &ldquo;On
          the record&rdquo; post shows that record in its own words. Which trends are posted is
          chosen without reading party, and audited for it.
        </Point>
        <Point>
          Anything withdrawn is logged publicly with its date and reason, not quietly deleted.
        </Point>
      </Summary>

      <Section id="action-center" title="How the Action Center picks stories">
        <P>
          Seven feeds from six newsrooms are read every hour: NPR (Politics and World, counted as
          one), PBS NewsHour, BBC World, The Hill, Politico and Roll Call, with opinion sections
          filtered out. AP News was read through a mirror until October 2026, when the mirror was
          found a month stale; AP&apos;s own site turns away automated readers, so it was dropped
          rather than read around that. AP stories still arrive through PBS NewsHour. A feed that
          fails, or has nothing new for longer than its own usual gaps, raises an alert to the
          operator. Articles are kept if they&apos;re about U.S. policy, and grouped into stories by
          headline similarity.
        </P>
        <More label="How an article is judged to be about U.S. policy">
          <P>
            Each article is compared with the feed articles already read, each marked the way its
            own publisher filed it: a politics feed or politics section counts as U.S. policy news,
            a world feed or a foreign or arts section as not, and an article that names a sitting
            member of Congress, justice, the president or a federal agency counts as U.S. policy
            news whatever section it ran in. An article is kept when the ones it most resembles,
            weighted by how closely, are mostly U.S. policy news; how many it compares with and how
            much is mostly are worked out again each day from the stored articles. Nobody labels
            articles by hand. Checked against the 827 articles a person had labelled before, 88 to
            96% of what it keeps is U.S. policy news, about the same as the hand-labelled version it
            replaced. The rule before that, closeness to two dozen written descriptions of policy
            topics, kept 61 to 67% relevant articles: storm and flood coverage, foreign politics and
            crime passed, while campaign coverage was dropped. Foreign news with no U.S. government
            role is not kept.
          </P>
        </More>
        <P>
          Stories are then ranked: 40% civic actionability (are officials named, does it resemble
          the government documents we index), 35% breadth (how many independent newsrooms cover it)
          and 25% whether people are talking about it on Google Trends and Bluesky. Trending counts
          least because it is the most volatile. At most two issues publish per hourly run, in rank
          order; if a top story fails the checks below, the next one is tried. A story is passed
          over as a repeat only of one that actually published in that run: a briefing that named
          two stories once crowded both out and then published nothing itself.
        </P>
        <More label="Who the stories are about">
          <P>
            Each issue records who it is principally about: the official named first in its
            headline, or the kind of story when it names none. We watch how concentrated that is
            week by week. In early October 2026, 37 of the last 90 issues led with the president. We
            tested two corrections against every hourly run since late September, on days the rules
            weren&apos;t tuned on: allowing one person to lead at most one issue a day, and ranking
            stories with no government actor last. Neither found a story to publish instead. Every
            story that passed our checks that day had already been published, so the cap would only
            have withheld real reporting, such as a proposed methane rule. We don&apos;t use either.
            The mix reflects which stories pass the checks, and that is what we&apos;d change to
            change it.
          </P>
        </More>
        <More label="How stories are grouped, and why it errs toward keeping them apart">
          <P>
            Each article&apos;s headline and the start of its description are compared after
            removing what every article that day has in common, so only the topic counts. Articles
            join a story only if every one resembles every other. An earlier rule asked only that
            each resemble one other, so stories chained together by theme: on 27 September 2026 an
            issue titled for floods in Bangkok led with a hurricane near Hawaii and listed facts
            about a nor&apos;easter and an epidemic in Fiji. Headline similarity can&apos;t reliably
            tell the same event from the same kind of event, so grouping errs toward keeping stories
            apart: an issue may cite fewer sources, which is a smaller error than one built from
            unrelated stories.
          </P>
          <P>
            How alike two articles must be to group them was set against 553 pairs of real articles,
            each judged as one event or two. The bar is the lowest at which none of the pairs from
            different events would be grouped. It groups fewer pairs of the same event than a looser
            bar would, and that is the trade we chose. Headlines alone could group only 6 of the 114
            same-event pairs on that rule; with the description, 17. A new issue is treated as an
            existing one only when they share a source article, say the same thing, or carry
            near-identical headlines: naming the same people turned out to be wrong three times in
            four, and merging two stories hides one. Multi-story briefings (&ldquo;Up First&rdquo;,
            &ldquo;Morning news brief&rdquo;) are dropped at the door, because once several stories
            share one article they can&apos;t be separated later. So is a two-story item whose
            headline and summary both tell the same two unrelated stories, one sentence each. A
            livestream or video page (&ldquo;WATCH LIVE: &hellip;&rdquo;) is for watching, not a
            written report, so it never names an issue or supplies a fact; a story with nothing else
            waits for a report. Which pages those are is learned from how the outlets themselves
            file their videos, not from a list.
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
          runs to the end of its clause. The &ldquo;who&rdquo; has to be a party that can act: a
          person, an institution, an official body or a group of officials. A named one passes on
          its name; one the story only describes (&ldquo;a federal judge&rdquo;, &ldquo;federal
          agents&rdquo;) is compared with nearly a thousand hand-labelled examples, and passes only
          when its nearest examples are overwhelmingly parties, a bar set so that, tested on those
          examples, it lets through nothing the name check would have kept out. A line from inside
          someone&apos;s quotation is dropped: it is their account, not the outlet&apos;s. Where the
          model stops short (&ldquo;Senator sues&rdquo;), the site reads on in the article to the
          end of that clause, and drops the claim if it can&apos;t tell where the clause ends, and a
          line with a bracket it opens or closes but doesn&apos;t match is dropped too. Only then is
          the sentence shown, in the source&apos;s own words, naming the outlet and linking the
          article it came from. The summary at the top of an issue is one of these lines, and names
          its outlet the same way. An issue needs two of these lines, and two that say the same
          thing in different words (a headline and its own first sentence) count as one: they are
          compared with 204 pairs of lines judged by hand. The headline is the top article&apos;s
          real headline, and an issue&apos;s full story is every checked sentence, listed under the
          outlet that reported it.
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
          : when the Senate or House passes a bill (a vote from the last two days, never an older
          one found later), or an agency publishes a significant rule (on its publication day, not
          while it waits on public inspection), before the news has covered it, a short draft is
          made from the primary record itself, in a fixed template with nothing characterized: a
          vote&apos;s measure, result, tally and date, or a rule&apos;s agency, title, abstract and
          publication date. It carries a note that broader coverage hasn&apos;t confirmed it yet,
          and isn&apos;t posted anywhere else until press coverage does. A vote the news already
          covers isn&apos;t drafted, and a draft gives way once the reporting on its bill appears:
          it leaves the Action Center, and the homepage&apos;s record of recent issues lists the
          reporting in its place. A draft that leaves the Action Center without coverage confirming
          it, its vote or rule having aged past the window, isn&apos;t part of that record either:
          its own page stays up for two weeks, marked{" "}
          <strong className="text-ink-hi">Not confirmed</strong>. That record lists one issue per
          ongoing story the site follows (the newest), beside the story itself, so two days of one
          story don&apos;t fill it.
        </P>
        <P>
          Each issue ends with what you can do about it, built from the record and never advocacy
          for or against a position: see how each member voted (a vote&apos;s own issue links its
          roll-call page here), contact the members of Congress the coverage names (through their
          own contact form), contact the committee that handles the subject, follow the bills
          involved, comment on a rule about the story by its deadline, and read the related federal
          documents. Where the record says who decides next (the stage a linked bill has reached, or
          the agency taking comments on a rule), the issue says so. Every issue also lets you pick
          your state from a list and shows your senators and representative right there, with each
          office&apos;s phone number and contact form, how each voted on any roll call the issue is
          about, and a call script made of the record and your choice to ask, support or oppose (the
          last two only for a bill). The site never asks where you live, and the state you pick
          isn&apos;t saved: a member&apos;s office answers its own constituents, so a member from
          another state is often not one you can reach. A seat changing party on election night is
          the exception: its issue is the count, so what you can do is follow that count on its
          state&apos;s page.
        </P>
        <P>
          When an issue names a race on this year&apos;s ballot, it leads with it: &quot;On the
          ballot: Georgia · Senate race (Roe)&quot;, then the state&apos;s next voter deadline from
          its election office and links to register and to check your registration. A race is linked
          only when the story names one of its confirmed nominees (in full, or by surname beside the
          state&apos;s name when no other nominee there shares it) or the seat itself (&quot;the
          Michigan Senate race&quot;). A state named in the headline of an election story gets its
          voter information alone, worded &quot;Voting in&quot;, with no race claimed.
        </P>
        <More label="How the committee is chosen">
          <P>
            Each chamber&apos;s committee is named from two measurements, and only when both pick
            the same committee and do so strongly enough. The first compares the issue with each
            committee&apos;s jurisdiction as the House and Senate rules list it (House Rule X,
            Senate Rule XXV; the intelligence and Indian Affairs committees from their own published
            statements). The second looks at the most similar of 460 past issues, each read and
            labelled by hand with the committees that handle it, or with none. Their chair and
            ranking member come from the committee rosters the site already keeps, as they stand
            when you read the issue. Tested on issues newer than every labelled one it learned from,
            the committee named was one of the right ones 82% of the time, and a committee was named
            for about one issue in five; the rest say nothing rather than guess. That is short of
            the bar the site sets for stating it as fact (being confident precision is at least
            80%), and asking for more certainty didn&apos;t raise it, so the row says
            &ldquo;likely&rdquo;. Most of the misses were stories no committee clearly handles. The
            rules&apos; wording alone did much worse (38%): it dates from 1946 in places and reads
            nothing like the news.
          </P>
          <P>
            A comment action appears only for a proposed or final rule (the Federal Register&apos;s
            own document type) still open for comment, whose title is as close to the issue&apos;s
            as a related document&apos;s must be; it disappears when the period closes.
          </P>
        </More>
        <P>
          Only the day&apos;s top two issues are posted to the <A href="/feeds">feeds</A> and the
          Bluesky account, and no more than two issue posts a day. Every issue still has its own
          page and its place here.
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
            documents whose comment period is open, each linking to its page in Explore and the
            comment form there. Those come in order of how much they matter, not which closes first:
            any a recent issue links, then proposed and final rules by how many people have read
            them (the Federal Register&apos;s own count), then other notices, then paperwork
            notices: requests to renew a form an agency collects, which the Register files under its
            own heading. By deadline alone, six of the first ten were such notices on 11 October
            2026; on 17 October they were 303 of the 615 open periods. &ldquo;Every open comment
            period&rdquo; lists them all in Explore in the same order, with the paperwork notices
            folded under their own heading, as each agency&apos;s page does. Each shows the
            abstract&apos;s first sentence as the Register printed it and how much of the period is
            left. Every day still on the record can be paged through, one at a time.
          </Item>
          <Item label="Ongoing">
            When a story keeps coming back, on five separate days within two weeks and from at least
            three outlets, it becomes a national monitor with its own sourced timeline. A language
            model writes the monitor&apos;s name and description and decides nothing: a name using a
            word its articles don&apos;t falls back to the headline most like the rest, and the
            model is asked again on later days. Until October 2026 the model copied the example name
            in its instructions for most new topics, and each new monitor was merged into the
            existing one of that name, so the site showed one monitor for months. A day&apos;s story
            joins a monitor when its headline is close enough to the monitor&apos;s own description
            and it names someone or something the monitor names that most stories don&apos;t: a name
            in a third of all stories, like the president&apos;s, doesn&apos;t count on its own. A
            language-model check on borderline matches was removed after a replay showed it let 17
            of 18 off-topic stories through. Two monitors are merged only when their titles are
            near-identical, since a merge can&apos;t be undone. A monitor goes quiet
            (&ldquo;watching&rdquo;) after a week without coverage and wakes when it returns; after
            a month it closes, and is listed under this tab&apos;s closed monitors for 60 days, with
            when it was tracked and when it closed, and in the Archive.
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
          roll-call files, with every member&apos;s vote, back to January 2025.
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
          advocacy, and each week, the bills that became law. A day or week with no record vote and
          nothing passed or confirmed, such as a pro forma session, isn&apos;t posted. On a weekend
          before a week of business, a post says when each chamber next meets, from the
          Record&apos;s own notice, and which bills passed one chamber the week before and go to the
          other. Posts go to the <A href="/feeds">feeds</A> and the Bluesky account. The feeds get
          them as soon as they are written; one written overnight waits for Bluesky until the hours
          U.S. users are most active that day, measured from Bluesky&apos;s own public activity.
          News, an Action Center issue or an election-night count, never waits.
        </P>
      </Section>

      <Section id="trend-posts" title="On the record: trending topics">
        <P>
          Bluesky publishes a public list of what is trending. Each hour Civitas reads it, and when
          a U.S. policy topic on it matches an official record, it may post that record: a vote, a
          bill, a rule or the day&apos;s Congressional Record. A match counts only if it is one of
          four pinned kinds: an Action Center issue whose wording is close to the trend&apos;s
          (cosine similarity of 0.5 or more) and that names the trend&apos;s own subject; an Act
          whose full title the trend names (a shorter name inside a longer title, or a title with
          another jurisdiction in front of it, is a different law); a nominee&apos;s confirmation
          vote, only when the trend is about the nomination itself; or a recent bill sponsored by a
          member the trend names. A person named in a story is never enough, and a record more than
          30 days old counts only when the trend names it: an old vote attached to new news says
          nothing about the news.
        </P>
        <P>
          Whether a trend is about U.S. policy is a vote among the most similar labelled Bluesky
          trends (nearest neighbors), the same method the Action Center uses for articles; the
          labels were made by an AI agent against a written rubric, published with them. The
          trend&apos;s name is the only text a post takes from Bluesky, so it is checked before
          anything else: a name that reads as electioneering, editorializes, states advocacy as
          fact, or that a vote of labelled examples finds loaded is skipped, not reworded.
        </P>
        <More label="Tragedies and violence">
          <P>
            They aren&apos;t excluded outright, because an official response to one is part of the
            public record. A trend about one is eligible only when a vote finds it about political
            violence, and only with an official record responding to it, or the agency&apos;s own
            record. The post then states only what that record says, and nothing about victims or
            perpetrators. With no such record, there is no post.
          </P>
        </More>
        <P>
          Every word of a post is fixed template text, the record&apos;s own fields, or
          Bluesky&apos;s name for the trend, quoted and attributed to Bluesky. No model writes any
          of it. The picture is the record itself (a vote&apos;s tally by party, a bill, a rule) on
          a card with its source, its page on Civitas and the date the data was read, and alt text
          made from the same fields. For the first four weeks, alternate days carry that picture or
          a plain link card instead, to measure which people engage with more.
        </P>
        <P>
          Selection doesn&apos;t look at party: among eligible trends, the one posted is the one
          with the highest rank on Bluesky&apos;s list times the strength of its match. Every
          candidate, posted or not, is recorded with the parties of the members it names and of the
          record&apos;s sponsors, and the share of posts leaning to each party is tested against the
          share in the whole eligible pool (an exact binomial test); a divergence alerts the
          operator.
        </P>
        <P>
          There are at most two a day, at least four hours apart, and only inside the posting
          window, the hours U.S. users are most active on Bluesky. A trend that qualifies outside it
          is held, and dropped if it has left the list by the next check. The same record is posted
          at most once in 14 days, and never one an issue post already carried. Posts go to the{" "}
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
