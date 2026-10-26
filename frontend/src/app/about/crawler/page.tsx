import { GITHUB_REPO_URL, pageMetadata } from "@/lib/site";
import {
  AboutPage,
  Summary,
  Point,
  Section,
  Boxes,
  Box,
  P,
  List,
  Item,
  Facts,
  Fact,
  A,
} from "@/components/about/AboutPage";
import { fetchCrawlerFacts } from "@/lib/crawler";
import type { CrawlerFacts } from "@/types/crawler";

// The facts below are read from the backend (GET /api/crawler), which reads
// them from its own code; `next build` can't reach it, so a short window
// keeps the build's "can't be read" wording from standing. A literal: Next
// reads it statically.
export const revalidate = 300;

export const metadata = pageMetadata({
  title: "The Civitas Crawler",
  description:
    "What the Civitas crawler is, who runs it and how to reach them, every site it reads and how fast, when it runs, its User-Agent, and how a site can ask for a change or opt out.",
  path: "/about/crawler",
});

const CODE = "break-all font-id text-small text-ink-hi";

function Unreadable() {
  return (
    <P>This part is read from the server&apos;s own code, and it can&apos;t be read right now.</P>
  );
}

function Identity({ facts }: { facts: CrawlerFacts }) {
  return (
    <Boxes>
      <Box
        id="user-agent"
        title="Its name in your logs"
        dek={<code className={CODE}>{facts.userAgent}</code>}
        more="what each part means"
      >
        <P>
          Every request the crawler sends carries this User-Agent: the product name and version,
          this page&apos;s address, and the address that reads mail about it. The only exceptions
          are the sites listed under &ldquo;Where it sends another form&rdquo;.
        </P>
      </Box>
      <Box
        id="exceptions"
        title="Where it sends another form"
        dek={`${facts.userAgentExceptions.length} sites refuse the name above. Each gets the nearest form it accepts, all still naming Civitas.`}
        more="each site, its form and why"
      >
        <Facts>
          {facts.userAgentExceptions.map((e) => (
            <Fact key={e.host} label={e.host}>
              <code className={CODE}>{e.userAgent}</code>
              <span className="block">Measured: it {e.reason}.</span>
            </Fact>
          ))}
        </Facts>
      </Box>
      <Box
        id="contact"
        title="Who runs it"
        dek={
          facts.contact ? (
            <>
              Civitas Research, the project behind this site. Write to{" "}
              <a
                href={`mailto:${facts.contact}`}
                className="text-signal-cyan underline underline-offset-2 hover:text-phos"
              >
                {facts.contact}
              </a>
              .
            </>
          ) : (
            "Civitas Research. This copy of the site has no contact address set, so its requests carry none."
          )
        }
        more="the code"
      >
        <P>
          The crawler is part of Civitas, and all of its code is public:{" "}
          <A href={GITHUB_REPO_URL}>the repository</A>. Anyone can check that what this page says is
          what it does.
        </P>
      </Box>
      <Box
        id="signed-requests"
        title="Signed requests"
        dek={
          facts.signing.configured
            ? "Each request is signed with a key only this server holds (Web Bot Auth), so a site can check it really is Civitas."
            : "Not yet: this server holds no signing key, so its requests are named but not signed."
        }
        more="how a site checks the signature"
      >
        {facts.signing.configured && facts.signing.directory ? (
          <P>
            The public key is published at <code className={CODE}>{facts.signing.directory}</code>.
            Bot-management services such as Cloudflare and Akamai read it there. A signature
            identifies Civitas; it is not a way past a site that says no.
          </P>
        ) : (
          <P>
            Web Bot Auth, a draft internet standard built on HTTP Message Signatures, lets a site
            check that a request came from the crawler it names. Once this server has its key, this
            box says so and gives the address of the public key.
          </P>
        )}
      </Box>
    </Boxes>
  );
}

function Sources({ facts }: { facts: CrawlerFacts }) {
  return (
    <Boxes columns={1}>
      <Box
        id="hosts"
        title="Every site it reads"
        dek="Government offices, courts, public datasets and newsrooms, each named in the code that reads it, with that reader's limit."
        figure={{
          type: "stat",
          value: facts.hostCount,
          caption: "sites named in the crawler's code",
        }}
        more={`all ${facts.hostCount} sites and their limits`}
      >
        <Facts>
          {facts.hosts.map((h) => (
            <Fact key={h.host} label={h.host}>
              <ul className="space-y-1">
                {h.readers.map((r) => (
                  <li key={`${r.module} ${r.registry ?? ""}`}>
                    <span className="font-id text-small text-ink-hi">{r.module}</span>: {r.pace}
                  </li>
                ))}
                <li>At least {h.minimumGap} between requests to it, one at a time.</li>
              </ul>
            </Fact>
          ))}
        </Facts>
      </Box>
    </Boxes>
  );
}

function Schedule({ facts }: { facts: CrawlerFacts }) {
  return (
    <Boxes>
      <Box
        id="nightly"
        title="The nightly run"
        dek={`Starts ${facts.nightly.when}: ${facts.nightly.links.join(", ")}, one after another.`}
        more="what it reads"
      >
        <P>
          Each link reads the records it needs (votes, bills, campaign finance, disclosures,
          elections), mostly from records it has already stored, asking a source again only for what
          has changed or gone stale. One link runs at a time.
        </P>
      </Box>
      <Box
        id="recurring"
        title="Through the day"
        dek="Smaller jobs read news feeds and the floor record every hour, and election sources only in election season."
        more="every recurring job"
      >
        <List>
          {facts.schedule.map((s) => (
            <Item key={s.job}>
              {s.reads}: {s.when}.
            </Item>
          ))}
        </List>
      </Box>
    </Boxes>
  );
}

function Manners({ facts }: { facts: CrawlerFacts }) {
  const m = facts.manners;
  return (
    <Boxes>
      <Box
        id="pace"
        title="One request at a time"
        dek={`Never two requests to one site at once, and at least ${m.minimumGap} between them, longer where a site's robots.txt asks.`}
        more="how the pace is set"
      >
        <P>
          The gap is the site&apos;s Crawl-delay where its robots.txt names one. Otherwise it is{" "}
          {m.minimumGap}, or a reader&apos;s own published limit where that is faster (the list
          above gives each site&apos;s). A request that would have to wait more than {m.longestWait}{" "}
          is not sent: it fails, saying why.
        </P>
      </Box>
      <Box
        id="refusals"
        title="A refusal is final"
        dek={`A challenge page or a "too many requests" stops all requests to that site: for ${m.firstBackoff}, then twice as long each time it refuses again, up to ${m.longestBackoff}.`}
        more="what it does on a refusal"
      >
        <P>
          When the wait ends, one request is sent to see whether the site will answer; an answer
          clears the record, another refusal doubles the wait. The record is kept on the server, so
          every job and every restart keeps to it. A Retry-After is honoured: a short one sets the
          next request&apos;s time, a long one is a wait of its own. A challenge page
          (Cloudflare&apos;s, Akamai&apos;s, Imperva&apos;s) is never answered or worked around, and
          any cookie it sets is thrown away. A source that refuses is recorded as unreadable, with
          the reason, never as having nothing to say. A plain &ldquo;forbidden&rdquo; for one
          address is not asked again in the same run.
        </P>
      </Box>
      <Box
        id="robots"
        title="robots.txt"
        dek={`It sets the pace, not what is read: a site's Crawl-delay is kept, but a Disallow does not stop the crawler reading.`}
        more="why, and how it is read"
      >
        <P>
          Each site&apos;s robots.txt is read before the first request to it, under the name
          &ldquo;Civitas&rdquo;, and kept {m.robotsKept}. Its Crawl-delay, if it names one, sets the
          gap between requests. Its Allow and Disallow rules are not applied: the crawler reads
          public records at the addresses its code names, the same documents a person would open,
          not pages it discovers by following links.
        </P>
        <P>
          One part of the crawler does follow Disallow: the weekly search for new state election
          pages, which follows links on election offices&apos; sites, skips anything robots.txt
          keeps crawlers from.
        </P>
      </Box>
      <Box
        id="caching"
        title="Asked once, kept"
        dek="Records are stored and asked for again only when they change: news feeds are asked only for what changed since the last read."
        more="how"
      >
        <P>
          A feed that sends an ETag or Last-Modified is asked with If-None-Match and
          If-Modified-Since, and a &ldquo;not modified&rdquo; answer costs the site no body. A
          bill&apos;s records are read again only after Congress.gov lists the bill as updated.
          Pictures are fetched once and served from this site, so a reader&apos;s browser never asks
          your server for them.
        </P>
      </Box>
      <Box
        id="refusing-now"
        title="Refusing it now"
        dek={
          facts.refusing.length
            ? `${facts.refusing.length} site${facts.refusing.length === 1 ? " is" : "s are"} refusing the crawler, and are not being asked.`
            : "No site is refusing the crawler right now."
        }
        more={facts.refusing.length ? "which, and until when" : undefined}
      >
        {facts.refusing.length ? (
          <Facts>
            {facts.refusing.map((r) => (
              <Fact key={r.host} label={r.host}>
                {r.reason}; refused {r.times} time{r.times === 1 ? "" : "s"} in a row since{" "}
                {r.since}, next asked after {r.until} UTC.
              </Fact>
            ))}
          </Facts>
        ) : null}
      </Box>
    </Boxes>
  );
}

export default async function CrawlerChapter() {
  const facts = await fetchCrawlerFacts();
  return (
    <AboutPage
      href="/about/crawler"
      eyebrow="About · our crawler"
      title="The Civitas crawler"
      lede={<p>Who is reading your site, and why.</p>}
      intro={
        <p>
          Civitas reads public records from government sites, courts, public datasets and newsrooms.
          This page says who it is, what it reads, how fast and when, and how to ask it to change.
        </p>
      }
    >
      <Summary>
        <Point>
          It reads public records to build this site, which is free and has no ads. It never logs
          in, buys data, or reads anything behind a paywall.
        </Point>
        <Point>
          It always names itself. A few sites refuse its usual name and get the nearest form they
          accept instead, each listed on this page.
        </Point>
        <Point>
          It asks for little and slowly: each reader has its own limit, and most records are read
          once and kept.
        </Point>
        <Point>
          A site that refuses it is respected. The crawler never works around a block, a challenge
          or a login.
        </Point>
      </Summary>

      <Section id="identity" title="Who it is">
        {facts ? <Identity facts={facts} /> : <Unreadable />}
      </Section>

      <Section id="reads" title="What it reads, and how fast">
        {facts ? <Sources facts={facts} /> : <Unreadable />}
      </Section>

      <Section id="schedule" title="When it runs">
        {facts ? <Schedule facts={facts} /> : <Unreadable />}
      </Section>

      <Section id="manners" title="How it behaves">
        {facts ? <Manners facts={facts} /> : <Unreadable />}
      </Section>

      <Section id="contact-us" title="Asking for a change, or opting out">
        <Boxes>
          <Box
            id="opt-out"
            title="To slow it or keep it out"
            dek={
              <>
                To slow it, add a <code className={CODE}>Crawl-delay</code> for{" "}
                <code className={CODE}>User-agent: Civitas</code> to your robots.txt. To keep it
                out, write to us.
              </>
            }
            more="what happens next"
          >
            <P>
              A Crawl-delay is read within a day and followed from then on. robots.txt does not stop
              the crawler reading, so to be left out, write to us and we will stop the readers that
              read your site. A reader that can&apos;t read your site is shown as unreadable on our
              pages, never filled in another way.
            </P>
          </Box>
          <Box
            id="ask"
            title="To ask for anything else"
            dek={
              facts?.contact ? (
                <>
                  Write to{" "}
                  <a
                    href={`mailto:${facts.contact}`}
                    className="text-signal-cyan underline underline-offset-2 hover:text-phos"
                  >
                    {facts.contact}
                  </a>{" "}
                  with your site&apos;s name and what you&apos;d like changed: fewer requests,
                  different hours, or a different address to read.
                </>
              ) : (
                "Open an issue on the project's repository with your site's name and what you'd like changed."
              )
            }
            links={[{ label: "The project's repository", href: GITHUB_REPO_URL }]}
          />
        </Boxes>
      </Section>
    </AboutPage>
  );
}
