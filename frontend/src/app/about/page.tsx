import Link from "next/link";
import type { ReactNode } from "react";
import { pageMetadata, GITHUB_REPO_URL } from "@/lib/site";
import { aboutPageJsonLd } from "@/lib/seo";
import JsonLd from "@/components/seo/JsonLd";
import { ABOUT_CHAPTERS } from "@/lib/aboutPages";
import { ACTION_CENTER_HREF, MEMBERS_HREF, PRESIDENTS_HREF } from "@/lib/routes";
import { AboutPage, Section, P, A } from "@/components/about/AboutPage";
import LegacyAboutAnchor from "@/components/about/LegacyAboutAnchor";

const ABOUT_DESCRIPTION =
  "What Civitas Research is, how it sets each member of Congress beside peers from public records, how presidents are measured (and why not justices), and the rules it holds itself to.";

// Absolute: the page names the site itself, so the template's
// " | Civitas Research" would say it twice.
export const metadata = pageMetadata({
  title: "About Civitas Research: How Members of Congress Are Compared",
  absoluteTitle: true,
  description: ABOUT_DESCRIPTION,
  path: "/about",
});

// The overview. It says what Civitas is and how a member is shown in the time a
// reader will actually give it, then hands off to the chapters (see
// lib/aboutPages.ts). Nothing here is the only place a fact is stated: every
// claim links to the chapter that explains it in full.

const PRINCIPLES: readonly { title: string; body: string }[] = [
  {
    title: "Public records only",
    body: "Every figure comes from official records: Congress.gov, the FEC, the Federal Register, the courts. Nothing is purchased, and nothing is invented to fill a gap.",
  },
  {
    title: "One formula for every party",
    body: "The same record in the same seat reads the same way, Democrat or Republican. No formula has a party term in it.",
  },
  {
    title: "No AI in any figure",
    body: "Figures come from published formulas you can check by hand. AI sorts records into categories; it never decides a number.",
  },
  {
    title: "Missing data is neutral, not zero",
    body: "What can't be measured is marked as such, never counted as zero. A figure with too little record behind it gets no word beside it.",
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

const COVERAGE: readonly { what: string; detail: string; href: string }[] = [
  {
    what: "Senate & House",
    detail: "Every sitting member, found by state, town or county",
    href: MEMBERS_HREF,
  },
  {
    what: "Presidents",
    detail: "Every president, on economic, public and historical records",
    href: PRESIDENTS_HREF,
  },
  {
    what: "Supreme Court",
    detail: "The sitting justices",
    href: "/court",
  },
  {
    what: "Your state's ballot",
    detail:
      "Federal and statewide contests, ballot measures quoted verbatim, and the live count on election night",
    href: "/elections",
  },
  {
    what: "Congress, day by day",
    detail:
      "What each chamber did, from the Congressional Record, under a calendar of the days each chamber sat and how far this Congress's bills got (each step lists its bills); during a recess, the last week of business. With your state picked, your members' latest votes and bills. Each bill's page gives how often bills at its stage became law this Congress",
    href: "/congress",
  },
  {
    what: "Action Center",
    detail: "Today's civic news in its sources' own words",
    href: ACTION_CENTER_HREF,
  },
  {
    what: "Search",
    detail: "Members, bills, pages and every speech, rule, order and opinion, in one list",
    href: "/search",
  },
];

export default function AboutOverview() {
  return (
    <AboutPage
      href="/about"
      eyebrow="About · how Civitas Research works"
      title="About Civitas Research"
      lede={
        <p>
          Civitas sets the record of the people who represent you (senators, representatives and
          presidents) beside their peers, using only public records and formulas anyone can check,
          and keeps the record of Supreme Court justices.
        </p>
      }
    >
      <JsonLd data={aboutPageJsonLd(ABOUT_DESCRIPTION)} />
      <LegacyAboutAnchor />

      <Section id="principles" title="What we hold ourselves to">
        <ul className="grid grid-cols-1 gap-px border border-white/[0.07] bg-white/[0.07] sm:grid-cols-2">
          {PRINCIPLES.map((p) => (
            <li key={p.title} className="bg-surface p-5">
              <h3 className="font-display text-base font-semibold text-ink-hi">{p.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-lo">{p.body}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="peers" title="How a member of Congress is shown">
        <P>
          Each member page sets four figures beside the same figures for colleagues in the same
          party: <A href="/about/scores#funding">small gifts and PAC money</A>,{" "}
          <A href="/about/scores#alignment">breaks with the party</A> and{" "}
          <A href="/about/scores#effectiveness">bills moved</A>. A word says if the member is above,
          near or below them, by a fixed rule. There is no overall score and no rank.
        </P>
        <P>
          Figures cover the current Congress, except money, which covers the election that won the
          seat. <A href="/about/scores">How each figure is measured</A>.
        </P>
      </Section>

      <Section id="coverage" title="What's covered">
        <ul className="divide-y divide-white/[0.07] border-y border-white/[0.07]">
          {COVERAGE.map((c) => (
            <li key={c.what}>
              <Link
                href={c.href}
                className="group grid grid-cols-1 gap-1 py-3 sm:grid-cols-[12rem_1fr_auto] sm:items-baseline sm:gap-4"
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

      <Section id="cannot-tell-you" title="What the figures can't tell you">
        <ul className="space-y-3">
          <Caveat>
            <strong className="text-ink-hi">Why someone voted.</strong> The record shows what
            members did, not their reasons. A donor whose industry lines up with a vote shows where
            money and votes meet, not that the money bought the vote.
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
            Republicans fund campaigns differently on average, so their funding figures differ too.
            The method is the same for both; the fundraising isn&apos;t.
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
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          One developer, as a hobby, on a Raspberry Pi at home. Civitas is not a company, a charity
          or any kind of registered group. There is no cloud service, no advertising and no outside
          money: it takes nothing from parties, candidates or PACs and sells nothing. It exists
          partly to show that a civic accountability tool doesn&apos;t need venture capital or a
          data center: the whole thing uses about as much electricity in a year as a refrigerator
          does in six weeks.
        </P>
        <P>
          Civitas Research (civitas-research.org) is an independent, open-source political
          transparency project covering the U.S. federal government. It is not affiliated with any
          other organization named Civitas, such as think tanks, university institutes or companies.
        </P>
        <div className="flex flex-wrap gap-2">
          <PillLink href={GITHUB_REPO_URL} external>
            Source code
          </PillLink>
          <PillLink href="/developers">Public API and MCP server</PillLink>
          <PillLink href="/changelog">Changelog</PillLink>
          <PillLink href="/feedback">Disagree with a figure? Tell us</PillLink>
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
    "inline-block border border-white/[0.09] px-3 py-2 font-mono text-xs text-ink-lo transition-colors hover:border-ink-min hover:text-ink-hi";
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
