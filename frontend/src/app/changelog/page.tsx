import Link from "next/link";
import { connection } from "next/server";
import { pageMetadata, TOPIC_FEEDS } from "@/lib/site";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import { Summary, Point, A, More, List, Item } from "@/components/about/AboutPage";
import { formatUtcDate } from "@/lib/formatting";
import { SCORE_VERSIONS, type ScoreVersion } from "@/lib/scoreVersions";

export const metadata = pageMetadata({
  title: "Changelog",
  description:
    "What is new on Civitas, and the version history of its scoring algorithms: every formula and data-input change, when it was released, and why it was made.",
  path: "/changelog",
  feed: TOPIC_FEEDS.new,
});

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

interface Feature {
  id: string;
  date: string;
  title: string;
  summary: string;
  url: string;
}

/**
 * New pages and features, from backend/app/data/whats_new.json (GET
 * /api/whats-new): the same entries the What's new feed and Bluesky post
 * once each (backend/app/whats_new.py), so there is one place to write one.
 * Per request, never prerendered (`next build` can't reach the backend);
 * left out when the backend can't answer, and the scoring history below
 * still renders.
 */
async function NewOnTheSite() {
  await connection();
  let features: Feature[] = [];
  try {
    const res = await fetch(`${BACKEND}/api/whats-new`, { next: { revalidate: 300 } });
    if (res.ok) features = ((await res.json()) as { entries?: Feature[] }).entries ?? [];
  } catch {
    features = [];
  }
  if (!features.length) return null;
  return (
    <section aria-labelledby="new-h">
      <h2
        id="new-h"
        className="font-display text-2xl font-bold leading-tight tracking-[-0.01em] text-ink-hi"
      >
        New on the site
      </h2>
      <p className="mt-3 text-base leading-relaxed text-ink">
        New pages and features, newest first, dated by release. Each is also posted once to the{" "}
        <A href={TOPIC_FEEDS.new.url}>What&apos;s new feed</A> and to Bluesky.
      </p>
      <ul className="mt-4 divide-y divide-white/[0.07] border-y border-white/[0.07]">
        {features.map((f) => (
          <li
            key={f.id}
            id={`new-${f.id}`}
            className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[9rem_1fr] sm:gap-4"
          >
            <time
              dateTime={f.date}
              className="font-mono text-xs uppercase tracking-[0.08em] text-signal-amber sm:pt-1"
            >
              {released(f.date)}
            </time>
            <div>
              <Link
                href={f.url}
                className="font-display text-base font-semibold text-signal-cyan underline underline-offset-2 hover:text-phos"
              >
                {f.title}
              </Link>
              <p className="mt-1 text-sm leading-relaxed text-ink-lo">{f.summary}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Which score a version belongs to, from how its name is written. */
function family(v: ScoreVersion): "Congress" | "Presidents" | "Justices" {
  if (v.version.startsWith("President")) return "Presidents";
  if (v.version.startsWith("Justice")) return "Justices";
  return "Congress";
}

/** The list is newest first, so each score's current version is its first entry. */
const CURRENT = (["Congress", "Presidents", "Justices"] as const).map((f) => ({
  family: f,
  version: SCORE_VERSIONS.find((v) => family(v) === f),
}));

const released = (d: string) => formatUtcDate(d, undefined, "en-US");

/** Link target for a version: "v6.23" -> "v6-23", "President v5" -> "president-v5". */
const anchor = (v: ScoreVersion) => v.version.toLowerCase().replace(/[^a-z0-9]+/g, "-");

function Entry({ v }: { v: ScoreVersion }) {
  const id = anchor(v);
  return (
    <article id={id} aria-labelledby={`${id}-h`} className="border-t border-white/[0.09] pt-6">
      <p className="font-mono text-xs uppercase tracking-[0.16em] text-signal-cyan">
        {v.version} · released <time dateTime={v.date}>{released(v.date)}</time>
      </p>
      <h3
        id={`${id}-h`}
        className="mt-1 font-display text-lg font-semibold leading-snug text-ink-hi"
      >
        {v.title}
      </h3>
      <div className="mt-3 space-y-3">
        {v.tldr ? (
          <>
            <p className="text-base leading-relaxed text-ink">{v.tldr}</p>
            <More label="What changed, and the evidence">
              <List>
                {v.changes.map((c, i) => (
                  <Item key={i}>{c}</Item>
                ))}
              </List>
            </More>
          </>
        ) : (
          // Older entries have no plain-language summary; the detail is all
          // there is, so it isn't hidden behind a toggle.
          <List>
            {v.changes.map((c, i) => (
              <Item key={i}>{c}</Item>
            ))}
          </List>
        )}
      </div>
    </article>
  );
}

export default function ChangelogPage() {
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        {/* `font-sans`: this is reading, not data. The body element is
            `font-mono`, and prose that names no face inherits it. */}
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead
            className="mb-6"
            eyebrow="Changelog · new features and versioned scoring methodology"
            title="Changelog"
          >
            <p>
              What is new on the site, and every change to how scores are calculated, when it was
              released, and why it was made.
            </p>
          </PageMasthead>

          <div className="mt-10 space-y-12">
            <NewOnTheSite />

            <Summary>
              <Point>
                The scoring methods are versioned. A new version can move every score at once on the
                first nightly update after its release.
              </Point>
              <Point>
                The score trend charts mark the point where the version changed, so a methodology
                change isn&apos;t read as a change in what someone did.
              </Point>
              <Point>
                How each score is calculated today is on the{" "}
                <A href="/about/scores">How Congress is scored</A> and{" "}
                <A href="/about/presidents-and-justices">Presidents &amp; justices</A> pages.
              </Point>
            </Summary>

            <section aria-labelledby="current-h">
              <h2
                id="current-h"
                className="font-display text-2xl font-bold leading-tight tracking-[-0.01em] text-ink-hi"
              >
                Current versions
              </h2>
              <dl className="mt-4 divide-y divide-white/[0.07] border-y border-white/[0.07]">
                {CURRENT.map(
                  ({ family: f, version: v }) =>
                    v && (
                      <div
                        key={f}
                        className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[13rem_1fr] sm:gap-4"
                      >
                        <dt className="font-mono text-xs uppercase tracking-[0.08em] text-signal-amber sm:pt-0.5">
                          {f}
                        </dt>
                        <dd className="text-sm leading-relaxed text-ink-lo">
                          <a
                            href={`#${anchor(v)}`}
                            className="text-signal-cyan underline underline-offset-2 hover:text-phos"
                          >
                            {v.version}
                          </a>
                          , released {released(v.date)}
                        </dd>
                      </div>
                    )
                )}
              </dl>
            </section>

            <section aria-labelledby="history-h">
              <h2
                id="history-h"
                className="font-display text-2xl font-bold leading-tight tracking-[-0.01em] text-ink-hi"
              >
                Every version, newest first
              </h2>
              <p className="mt-3 text-base leading-relaxed text-ink">
                Dates are when a version was released. Versions up to v5.8 (July 12, 2026) predate
                the public code history, so their dates can&apos;t be checked against it.
              </p>
              <div className="mt-6 space-y-8">
                {SCORE_VERSIONS.map((v) => (
                  <Entry key={v.version} v={v} />
                ))}
              </div>
            </section>
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
