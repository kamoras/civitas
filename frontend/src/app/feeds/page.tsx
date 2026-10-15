import { FEED_PATH, absoluteUrl, pageMetadata } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import CopyText from "@/components/CopyText";
import { Summary, Point, Section, P, List, Item, More, A } from "@/components/about/AboutPage";

export const metadata = pageMetadata({
  title: "Feeds",
  description:
    "Everything Civitas publishes, as Atom feeds: Action Center issues, Congress's day and week, the daily member spotlight, the record behind a trending topic, race news and election-night counts, by topic or by state. No sign-up.",
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
  return f.slug === "all" ? "Everything" : f.title.replace(/^Civitas: /, "");
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
      title: "Civitas",
      description: "Everything Civitas publishes.",
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
                election night, the count as each state reports it. The Bluesky account posts the
                same posts, when Bluesky accepts them. The spotlight and the Congress posts written
                overnight reach Bluesky later that day, in the hours U.S. users are most active; a
                feed has them at once.
              </Point>
              <Point>
                Follow everything, one topic or one state. Choosing a feed is your filter, so there
                is nothing to set up here. A state&apos;s ballot page links its state&apos;s feed,
                and the Congress pages the Congress feed. Give a feed reader a state, Congress or
                issue page and it is offered that page&apos;s own feed first.
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

            <Section id="using" title="How to follow one">
              <List>
                <Item label="A feed reader">
                  Paste a feed&apos;s address in as a new subscription.
                </Item>
                <Item label="Discord">
                  Discord doesn&apos;t read feeds itself. Add a feed bot to your server (MonitoRSS
                  is one) and give it a feed&apos;s address and the channel to post in. Each new
                  entry arrives as a message with its link and picture.
                </Item>
                <Item label="Slack">
                  Slack&apos;s RSS app takes a feed&apos;s address the same way.
                </Item>
                <Item label="Your own program">
                  The feeds are Atom 1.0. Each entry has a permanent id, a title, the post as HTML
                  (its picture, its text, a link to read it on Civitas, and the source it restates,
                  if any), the page&apos;s description as a summary, a link to the page it is about,
                  when it was published, and its kind as a category. The picture is also an
                  enclosure and a Media RSS thumbnail, the same picture a Bluesky post&apos;s link
                  card shows.
                </Item>
              </List>
              <More label="Details for anyone polling a feed">
                <p>
                  A feed holds the 50 newest entries. It is refreshed at most every five minutes, so
                  checking more often than that returns the same document. Send back the ETag you
                  were given and an unchanged feed answers 304 with no body.
                </p>
                <p>
                  An entry&apos;s words are never edited after it is published, and its id is never
                  reused. Its picture and summary come from the page it links to; if that page
                  couldn&apos;t be read at the moment of publishing, they arrive within the hour.
                  The text is the post in full; on Bluesky a long post can be cut short to fit, but
                  not here.
                </p>
              </More>
            </Section>

            <Section id="what" title="What is in a post">
              <P>
                The same rules as everything else Civitas publishes. An issue post is a claim quoted
                word for word from its sources, or their headline, for the day&apos;s top two issues
                only, and at most two a day. A Congress post is the day&apos;s counts from the
                official Record, filled into a fixed sentence, with bill numbers but never their
                titles, for a day or week that had a record vote, a measure passed or a nomination
                confirmed; a day of pro forma sessions isn&apos;t posted. Before a week of business,
                a weekend post says when each chamber next meets, from the Record&apos;s own notice,
                and which bills passed one chamber the week before and go to the other. A member
                spotlight is the one finding where their scorecard stands furthest from their
                chamber&apos;s, in the scorecard&apos;s own fixed sentence, then their score and
                rank. A race update names who did what, both parts copied word for word from a news
                report about that race, never one already posted, then names the outlet; it links to
                the state&apos;s ballot page, and the entry carries the article as its source. Posts
                by members of the public are never restated, and there are at most four a day. An
                election-night post is a state&apos;s own count (who leads, their share and how much
                is in) in a fixed sentence, saying &ldquo;leads&rdquo; until the state lists its
                count as official; Civitas never calls a race. An &ldquo;On the record&rdquo; post
                is made only when a U.S. policy topic trending on Bluesky has a pinned match to an
                official record: a vote, a bill, a rule or the day&apos;s congressional record.
                Every word is fixed template text, the record&apos;s own fields, or Bluesky&apos;s
                name for the trend, quoted and attributed to Bluesky. Its picture is the record
                itself (a vote&apos;s tally, a bill, a rule) with alt text. There are at most two a
                day, at least four hours apart and inside the posting window; the same record is
                posted at most once in 14 days, and never one an issue post already carried. Each
                links to the page with the full record. More on how the news is handled is in{" "}
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
