import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import Footer from "@/components/layout/Footer";
import { ABOUT_CHAPTERS, ABOUT_OVERVIEW_HREF, adjacentChapters } from "@/lib/aboutPages";
import { REFERENCES, type RefId } from "./references";
import OnThisPage from "@/components/shared/OnThisPage";
import { onThisPage } from "@/lib/onThisPage";

/**
 * Shared frame and prose parts for the About section (see lib/aboutPages.ts
 * for why it is split into chapters).
 *
 * Writing rules these parts exist to enforce, learned from the page they
 * replace:
 *
 * - Every chapter opens with `Summary`: the handful of things a reader
 *   should leave with, in plain words, before any mechanism.
 * - Paragraphs describe how things work NOW. How a method got here — the
 *   version it changed in, the audit that prompted it, the numbers from a
 *   test — goes in a `More` block under the paragraph it supports, closed by
 *   default. It stays one click away, which is the point of publishing it,
 *   without being the first thing every reader has to wade through.
 * - Headings are sentences a reader would ask ("Which votes count"), not
 *   system names.
 * - A chapter with CONTENTS_MIN_SECTIONS or more `Section`s gets an "On
 *   this page" list of them after its Summary, built from the Sections
 *   themselves so it can't drift from the headings. /about/scores ran to
 *   about 26 phone screens with no way to jump to a part.
 *
 * Server components throughout, like the page they replace: `<details>`
 * gives expand/collapse, keyboard support and find-in-page for free.
 */

export function AboutPage({
  href,
  eyebrow,
  title,
  lede,
  children,
}: {
  href: string;
  eyebrow: string;
  title: string;
  lede: ReactNode;
  children: ReactNode;
}) {
  const { prev, next } = adjacentChapters(href);
  const body = withContents(children);
  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <OnThisPage />
        {/* `font-sans`: this is reading, not data. The body element is
            `font-mono`, and prose that names no face inherits it. */}
        <div className="max-w-3xl mx-auto font-sans">
          <PageMasthead className="mb-6" eyebrow={eyebrow} title={title}>
            {lede}
          </PageMasthead>
          <ChapterNav current={href} />
          <div className="mt-10 space-y-12">{body}</div>
          {(prev || next || href !== ABOUT_OVERVIEW_HREF) && (
            <nav
              aria-label="Chapter"
              className="mt-14 grid grid-cols-1 gap-3 border-t-3 border-ink-min/60 pt-6 sm:grid-cols-2"
            >
              {prev ? (
                <PagerLink href={prev.href} dir="Previous" title={prev.title} />
              ) : (
                <PagerLink
                  href={ABOUT_OVERVIEW_HREF}
                  dir="Back to"
                  title="About Civitas Research"
                />
              )}
              {next && <PagerLink href={next.href} dir="Next" title={next.title} alignEnd />}
            </nav>
          )}
        </div>
      </main>
      <Footer />
    </>
  );
}

/** Fewer sections than this and the page is short enough to scan. */
const CONTENTS_MIN_SECTIONS = 4;

/** The chapter's children with an "On this page" list of its top-level
 *  Sections placed after its Summary (first, when it has none). */
function withContents(children: ReactNode): ReactNode {
  const kids = Children.toArray(children);
  const sections = kids
    .filter((c): c is ReactElement<{ id: string; title: string }> => {
      return isValidElement(c) && c.type === Section;
    })
    .map((c) => ({ id: c.props.id, title: c.props.title }));
  if (sections.length < CONTENTS_MIN_SECTIONS) return kids;
  const at = kids.findIndex((c) => isValidElement(c) && c.type === Summary) + 1;
  return [
    ...kids.slice(0, at),
    <Contents key="on-this-page" sections={sections} />,
    ...kids.slice(at),
  ];
}

function Contents({ sections }: { sections: { id: string; title: string }[] }) {
  return (
    <nav aria-labelledby="on-this-page" className="border-l-3 border-l-signal-cyan/60 pl-5">
      <h2 id="on-this-page" className="font-mono text-xs text-signal-cyan">
        On this page
      </h2>
      <ol className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-base sm:grid-cols-2">
        {sections.map((s) => (
          <li key={s.id}>
            <a
              href={`#${s.id}`}
              className="inline-block py-1 text-ink underline decoration-white/20 underline-offset-2 hover:text-phos"
            >
              {s.title}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

function PagerLink({
  href,
  dir,
  title,
  alignEnd = false,
}: {
  href: string;
  dir: string;
  title: string;
  alignEnd?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`panel block p-4 hover:border-ink-min transition-colors ${alignEnd ? "sm:text-right sm:col-start-2" : ""}`}
    >
      <span className="block font-mono text-xs text-ink-min">{dir}</span>
      <span className="mt-1 block font-display text-lg font-semibold text-ink-hi">{title}</span>
    </Link>
  );
}

/** Every chapter, always visible, so no page is a dead end. */
function ChapterNav({ current }: { current: string }) {
  const items = [{ href: ABOUT_OVERVIEW_HREF, title: "Overview" }, ...ABOUT_CHAPTERS];
  return (
    <nav aria-label="About this site">
      <ul className="flex flex-wrap gap-x-1 gap-y-2 font-mono text-xs">
        {items.map((c) => {
          const active = c.href === current;
          return (
            <li key={c.href}>
              <Link
                href={c.href}
                aria-current={active ? "page" : undefined}
                className={`inline-block border px-2.5 py-1.5 transition-colors ${
                  active
                    ? "border-signal-cyan text-signal-cyan"
                    : "border-white/[0.09] text-ink-lo hover:border-ink-min hover:text-ink-hi"
                }`}
              >
                {c.title}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** "The short version": what a reader should leave with. */
export function Summary({ children }: { children: ReactNode }) {
  return (
    <section
      aria-labelledby="short-version"
      className="panel border-l-3 border-l-signal-amber p-5 sm:p-6"
    >
      <h2 id="short-version" className="font-mono text-xs text-signal-amber">
        The short version
      </h2>
      <ul className="mt-3 space-y-2.5 text-base leading-relaxed text-ink">{children}</ul>
    </section>
  );
}

export function Point({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="mt-[0.7em] h-1.5 w-1.5 shrink-0 bg-signal-amber" />
      <span>{children}</span>
    </li>
  );
}

export function Section({
  id,
  title,
  kicker,
  children,
}: {
  id: string;
  title: string;
  /** Small label above the heading, e.g. a weight or a category. */
  kicker?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-h`}>
      {kicker && <p className="font-mono text-xs text-signal-cyan">{kicker}</p>}
      <h2
        id={`${id}-h`}
        {...onThisPage(title)}
        className="mt-1 font-display text-2xl font-semibold leading-tight tracking-[-0.01em] text-ink-hi"
      >
        {title}
      </h2>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

export function Sub({ title, id, children }: { title: string; id?: string; children: ReactNode }) {
  return (
    <div id={id} className="space-y-3 pt-2">
      <h3 className="font-display text-lg font-semibold text-ink-hi">{title}</h3>
      {children}
    </div>
  );
}

export function P({ children }: { children: ReactNode }) {
  return <p className="text-base leading-relaxed text-ink">{children}</p>;
}

/** A plain bulleted list inside prose. */
export function List({ children }: { children: ReactNode }) {
  return <ul className="space-y-2 text-base leading-relaxed text-ink">{children}</ul>;
}

export function Item({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span aria-hidden="true" className="text-ink-min">
        –
      </span>
      {/* min-w-0: a flex item's minimum width is its content's, so a wide
          child (the MCP config <pre> on /developers, overflow-x-auto) made
          the item, and the page, wider than a phone instead of scrolling
          inside its own box. */}
      <span className="min-w-0">
        {label && <strong className="font-semibold text-ink-hi">{label}. </strong>}
        {children}
      </span>
    </li>
  );
}

/** Numbered steps: a method that happens in order. */
export function Steps({ children }: { children: ReactNode }) {
  return <ol className="space-y-3 text-base leading-relaxed text-ink">{children}</ol>;
}

export function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span
        aria-hidden="true"
        className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center border border-signal-cyan/60 font-mono text-xs text-signal-cyan"
      >
        {n}
      </span>
      <span>
        <strong className="font-semibold text-ink-hi">{title}.</strong> {children}
      </span>
    </li>
  );
}

/**
 * The depth under a paragraph: evidence, history, edge cases. Closed by
 * default; its summary line says what is inside, so a reader can tell
 * whether it is worth opening.
 */
/** `context` is read, not shown: what the disclosure belongs to, when the
 * same label repeats down a page (every endpoint's "Response fields" on
 * /developers), so a reader tabbing through hears which one each is. */
export function More({
  label,
  context,
  children,
}: {
  label: string;
  context?: string;
  children: ReactNode;
}) {
  return (
    <details className="group border border-white/[0.09] bg-white/[0.015]">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 text-sm text-ink-lo hover:text-ink-hi [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className="font-mono text-signal-cyan">
          <span className="group-open:hidden">+</span>
          <span className="hidden group-open:inline">−</span>
        </span>
        {/* min-w-0: a flex item is otherwise as wide as its longest word, so
            a field name ("constituentAlignment") at 200% text ran past a
            phone instead of breaking (body's overflow-wrap). */}
        <span className="min-w-0 font-semibold">
          {label}
          {context && <span className="sr-only"> {context}</span>}
        </span>
      </summary>
      <div className="space-y-3 px-4 pb-4 text-[0.95rem] leading-relaxed text-ink-lo [&_p]:text-[0.95rem] [&_p]:text-ink-lo">
        {children}
      </div>
    </details>
  );
}

/** A label/value register, for sources and specifications. */
export function Facts({ children }: { children: ReactNode }) {
  return <dl className="divide-y divide-white/[0.07] border-y border-white/[0.07]">{children}</dl>;
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[13rem_1fr] sm:gap-4">
      <dt className="font-mono text-xs text-signal-amber sm:pt-0.5">{label}</dt>
      <dd className="text-sm leading-relaxed text-ink-lo">{children}</dd>
    </div>
  );
}

/** Inline citation: prints who, links to the full entry. It wraps at its
 * own spaces like the prose around it: it was one unbreakable span, which at
 * 200% text ("(Ansolabehere & Kuriwaki 2022)") was wider than a phone and
 * pushed the page sideways. The parentheses touch the name, so they never
 * strand on a line of their own. */
export function Cite({ id }: { id: RefId }) {
  return (
    <>
      {" "}
      <span className="text-sm text-ink-lo">
        (
        <a
          href={`/about/references#${id}`}
          className="underline decoration-ink-min/50 underline-offset-2 hover:text-phos"
        >
          {REFERENCES[id].short}
        </a>
        )
      </span>
    </>
  );
}

/** Inline link inside prose. */
export function A({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="text-signal-cyan underline underline-offset-2 hover:text-phos">
      {children}
    </Link>
  );
}
