import Link from "next/link";
import type { ReactNode } from "react";
import { pageMetadata, GITHUB_REPO_URL } from "@/lib/site";
import { ABOUT_CHAPTERS } from "@/lib/aboutPages";
import { ACTION_CENTER_HREF } from "@/lib/routes";
import { AboutPage, Section, P, A } from "@/components/about/AboutPage";
import ScoreWeightBar from "@/components/about/ScoreWeightBar";
import LegacyAboutAnchor from "@/components/about/LegacyAboutAnchor";

export const metadata = pageMetadata({
  title: "About Civitas: How Members of Congress Are Scored",
  description:
    "What Civitas is, how it scores senators, representatives, presidents and justices from public records, and the rules it holds itself to: in five minutes.",
  path: "/about",
});

// The overview. It says what Civitas is and how a score works in the time a
// reader will actually give it, then hands off to the chapters (see
// lib/aboutPages.ts). Nothing here is the only place a fact is stated: every
// claim links to the chapter that explains it in full.

const PRINCIPLES: readonly { title: string; body: string }[] = [
  {
    title: "Public records only",
    body: "Every score is computed from official records: Congress.gov, the FEC, the Federal Register, the courts. Nothing is purchased, and nothing is invented to fill a gap.",
  },
  {
    title: "One formula for every party",
    body: "The same record in the same seat gets the same score, Democrat or Republican. No formula has a party term in it.",
  },
  {
    title: "No AI in any score",
    body: "Scores come from published formulas you can check by hand. AI sorts records into categories; it never decides a number.",
  },
  {
    title: "Missing data is neutral, not zero",
    body: "Nobody is penalized for what can't be measured. A thin record is pulled toward the middle rather than pushed to an extreme.",
  },
  {
    title: "Quoted, never rewritten",
    body: "Ballot measures and news stories are shown in their sources' own words, with the source named. No model writes what's on your ballot.",
  },
  {
    title: "Nothing asked about you",
    body: "No account, no address, no ZIP code, no tracking cookies. Visits are counted in a way that can't be traced back to anyone once the day is over.",
  },
];

const SCORE_PARTS: readonly {
  href: string;
  name: string;
  question: string;
  high: string;
  low: string;
}[] = [
  {
    href: "/about/scores#funding",
    name: "Funding Independence",
    question: "Who pays for their campaigns?",
    high: "Many small donors, money spread across many sources.",
    low: "Heavy reliance on PACs, a few big donors, or one industry.",
  },
  {
    href: "/about/scores#alignment",
    name: "Constituent Alignment",
    question: "Do they vote the way their seat elected them to?",
    high: "Breaks with their party about as often as members in similar seats.",
    low: "Breaks far more often than that, or, less costly, far less.",
  },
  {
    href: "/about/scores#effectiveness",
    name: "Legislative Effectiveness",
    question: "Do they get legislation done?",
    high: "Bills that advance and become law; cosponsors from both parties.",
    low: "Bills introduced and never moved.",
  },
];

const COVERAGE: readonly { what: string; detail: string; href: string }[] = [
  {
    what: "Senate & House",
    detail: "Every sitting member, scored and ranked",
    href: "/politicians",
  },
  {
    what: "Presidents",
    detail: "Every president, on economic, public and historical records",
    href: "/leaderboard?branch=president",
  },
  {
    what: "Supreme Court",
    detail: "The nine sitting justices",
    href: "/leaderboard?branch=scotus",
  },
  {
    what: "Your state's ballot",
    detail:
      "Federal and statewide contests, ballot measures quoted verbatim, and the live count on election night",
    href: "/elections",
  },
  {
    what: "Congress, day by day",
    detail: "What each chamber did, from the Congressional Record",
    href: "/congress",
  },
  {
    what: "Action Center",
    detail: "Today's civic news in its sources' own words",
    href: ACTION_CENTER_HREF,
  },
  {
    what: "Explore",
    detail: "Search speeches, executive orders, rules and opinions",
    href: "/explore",
  },
];

export default function AboutOverview() {
  return (
    <AboutPage
      href="/about"
      eyebrow="About · how Civitas works"
      title="About Civitas"
      lede={
        <p>
          Civitas scores the people who represent you (senators, representatives, presidents and
          Supreme Court justices) using only public records and formulas anyone can check.
        </p>
      }
    >
      <LegacyAboutAnchor />

      <Section id="principles" title="What we hold ourselves to">
        <ul className="grid gap-px border border-white/[0.07] bg-white/[0.07] sm:grid-cols-2">
          {PRINCIPLES.map((p) => (
            <li key={p.title} className="bg-surface p-5">
              <h3 className="font-display text-base font-semibold text-ink-hi">{p.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-lo">{p.body}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="the-score" title="How a member of Congress is scored">
        <P>
          Every senator and representative gets a Representation Score from 0 to 100, higher is
          better. It asks one question: does this member carry out the will of the people who
          elected them, rather than a few wealthy donors? It is built from three parts, each also 0
          to 100.
        </P>
        <ScoreWeightBar />
        <div className="grid gap-3 sm:grid-cols-3">
          {SCORE_PARTS.map((s) => (
            <Link
              key={s.href}
              href={s.href}
              className="panel flex flex-col p-4 transition-colors hover:border-ink-min"
            >
              <span className="font-display text-base font-semibold text-ink-hi">{s.name}</span>
              <span className="mt-1 text-sm text-ink">{s.question}</span>
              <span className="mt-3 text-sm text-ink-lo">
                <span className="font-mono text-xs uppercase tracking-[0.1em] text-phos-mid">
                  Higher
                </span>{" "}
                {s.high}
              </span>
              <span className="mt-1.5 text-sm text-ink-lo">
                <span className="font-mono text-xs uppercase tracking-[0.1em] text-signal-red">
                  Lower
                </span>{" "}
                {s.low}
              </span>
              <span className="mt-auto pt-3 font-mono text-xs text-signal-cyan">
                How it works →
              </span>
            </Link>
          ))}
        </div>
        <P>
          Scores cover the current Congress (two years of votes and bills), not a whole career,
          except funding, which covers the election that won the member their seat. The
          Representation Score, each of its three parts and each of their components has a{" "}
          <span className="font-mono text-ink-hi">[?]</span> beside it explaining what it measures.{" "}
          <A href="/about/scores">Read how each part is calculated</A>.
        </P>
      </Section>

      <Section id="coverage" title="What's covered">
        <ul className="divide-y divide-white/[0.07] border-y border-white/[0.07]">
          {COVERAGE.map((c) => (
            <li key={c.what}>
              <Link
                href={c.href}
                className="group grid gap-1 py-3 sm:grid-cols-[12rem_1fr_auto] sm:items-baseline sm:gap-4"
              >
                <span className="font-display font-semibold text-ink-hi group-hover:text-phos">
                  {c.what}
                </span>
                <span className="text-sm text-ink-lo">{c.detail}</span>
                <span aria-hidden="true" className="hidden font-mono text-ink-min sm:inline">
                  →
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="cannot-tell-you" title="What a score can't tell you">
        <ul className="space-y-3">
          <Caveat>
            <strong className="text-ink-hi">Why someone voted.</strong> A score reads what members
            did, not their reasons. A donor whose industry lines up with a vote shows where money
            and votes meet, not that the money bought the vote.
          </Caveat>
          <Caveat>
            <strong className="text-ink-hi">
              What your neighbors think about a specific bill.
            </strong>{" "}
            A seat&apos;s expectations come from how it votes for president, a broad stand-in for
            local opinion rather than a measure of it.
          </Caveat>
          <Caveat>
            <strong className="text-ink-hi">Which party is better.</strong> Democrats and
            Republicans fund campaigns differently on average, so average funding scores differ by
            party. The formula is the same for both; the fundraising isn&apos;t.
          </Caveat>
          <Caveat>
            <strong className="text-ink-hi">Your exact ballot.</strong> Ballots differ street by
            street. We show what&apos;s the same across a state and link you to your election office
            for the rest, rather than ask for your address.
          </Caveat>
        </ul>
        <P>
          <A href="/about/limitations">Every known limitation</A>, with the reason each is still
          open.
        </P>
      </Section>

      <Section id="chapters" title="The full method">
        <P>
          Every formula, data source and design choice is published, along with the evidence behind
          it and what we changed when the evidence disagreed.
        </P>
        <ul className="grid gap-3 sm:grid-cols-2">
          {ABOUT_CHAPTERS.map((c) => (
            <li key={c.href}>
              <Link
                href={c.href}
                className="panel flex h-full flex-col p-4 transition-colors hover:border-ink-min"
              >
                <span className="font-display text-base font-semibold text-ink-hi">{c.title}</span>
                <span className="mt-1 text-sm leading-relaxed text-ink-lo">{c.blurb}</span>
              </Link>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="who" title="Who runs it">
        <P>
          One developer, as a hobby, on a Raspberry Pi at home. There is no company, no cloud
          service, no advertising and no outside money: Civitas takes nothing from parties,
          candidates or PACs and sells nothing. It exists partly to show that a civic accountability
          tool doesn&apos;t need venture capital or a data center: the whole thing uses about as
          much electricity in a year as a refrigerator does in six weeks.
        </P>
        <div className="flex flex-wrap gap-2">
          <PillLink href={GITHUB_REPO_URL} external>
            Source code
          </PillLink>
          <PillLink href="/developers">Public API and MCP server</PillLink>
          <PillLink href="/changelog">Scoring changelog</PillLink>
          <PillLink href="/feedback">Disagree with a score? Tell us</PillLink>
        </div>
      </Section>
    </AboutPage>
  );
}

function Caveat({ children }: { children: ReactNode }) {
  return (
    <li className="border-l-3 border-ink-min/60 pl-4 text-base leading-relaxed text-ink">
      {children}
    </li>
  );
}

function PillLink({
  href,
  external = false,
  children,
}: {
  href: string;
  external?: boolean;
  children: ReactNode;
}) {
  const cls =
    "inline-block border border-white/[0.09] px-3 py-2 font-mono text-xs uppercase tracking-[0.08em] text-ink-lo transition-colors hover:border-ink-min hover:text-ink-hi";
  return external ? (
    <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>
      {children}
    </a>
  ) : (
    <Link href={href} className={cls}>
      {children}
    </Link>
  );
}
