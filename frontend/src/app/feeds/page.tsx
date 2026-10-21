import { FEED_PATH, absoluteUrl, pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import CopyText from "@/components/CopyText";
import {
  Summary,
  Point,
  Section,
  P,
  List,
  Item,
  More,
  A,
  Boxes,
  Box,
} from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "Feeds",
  description:
    "Everything Civitas publishes, as Atom feeds: Action Center issues, Congress's day and week, the daily member spotlight, race news and new features, by topic or by state, and a feed for one member, bill, agency or story. No sign-up.",
  path: "/feeds",
});

// Rendered per request (the backend fetch below is cached for an hour):
// `next build` runs where the backend isn't reachable, and a prerendered
// page would ship with the fallback list until the next deploy.
export const dynamic = "force-dynamic";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

interface FeedIndex {
  feeds: { slug: string; title: string; description: string; path: string }[];
  states: { code: string; name: string; path: string }[];
  /** A feed for one followed thing (backend api/feed.py FOLLOWED). */
  followed?: { kind: string; title: string; description: string; pattern: string; where: string }[];
}

/** The feed list is the backend's (app/broadcast.py FEEDS), not a copy. */
async function fetchIndex(): Promise<FeedIndex | null> {
  try {
    const res = await fetch(`${BACKEND}/api/feeds`, { next: { revalidate: 3600 } });
    if (!res.ok) return null;
    const data = await res.json();
    if (!Array.isArray(data?.feeds) || !Array.isArray(data?.states)) return null;
    return data as FeedIndex;
  } catch {
    return null;
  }
}

/** "Everything", or the topic ("Civitas: Congress" -> "Congress"). */
function feedName(f: { slug: string; title: string }): string {
  return f.slug === "all" ? "Everything" : f.title.replace(/^Civitas Research: /, "");
}

/* Every claim here is checked against the code (backend/app/broadcast.py,
   backend/app/api/feed.py, nginx/civitas.conf). Change this page with them. */
export default async function FeedsPage() {
  const index = await fetchIndex();
  // Without the backend the page still gives the one feed it can't be
  // wrong about.
  const feeds = index?.feeds ?? [
    {
      slug: "all",
      title: "Civitas Research",
      description: "Everything Civitas Research publishes.",
      path: FEED_PATH,
    },
  ];

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead className="mb-6" eyebrow="Feeds · follow without an account" title="Feeds">
            <p>
              Everything Civitas posts, as feeds you can follow in a feed reader, a Discord or Slack
              channel, or your own program.
            </p>
          </PageMasthead>

          <div className="mt-10 space-y-12">
            <Summary>
              <Point>
                Every post Civitas publishes goes into these feeds first: Action Center issues, what
                Congress did each day and week, the daily member spotlight, the official record
                behind a policy topic trending on Bluesky, news about races on the ballot and, on
                election night, the count as each state reports it, and each new page or feature,
                once.
              </Point>
              <Point>
                The Bluesky account posts the same posts, when Bluesky accepts them. The spotlight
                and the Congress posts written overnight reach Bluesky later that day, in the hours
                U.S. users are most active; a feed has them at once.
              </Point>
              <Point>
                Follow everything, one topic, one state, or one thing: a member of Congress, a bill,
                a federal agency or a story the Action Center tracks. Choosing a feed is your
                filter, so there is nothing to set up here. Each of those pages links its own feed,
                and a feed reader given the page is offered that feed first.
              </Point>
              <Point>
                There is nothing to sign up for and no list of who follows. Reading a feed is the
                same kind of request as opening a page.
              </Point>
            </Summary>

            <Section id="feeds" title="Which feed to follow">
              <ul className="space-y-6">
                {feeds.map((f) => (
                  <li key={f.slug} className="space-y-2">
                    <h3 className="font-display text-lg font-semibold text-ink-hi">
                      <a
                        href={f.path}
                        className="underline decoration-ink-min/50 underline-offset-2 hover:text-phos"
                      >
                        {feedName(f)}
                      </a>
                    </h3>
                    <P>{f.description}</P>
                    <CopyText text={absoluteUrl(f.path)} label={`${feedName(f)} feed address`} />
                  </li>
                ))}
              </ul>
              {index && (
                <More label="A feed for each state: its races and its members of Congress">
                  <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
                    {index.states.map((s) => (
                      <li key={s.code}>
                        <a
                          href={s.path}
                          className="text-signal-cyan underline underline-offset-2 hover:text-phos"
                        >
                          {s.name}
                        </a>
                      </li>
                    ))}
                  </ul>
                  <p>
                    Each is at{" "}
                    <code className="font-mono text-xs">{absoluteUrl("/feed/states/")}</code>{" "}
                    followed by the state&apos;s two-letter code and{" "}
                    <code className="font-mono text-xs">.xml</code>, such as{" "}
                    <code className="font-mono text-xs">GA.xml</code>. Action Center issues and
                    Congress posts are national, so they are only in the topic feeds.
                  </p>
                </More>
              )}
            </Section>

            {index?.followed && index.followed.length > 0 && (
              <Section id="one-thing" title="Follow one member, bill, agency or story">
                <Boxes columns={1}>
                  <Box
                    title="Entries restate the record"
                    dek="Each entry is a fixed sentence around the official record, linking its page here and its source."
                    more="how entries are built"
                  >
                    <P>
                      These feeds aren&apos;t posts: each entry is a fixed sentence around the
                      official record as Civitas stores it, linking the page here and the source it
                      comes from. They are built from the record when asked for, so an entry can
                      change if the record it restates is corrected; its id stays the same.
                    </P>
                  </Box>
                </Boxes>
                <List>
                  {index.followed.map((k) => (
                    <Item key={k.kind} label={k.title}>
                      {k.description} {k.where} Its address is{" "}
                      <code className="font-mono text-xs break-all">{absoluteUrl(k.pattern)}</code>.
                    </Item>
                  ))}
                </List>
              </Section>
            )}

            <Section id="using" title="How to follow one">
              <Boxes>
                <Box title="A feed reader" dek="Paste a feed's address in as a new subscription." />
                <Box
                  title="Discord"
                  dek="Add a feed bot to your server and give it a feed's address and a channel."
                  more="how it works"
                >
                  <P>
                    Discord doesn&apos;t read feeds itself. Add a feed bot to your server (MonitoRSS
                    is one) and give it a feed&apos;s address and the channel to post in. Each new
                    entry arrives as a message with its link and picture.
                  </P>
                </Box>
                <Box title="Slack" dek="Slack's RSS app takes a feed's address the same way." />
                <Box
                  title="Your own program"
                  dek="The feeds are Atom 1.0. Each entry has a permanent id, the post as HTML and a link to its page."
                  more="every field of an entry"
                >
                  <P>
                    The feeds are Atom 1.0. Each entry has a permanent id, a title, the post as HTML
                    (its picture, its text, a link to read it on Civitas, and the source it
                    restates, if any), the page&apos;s description as a summary, a link to the page
                    it is about, when it was published, and its kind as a category. The picture is
                    also an enclosure and a Media RSS thumbnail, the same picture a Bluesky
                    post&apos;s link card shows.
                  </P>
                </Box>
                <Box
                  title="Polling a feed"
                  dek="A feed holds the 50 newest entries and changes at most every five minutes. Entries are never edited."
                  figure={{ type: "stat", value: 50, caption: "newest entries" }}
                  more="details for anyone polling a feed"
                >
                  <P>
                    A feed holds the 50 newest entries. It is refreshed at most every five minutes,
                    so checking more often than that returns the same document. Send back the ETag
                    you were given and an unchanged feed answers 304 with no body.
                  </P>
                  <P>
                    An entry&apos;s words are never edited after it is published, and its id is
                    never reused. Its picture and summary come from the page it links to; if that
                    page couldn&apos;t be read at the moment of publishing, they arrive within the
                    hour. The text is the post in full; on Bluesky a long post can be cut short to
                    fit, but not here.
                  </P>
                </Box>
              </Boxes>
            </Section>

            <Section id="what" title="What is in a post">
              <P>The same rules as everything else Civitas publishes.</P>
              <Boxes>
                <Box
                  title="Issue posts"
                  dek="An issue post is a claim quoted word for word from its sources, or their headline, for the day's top two issues only, and at most two a day."
                />
                <Box
                  title="Congress posts"
                  dek="The day's counts from the official Record in a fixed sentence, only for a day or week with real business."
                  more="what counts, and the weekend post"
                >
                  <P>
                    A Congress post is the day&apos;s counts from the official Record, filled into a
                    fixed sentence, with bill numbers but never their titles, for a day or week that
                    had a record vote, a measure passed or a nomination confirmed; a day of pro
                    forma sessions isn&apos;t posted. Before a week of business, a weekend post says
                    when each chamber next meets, from the Record&apos;s own notice, and which bills
                    passed one chamber the week before and go to the other.
                  </P>
                </Box>
                <Box
                  title="Member spotlight"
                  dek="A member spotlight is the one finding where their record stands furthest from their chamber's, in the member page's own fixed sentence, with no score or rank."
                />
                <Box
                  title="Race updates"
                  dek="Who did what in a race, copied word for word from a news report, with the outlet named. At most four a day."
                  more="the rules for race updates"
                >
                  <P>
                    A race update names who did what, both parts copied word for word from a news
                    report about that race, never one already posted, then names the outlet; it
                    links to the state&apos;s ballot page, and the entry carries the article as its
                    source. Posts by members of the public are never restated, and there are at most
                    four a day.
                  </P>
                </Box>
                <Box
                  title="Election night"
                  dek="A state's own count in a fixed sentence. Civitas never calls a race."
                  more="what the sentence says"
                >
                  <P>
                    An election-night post is a state&apos;s own count (who leads, their share and
                    how much is in) in a fixed sentence, saying &ldquo;leads&rdquo; until the state
                    lists its count as official; Civitas never calls a race.
                  </P>
                </Box>
                <Box
                  title="On the record"
                  dek="Made only when a policy topic trending on Bluesky matches an official record. At most two a day."
                  figure={{ type: "stat", value: 2, caption: "a day at most" }}
                  more="the match, the words and the limits"
                >
                  <P>
                    An &ldquo;On the record&rdquo; post is made only when a U.S. policy topic
                    trending on Bluesky has a pinned match to an official record: a vote, a bill, a
                    rule or the day&apos;s congressional record. Every word is fixed template text
                    or the record&apos;s own fields, then the record&apos;s date and source. Its
                    picture is the record itself (a vote&apos;s tally, a bill, a rule) with alt
                    text. There are at most two a day, at least four hours apart and inside the
                    posting window; the same record is posted at most once in 14 days, and never one
                    an issue post already carried. Each links to the page with the full record.
                  </P>
                </Box>
              </Boxes>
              <P>
                More on how the news is handled is in{" "}
                <A href="/about/news">News &amp; Congress reports</A>.
              </P>
            </Section>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
