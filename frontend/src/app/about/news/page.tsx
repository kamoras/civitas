import { pageMetadata } from "@/lib/site";
import { AboutPage, Summary, Point, Section, Boxes, Box, P, A } from "@/components/about/AboutPage";

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
        <Boxes>
          <Box
            title="Seven feeds, read hourly"
            dek="Seven feeds from six newsrooms are read every hour, with opinion sections filtered out. Articles about U.S. policy are kept and grouped into stories."
            figure={{ type: "stat", value: 7, caption: "feeds, read hourly" }}
            more="the feeds, and AP"
          >
            <P>
              Seven feeds from six newsrooms are read every hour: NPR (Politics and World, counted
              as one), PBS NewsHour, BBC World, The Hill, Politico and Roll Call, with opinion
              sections filtered out. AP News was read through a mirror until October 2026, when the
              mirror was found a month stale; AP&apos;s own site turns away automated readers, so it
              was dropped rather than read around that. AP stories still arrive through PBS
              NewsHour. A feed that fails, or has nothing new for longer than its own usual gaps,
              raises an alert to the operator. Articles are kept if they&apos;re about U.S. policy,
              and grouped into stories by headline similarity.
            </P>
          </Box>
          <Box
            title="About U.S. policy?"
            dek="Each article is compared with articles already read, each marked the way its publisher filed it. Nobody labels articles by hand."
            figure={{ type: "stat", value: 88, text: "88-96%", caption: "of what it keeps" }}
            more="how an article is judged"
          >
            <P>
              Each article is compared with the feed articles already read, each marked the way its
              own publisher filed it: a politics feed or politics section counts as U.S. policy
              news, a world feed or a foreign or arts section as not, and an article that names a
              sitting member of Congress, justice, the president or a federal agency counts as U.S.
              policy news whatever section it ran in. An article is kept when the ones it most
              resembles, weighted by how closely, are mostly U.S. policy news; how many it compares
              with and how much is mostly are worked out again each day from the stored articles.
              Nobody labels articles by hand. Checked against the 827 articles a person had labelled
              before, 88 to 96% of what it keeps is U.S. policy news, about the same as the
              hand-labelled version it replaced. The rule before that, closeness to two dozen
              written descriptions of policy topics, kept 61 to 67% relevant articles: storm and
              flood coverage, foreign politics and crime passed, while campaign coverage was
              dropped. Foreign news with no U.S. government role is not kept.
            </P>
          </Box>
          <Box
            title="Ranking stories"
            dek="Stories are ranked 40% civic actionability, 35% breadth and 25% trending. At most two issues publish per hourly run."
            figure={{ type: "stat", value: 40, text: "40/35/25", caption: "percent weights" }}
            more="the ranking and repeats"
          >
            <P>
              Stories are then ranked: 40% civic actionability (are officials named, does it
              resemble the government documents we index), 35% breadth (how many independent
              newsrooms cover it) and 25% whether people are talking about it on Google Trends and
              Bluesky. Trending counts least because it is the most volatile. At most two issues
              publish per hourly run, in rank order; if a top story fails the checks below, the next
              one is tried. A story is passed over as a repeat only of one that actually published
              in that run: a briefing that named two stories once crowded both out and then
              published nothing itself.
            </P>
          </Box>
          <Box
            title="Who the stories are about"
            dek="Each issue records who it is mainly about. Two rules to balance the mix were tested, and neither found a story to publish instead."
            figure={{
              type: "stat",
              value: 37,
              text: "37 / 90",
              caption: "issues led with the president",
            }}
            more="the test"
          >
            <P>
              Each issue records who it is principally about: the official named first in its
              headline, or the kind of story when it names none. We watch how concentrated that is
              week by week. In early October 2026, 37 of the last 90 issues led with the president.
              We tested two corrections against every hourly run since late September, on days the
              rules weren&apos;t tuned on: allowing one person to lead at most one issue a day, and
              ranking stories with no government actor last. Neither found a story to publish
              instead. Every story that passed our checks that day had already been published, so
              the cap would only have withheld real reporting, such as a proposed methane rule. We
              don&apos;t use either. The mix reflects which stories pass the checks, and that is
              what we&apos;d change to change it.
            </P>
          </Box>
          <Box
            title="Grouping articles into stories"
            dek="Articles join a story only if every one resembles every other. Grouping errs toward keeping stories apart."
            figure={{ type: "stat", value: 553, caption: "article pairs judged" }}
            more="how stories are grouped"
          >
            <P>
              Each article&apos;s headline and the start of its description are compared after
              removing what every article that day has in common, so only the topic counts. Articles
              join a story only if every one resembles every other. An earlier rule asked only that
              each resemble one other, so stories chained together by theme: on 27 September 2026 an
              issue titled for floods in Bangkok led with a hurricane near Hawaii and listed facts
              about a nor&apos;easter and an epidemic in Fiji. Headline similarity can&apos;t
              reliably tell the same event from the same kind of event, so grouping errs toward
              keeping stories apart: an issue may cite fewer sources, which is a smaller error than
              one built from unrelated stories.
            </P>
            <P>
              How alike two articles must be to group them was set against 553 pairs of real
              articles, each judged as one event or two. The bar is the lowest at which none of the
              pairs from different events would be grouped. It groups fewer pairs of the same event
              than a looser bar would, and that is the trade we chose. Headlines alone could group
              only 6 of the 114 same-event pairs on that rule; with the description, 17. A new issue
              is treated as an existing one only when they share a source article, say the same
              thing, or carry near-identical headlines: naming the same people turned out to be
              wrong three times in four, and merging two stories hides one. Multi-story briefings
              (&ldquo;Up First&rdquo;, &ldquo;Morning news brief&rdquo;) are dropped at the door,
              because once several stories share one article they can&apos;t be separated later. So
              is a two-story item whose headline and summary both tell the same two unrelated
              stories, one sentence each. A livestream or video page (&ldquo;WATCH LIVE:
              &hellip;&rdquo;) is for watching, not a written report, so it never names an issue or
              supplies a fact; a story with nothing else waits for a report. Which pages those are
              is learned from how the outlets themselves file their videos, not from a list. A live
              blog whose headline lists several unrelated stories under the outlet&apos;s label
              (&ldquo;Live updates: A; B&rdquo;) never names an issue either: the issue takes a
              single story&apos;s headline, and a live blog sharing a source with an issue is
              matched to it by the story they share. When two current issues still turn out to be
              one story, the later card gives way to the earlier and links it.
            </P>
            <P>
              Reddit was a trending source until September 2026, when it began requiring a login
              this site doesn&apos;t have. It was retired rather than left returning nothing: a dead
              source that looks like a quiet one is worse than none.
            </P>
          </Box>
        </Boxes>
      </Section>

      <Section id="quoted" title="The model doesn’t write the sentence">
        <Boxes>
          <Box
            title="Find, then check"
            dek="A model only finds who did what in one article. The site checks both parts appear word for word before the line is shown."
            more="every check on a line"
          >
            <P>
              A model is asked only to locate an assertion in one article: who did something, and
              what they did. The site then checks that both parts appear in the article word for
              word, that the article asserts one of the other rather than merely containing both,
              and that the span runs to the end of its clause. The &ldquo;who&rdquo; has to be a
              party that can act: a person, an institution, an official body or a group of
              officials. A named one passes on its name; one the story only describes (&ldquo;a
              federal judge&rdquo;, &ldquo;federal agents&rdquo;) is compared with nearly a thousand
              hand-labelled examples, and passes only when its nearest examples are overwhelmingly
              parties, a bar set so that, tested on those examples, it lets through nothing the name
              check would have kept out. A line from inside someone&apos;s quotation is dropped: it
              is their account, not the outlet&apos;s. Where the model stops short (&ldquo;Senator
              sues&rdquo;), the site reads on in the article to the end of that clause, and drops
              the claim if it can&apos;t tell where the clause ends, and a line with a bracket it
              opens or closes but doesn&apos;t match is dropped too. Only then is the sentence
              shown, in the source&apos;s own words, naming the outlet and linking the article it
              came from. The summary at the top of an issue is one of these lines, and names its
              outlet the same way. An issue needs two of these lines, and two that say the same
              thing in different words (a headline and its own first sentence) count as one: they
              are compared with 204 pairs of lines judged by hand. The headline is the top
              article&apos;s real headline, and an issue&apos;s full story is every checked
              sentence, listed under the outlet that reported it.
            </P>
          </Box>
          <Box
            title="Why copy, not write"
            dek="This replaced asking a model to write neutrally. Copying can't invent a word. The cost is that some stories produce no issue."
            more="what writing got wrong"
          >
            <P>
              This replaced asking a model to write neutrally and checking whether it had. That
              approach published an endorsement, and turned officials calling for an end to a war
              into a report that the war had ended. A paraphrase can be faithful to its source and
              still unfit to repeat; copying can&apos;t invent a word that isn&apos;t there. The
              cost is silence: a story with no attributable claim produces no issue, and some days
              carry fewer issues than others.
            </P>
          </Box>
          <Box
            title="Developing"
            dek="When a bill passes or a significant rule is published before the news covers it, a fixed-template draft is made from the record."
            more="the template, and when a draft goes"
          >
            <P>
              One exception, labelled on the page as{" "}
              <strong className="text-ink-hi">Developing</strong>: when the Senate or House passes a
              bill (a vote from the last two days, never an older one found later), or an agency
              publishes a significant rule (on its publication day, not while it waits on public
              inspection), before the news has covered it, a short draft is made from the primary
              record itself, in a fixed template with nothing characterized: a vote&apos;s measure,
              result, tally and date, or a rule&apos;s agency, title, abstract and publication date.
              It carries a note that broader coverage hasn&apos;t confirmed it yet, and isn&apos;t
              posted anywhere else until press coverage does. A vote the news already covers
              isn&apos;t drafted, and a draft gives way once the reporting on its bill appears: it
              leaves the Action Center, and the homepage&apos;s news column lists the reporting in
              its place. A draft that leaves the Action Center without coverage confirming it, its
              vote or rule having aged past the window, isn&apos;t part of that record either: its
              own page stays up for two weeks, marked{" "}
              <strong className="text-ink-hi">Not confirmed</strong>. That column lists one issue
              per ongoing story the site follows (the newest, tagged as an ongoing story, with the
              story&apos;s other days that week named beside it), so two days of one story
              don&apos;t fill it.
            </P>
          </Box>
          <Box
            title="What you can do"
            dek="Each issue ends with steps built from the record, never advocacy. Pick a state to see your members, their phones and their votes."
            more="every action"
          >
            <P>
              Each issue ends with what you can do about it, built from the record and never
              advocacy for or against a position: see how each member voted (a vote&apos;s own issue
              links its roll-call page here), contact the members of Congress the coverage names
              (through their own contact form), contact the committee that handles the subject,
              follow the bills involved, comment on a rule about the story by its deadline, and read
              the related federal documents. Where the record says who decides next (the stage a
              linked bill has reached, or the agency taking comments on a rule), the issue says so.
              Every issue also lets you pick your state from a list and shows your senators and
              representative right there, with each office&apos;s phone number and contact form, how
              each voted on any roll call the issue is about, and a call script made of the record
              and your choice to ask, support or oppose (the last two only for a bill). The site
              never asks where you live, and the state you pick isn&apos;t saved: a member&apos;s
              office answers its own constituents, so a member from another state is often not one
              you can reach. A seat changing party on election night is the exception: its issue is
              the count, so what you can do is follow that count on its state&apos;s page.
            </P>
          </Box>
          <Box
            title="On the ballot"
            dek="When an issue names a race on this year's ballot, it leads with it, the state's next voter deadline and links to register."
            more="when a race is linked"
          >
            <P>
              When an issue names a race on this year&apos;s ballot, it leads with it: &quot;On the
              ballot: Georgia · Senate race (Roe)&quot;, then the state&apos;s next voter deadline
              from its election office and links to register and to check your registration. A race
              is linked only when the story names one of its confirmed nominees (in full, or by
              surname beside the state&apos;s name when no other nominee there shares it) or the
              seat itself (&quot;the Michigan Senate race&quot;). A state named without one of its
              races is not linked.
            </P>
          </Box>
          <Box
            title="Related votes in Congress"
            dek="Each issue is compared with roll calls from three days before to two after. A close vote is shown as related, never as the cause."
            figure={{
              type: "stat",
              value: 53,
              text: "53 / 57",
              caption: "links right, unseen months",
            }}
            more="how a story is linked to votes"
          >
            <P>
              News rarely prints a bill&apos;s number, so until October 2026 not one issue in 543
              had reached the roll calls it was about, and the Iran monitor showed none of
              Congress&apos;s war-powers votes. Now each issue and monitor update is compared with
              every roll call taken from three days before its date to two after: the story&apos;s
              headline against the vote&apos;s title (the measure or nominee as the chamber records
              it), by two of the site&apos;s own language models, which compare meaning, not
              wording. A vote close enough on both is listed under &ldquo;Related votes in
              Congress&rdquo;, with how your members voted on it once you pick your state, and the
              call script cites that vote. It is shown as related, never as the cause of the story.
              An amendment vote is compared by the purpose its record states, since its title names
              only its sponsor, and left out when no purpose is on file.
            </P>
            <P>
              How close is close enough was set on 9,868 story-and-vote pairs read and marked by
              hand (642 stories from April to October 2026), at the point where the site is most
              confident that the links shown are right, which has to clear its bar for anything
              stated as fact (being confident at least 80% are right). Tested on months the setting
              never saw, 53 of 57 links were right, and it found about one related vote in five: it
              prefers saying nothing to guessing. People&apos;s names are left out of both texts
              before they are compared, so a story is matched by what it is about, not who is in it.
              A monitor&apos;s &ldquo;On the record&rdquo; chart shows every vote linked to its
              updates over its life, one square per vote, higher for more yeas than nays, with the
              same votes in a table.
            </P>
          </Box>
          <Box
            title="The committee"
            dek="A committee is named only when two measurements agree. The row says likely, because it falls short of the bar for fact."
            figure={{ type: "stat", value: 82, text: "82%", caption: "named right, newer issues" }}
            more="how the committee is chosen"
          >
            <P>
              Each chamber&apos;s committee is named from two measurements, and only when both pick
              the same committee and do so strongly enough. The first compares the issue with each
              committee&apos;s jurisdiction as the House and Senate rules list it (House Rule X,
              Senate Rule XXV; the intelligence and Indian Affairs committees from their own
              published statements). The second looks at the most similar of 460 past issues, each
              read and labelled by hand with the committees that handle it, or with none. Their
              chair and ranking member come from the committee rosters the site already keeps, as
              they stand when you read the issue. Tested on issues newer than every labelled one it
              learned from, the committee named was one of the right ones 82% of the time, and a
              committee was named for about one issue in five; the rest say nothing rather than
              guess. That is short of the bar the site sets for stating it as fact (being confident
              precision is at least 80%), and asking for more certainty didn&apos;t raise it, so the
              row says &ldquo;likely&rdquo;. Most of the misses were stories no committee clearly
              handles. The rules&apos; wording alone did much worse (38%): it dates from 1946 in
              places and reads nothing like the news.
            </P>
          </Box>
          <Box
            title="Comment by a date"
            dek="A comment action appears only for a rule still open for comment and close to the issue. It disappears when the period closes."
            more="which rules"
          >
            <P>
              A comment action appears only for a proposed or final rule (the Federal
              Register&apos;s own document type) still open for comment, whose title is as close to
              the issue&apos;s as a related document&apos;s must be; it disappears when the period
              closes.
            </P>
          </Box>
          <Box
            title="What is posted"
            dek={
              <>
                Only the day&apos;s top two issues are posted to the <A href="/feeds">feeds</A> and
                the Bluesky account, and no more than two issue posts a day. Every issue still has
                its own page and its place here.
              </>
            }
            figure={{ type: "stat", value: 2, caption: "issue posts a day at most" }}
          />
          <Box
            title="Keeping the hourly refresh running"
            dek="Only one refresh runs at a time. A run renews its claim every minute, and a claim unrenewed for ten minutes is released."
            more="why, and the 26 September outage"
          >
            <P>
              Only one refresh runs at a time, even while a deploy briefly runs two copies of the
              site. The run holding that turn renews its claim every minute, and a claim unrenewed
              for ten minutes is released. A claim used to last four hours whether or not its run
              was alive, so a deploy that stopped a refresh silenced the next four: on 26 September
              2026, a day of steady deploys, no issue was published at all. Deploys now wait for a
              refresh under way, up to a limit, and each write is saved before the next model call
              so the renewal always gets its turn.
            </P>
          </Box>
        </Boxes>
      </Section>

      <Section id="action-tabs" title="The Action Center's tabs">
        <Boxes>
          <Box
            title="Today"
            dek="The day's issues, each with what you can do about it, and below them the federal documents whose comment period is open."
            more="how open comment periods are ordered"
          >
            <P>
              The day&apos;s issues, each with what you can do about it, and below them the federal
              documents whose comment period is open, each linking to its page in Explore and the
              comment form there. Those come in order of how much they matter, not which closes
              first: any a recent issue links, then proposed and final rules by how many people have
              read them (the Federal Register&apos;s own count), then other notices, then paperwork
              notices: requests to renew a form an agency collects, which the Register files under
              its own heading. By deadline alone, six of the first ten were such notices on 11
              October 2026; on 17 October they were 303 of the 615 open periods. &ldquo;Every open
              comment period&rdquo; lists them all in Explore in the same order, with the paperwork
              notices folded under their own heading, as each agency&apos;s page does. Each shows
              the abstract&apos;s first sentence as the Register printed it and how much of the
              period is left. Every day still on the record can be paged through, one at a time.
            </P>
          </Box>
          <Box
            title="Ongoing"
            dek="A story back on five separate days within two weeks, from at least three outlets, becomes a national monitor with its own sourced timeline."
            figure={{ type: "stat", value: 5, caption: "days in two weeks" }}
            more="naming, joining and closing a monitor"
          >
            <P>
              When a story keeps coming back, on five separate days within two weeks and from at
              least three outlets, it becomes a national monitor with its own sourced timeline. A
              language model writes the monitor&apos;s name and description and decides nothing: a
              name using a word its articles don&apos;t falls back to the headline most like the
              rest, and the model is asked again on later days. Until October 2026 the model copied
              the example name in its instructions for most new topics, and each new monitor was
              merged into the existing one of that name, so the site showed one monitor for months.
              A day&apos;s story joins a monitor when its headline is close enough to the
              monitor&apos;s own description and it names someone or something the monitor names
              that most stories don&apos;t: a name in a third of all stories, like the
              president&apos;s, doesn&apos;t count on its own. A language-model check on borderline
              matches was removed after a replay showed it let 17 of 18 off-topic stories through.
              Two monitors are merged only when their titles are near-identical, since a merge
              can&apos;t be undone. A monitor goes quiet (&ldquo;watching&rdquo;) after a week
              without coverage and wakes when it returns; after a month it closes, and is listed
              under this tab&apos;s closed monitors for 60 days, with when it was tracked and when
              it closed, and in the Archive.
            </P>
          </Box>
          <Box
            title="Archive"
            dek="Each day's top issue is kept permanently, building a month-by-month record with a summary of each finished week and month, and the civic dates coming up."
          />
          <Box
            title="Members and documents"
            dek="A story links to the page of each member of Congress or president in it, and to government documents whose titles closely match."
            more="the matching bar"
          >
            <P>
              When a member of Congress or a president is part of a story, it links to their page;
              related government documents are matched from the Explore index, and only when their
              titles closely match the story&apos;s: a looser bar once linked boating safety zones
              in Miami to a story about a Florida golf club. Elections, with the countdown to the
              next Election Day, have their own page.
            </P>
          </Box>
        </Boxes>
      </Section>

      <Section id="congress-reports" title="Congress reports">
        <Boxes>
          <Box
            title="From the Daily Digest"
            dek="Each day's report comes from the Congressional Record's Daily Digest. Until it appears, the page shows each chamber's floor log."
            more="the sources"
          >
            <P>
              The Congress pages answer what the Senate and House did on a given day, week or month,
              from the Congressional Record&apos;s Daily Digest: the Record&apos;s own summary of
              each day, published by the Government Publishing Office the next day. Until it
              appears, the page shows each chamber&apos;s floor log: the House writes its log
              through the day, the Senate posts its log after the session ends. Record votes come
              from each chamber&apos;s roll-call files, with every member&apos;s vote, back to
              January 2025.
            </P>
          </Box>
          <Box
            title="The Record's own wording"
            dek="Entries use the Record's wording, and the summary line is a fixed template filled from counts. A source that can't be read shows as unavailable."
            more="the template, and empty days"
          >
            <P>
              Every entry uses the Record&apos;s own wording. The one-line summary at the top is
              filled from counts by a fixed template (&ldquo;The Senate passed 3 bills, agreed to 4
              resolutions and took 3 record votes&rdquo;) and never describes what a bill does. A
              chamber that didn&apos;t meet shows as not in session; a source that couldn&apos;t be
              read shows as unavailable, never as an empty day. Every bill named links to its page
              (summary, sponsors, full history, text and every recorded vote), including bills whose
              sponsors have left.
            </P>
          </Box>
          <Box
            title="Congress posts"
            dek="Once a day's Record is final, its count summary and the numbers of bills passed are posted. A day with nothing on the record isn't."
            more="what is posted, and when"
          >
            <P>
              Once a day&apos;s Record is final, Civitas posts that day&apos;s count summary and the
              numbers of bills passed, never their titles, since an official short title can read as
              advocacy, and each week, the bills that became law. A day or week with no record vote
              and nothing passed or confirmed, such as a pro forma session, isn&apos;t posted. On a
              weekend before a week of business, a post says when each chamber next meets, from the
              Record&apos;s own notice, and which bills passed one chamber the week before and go to
              the other. Posts go to the <A href="/feeds">feeds</A> and the Bluesky account. The
              feeds get them as soon as they are written; one written overnight waits for Bluesky
              until the hours U.S. users are most active that day, measured from Bluesky&apos;s own
              public activity. News, an Action Center issue or an election-night count, never waits.
            </P>
          </Box>
        </Boxes>
      </Section>

      <Section id="trend-posts" title="On the record: trending topics">
        <Boxes>
          <Box
            title="Matching an official record"
            dek="Each hour Civitas reads Bluesky's trending list. A U.S. policy topic may be posted only when it matches one of four kinds of official record."
            figure={{ type: "stat", value: 4, caption: "pinned kinds of match" }}
            more="the four kinds"
          >
            <P>
              Bluesky publishes a public list of what is trending. Each hour Civitas reads it, and
              when a U.S. policy topic on it matches an official record, it may post that record: a
              vote, a bill, a rule or the day&apos;s Congressional Record. A match counts only if it
              is one of four pinned kinds: an Action Center issue whose wording is close to the
              trend&apos;s (cosine similarity of 0.5 or more) and that names the trend&apos;s own
              subject; an Act whose full title the trend names (a shorter name inside a longer
              title, or a title with another jurisdiction in front of it, is a different law); a
              nominee&apos;s confirmation vote, only when the trend is about the nomination itself;
              or a recent bill sponsored by a member the trend names. A person named in a story is
              never enough, and a record more than 30 days old counts only when the trend names it:
              an old vote attached to new news says nothing about the news.
            </P>
          </Box>
          <Box
            title="Is it about policy?"
            dek="Only a trend Bluesky itself files under politics is matched. A trend name that reads as loaded or as advocacy is skipped, not reworded."
            more="the vote and the name checks"
          >
            <P>
              Only a trend Bluesky&apos;s own trends list files under &ldquo;politics&rdquo; goes on
              to be matched against the record: its category, read as Bluesky publishes it. The post
              doesn&apos;t quote the trend&apos;s name, but it answers it, so the name is checked
              before anything else: a name that reads as electioneering, editorializes, states
              advocacy as fact, or that a vote of labelled examples finds loaded is skipped, not
              reworded.
            </P>
          </Box>
          <Box
            title="Tragedies and violence"
            dek="An official response to one is part of the record. A trend about one is posted only with an official record or a confirmed issue."
            more="what counts"
          >
            <P>
              They aren&apos;t excluded outright, because an official response to one is part of the
              public record. A trend about one is eligible only with an official record (a vote, a
              bill, a rule or the day&apos;s Congressional Record), or an Action Center issue
              confirmed by two or more outlets, whose checked opening line the post quotes and
              credits to its outlet. The post then states only what that record says. With no such
              record, there is no post.
            </P>
          </Box>
          <Box
            title="Every word is template or record"
            dek="A post is fixed template text and the record's own fields. No model writes any of it, and the post doesn't name the trend."
            more="the words and the picture"
          >
            <P>
              Every word of a post is fixed template text or the record&apos;s own fields: what the
              record says, then its date and source in parentheses. The post doesn&apos;t name the
              trend or call it trending, so the record has to say what happened on its own. No model
              writes any of it. The picture is the record itself (a vote&apos;s tally by party, a
              bill, a rule) on a card with its source, its page on Civitas and the date the data was
              read, and alt text made from the same fields. For the first four weeks, alternate days
              carry that picture or a plain link card instead, to measure which people engage with
              more.
            </P>
          </Box>
          <Box
            title="Party-blind selection"
            dek="Selection doesn't look at party. Every candidate is recorded, and posts leaning to one party more than the eligible pool alert the operator."
            more="the test"
          >
            <P>
              Selection doesn&apos;t look at party: among eligible trends, the one posted is the one
              with the highest rank on Bluesky&apos;s list times the strength of its match. Every
              candidate, posted or not, is recorded with the parties of the members it names and of
              the record&apos;s sponsors, and the share of posts leaning to each party is tested
              against the share in the whole eligible pool (an exact binomial test); a divergence
              alerts the operator.
            </P>
          </Box>
          <Box
            title="How often"
            dek="At most two a day, at least four hours apart, inside the posting window. The same record is posted at most once in 14 days."
            figure={{ type: "stat", value: 2, caption: "posts a day at most" }}
            more="held trends, and where posts go"
          >
            <P>
              There are at most two a day, at least four hours apart, and only inside the posting
              window, the hours U.S. users are most active on Bluesky. A trend that qualifies
              outside it is held, and dropped if it has left the list by the next check. The same
              record is posted at most once in 14 days, and never one an issue post already carried.
              Posts go to the <A href="/feeds">feeds</A> and the Bluesky account.
            </P>
          </Box>
        </Boxes>
      </Section>

      <Section id="race-posts" title="Race updates">
        <Boxes>
          <Box
            title="Listing a story beside a race"
            dek="A story is listed when it names a candidate in full and also names the race. A candidate's name alone is not enough."
            more="why, and roundups"
          >
            <P>
              A news story is listed beside a race when it names one of the race&apos;s candidates
              in full and also names the race: its state, or another of its candidates. A
              candidate&apos;s name alone is not enough, because a member of Congress is in the news
              for many things besides their own race. Newsletters and other multi-story roundups are
              left out, the same way the Action Center leaves them out.
            </P>
          </Box>
          <Box
            title="Posting a race update"
            dek="Far fewer stories are posted than listed. A story must be about a candidate on the certified ballot and match the race closely."
            more="the bar"
          >
            <P>
              Civitas posts far fewer stories than it lists. Before a story becomes an update on its
              race, it must also be about a candidate still on the state&apos;s certified November
              ballot, and score high enough on how closely it matches the race. That bar was set
              against stories we read and marked as about the race or not: about nine in ten of the
              stories above it were about their race.
            </P>
          </Box>
        </Boxes>
      </Section>

      <Section id="retractions" title="Corrections and retractions">
        <Boxes>
          <Box
            title="Withdrawn, not deleted"
            dek="Something wrong that can't be corrected from its sources is withdrawn, and listed with its date and reason in a public log."
            more="the log so far"
          >
            <P>
              When something Civitas published is wrong and can&apos;t be corrected from its
              sources, it is withdrawn, not quietly deleted. Each withdrawal is listed with its date
              and reason in a public retraction log in the source code. A withdrawn issue&apos;s
              page says it was withdrawn and why; a withdrawn race update is left out of the feeds,
              and its story out of the race&apos;s page. The first entry, on 27 September 2026,
              withdrew two issues that a since-fixed grouping step had built from unrelated stories.
              The second, on 17 October 2026, withdrew an update on a New York House race that was
              really a newsletter about other things, and a check of the month before it withdrew
              sixteen more race updates linked to a race by a candidate&apos;s name alone or by a
              newsletter.
            </P>
          </Box>
        </Boxes>
      </Section>
    </AboutPage>
  );
}
