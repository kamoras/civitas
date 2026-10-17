import Link from "next/link";
import { connection } from "next/server";
import type { Notable, NotableEntry } from "@/types/notable";
import { rollCallHref, shortDate, weekHref } from "@/lib/congress";
import { fetchSectionFigures } from "@/components/sections/SectionCards";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/notable.py).
const REVALIDATE_S = 300;

/**
 * On the record: the closest vote and the lone party breaks of Congress's
 * latest week of record votes, as docket entries.
 *
 * Each entry is the backend's fixed sentence around the chamber's roll call
 * and ends in the commands that check it: the roll call itself, the member,
 * and the member beside their state's other senator. The point is a tap
 * into evidence, not a headline; nothing here is a model's wording.
 *
 * Rendered on the server with the page. An empty week (a long recess, or a
 * backend that can't answer) renders nothing at all rather than filler.
 */
export default async function OnTheRecord() {
  await connection();
  const [data, figures] = await Promise.all([fetchNotable(), fetchSectionFigures()]);
  if (!data?.weekStart || data.entries.length === 0) return null;
  // On a day Congress is out (the backend's `recess`, the one /congress
  // leads with), the heading says these are the last week of business's
  // votes, not this week's: "On the record" over a weeks-old list read as
  // current.
  return (
    <OnTheRecordList
      data={data as Notable & { weekStart: string }}
      recess={Boolean(figures?.congress.recess)}
    />
  );
}

async function fetchNotable(): Promise<Notable | null> {
  try {
    const res = await fetch(`${BACKEND}/api/notable`, { next: { revalidate: REVALIDATE_S } });
    return res.ok ? ((await res.json()) as Notable) : null;
  } catch {
    return null;
  }
}

const KIND: Record<NotableEntry["kind"], { tag: string; tone: string }> = {
  closest: { tag: "CLOSEST VOTE", tone: "text-signal-amber" },
  loneBreak: { tag: "LONE BREAK", tone: "text-signal-cyan" },
};

// The site's bracketed command style (the header search's "[ SEARCH ]"), sized to a 32px
// touch target.
const COMMAND =
  "inline-flex min-h-[32px] items-center font-mono text-xs tracking-[0.12em] text-phos-mid hover:text-phos";

function chamberWord(c: "senate" | "house") {
  return c === "senate" ? "SENATE" : "HOUSE";
}

function Entry({ entry }: { entry: NotableEntry }) {
  const v = entry.vote;
  const kind = KIND[entry.kind];
  const what = [v.billLabel, v.question].filter(Boolean).join(" · ");
  return (
    <li className="grid grid-cols-1 gap-x-8 gap-y-1 border-b border-white/[0.07] py-3 md:grid-cols-[11rem_minmax(0,1fr)_auto]">
      {/* One line on a phone, three stacked lines beside the entry on a
          wide screen: the docket reference reads as a margin note. */}
      <p className="font-mono text-xs leading-relaxed tracking-[0.08em]">
        <span className={`md:block ${kind.tone}`}>{kind.tag}</span>
        <span className="text-ink-min">
          <span className="md:hidden"> · </span>
          <span className="md:block">{shortDate(v.date).toUpperCase()}</span>
          <span className="md:hidden"> · </span>
          <span className="md:block">
            {chamberWord(v.chamber)} ROLL CALL {v.number}
          </span>
        </span>
      </p>
      <div className="min-w-0">
        <p className="font-display text-base font-semibold leading-snug text-ink-hi">
          {entry.sentence}
        </p>
        <p className="mt-0.5 line-clamp-2 font-display text-sm leading-snug text-ink-lo">
          {what}
          {v.title && v.title !== v.question ? ` · ${v.title}` : ""}
        </p>
      </div>
      <p className="flex flex-wrap gap-x-4 md:flex-col md:items-end md:gap-0">
        {/* Named in full for a screen reader, which otherwise hears "left
            bracket roll call right bracket" once per entry with nothing to
            tell them apart. The visible words stay in the name (WCAG 2.5.3). */}
        <Link
          href={rollCallHref(v)}
          aria-label={`${v.chamber === "senate" ? "Senate" : "House"} roll call ${v.number}`}
          className={COMMAND}
        >
          [ ROLL CALL ]
        </Link>
        {entry.kind === "loneBreak" && entry.memberId && (
          <Link
            href={`/politicians/${entry.memberId}`}
            aria-label={entry.memberName}
            className={COMMAND}
          >
            [ {entry.memberName.toUpperCase()} ]
          </Link>
        )}
        {entry.kind === "loneBreak" && entry.memberId && entry.compareWithId && (
          <Link
            href={`/compare?leftId=${entry.memberId}&leftChamber=senate&rightId=${entry.compareWithId}&rightChamber=senate`}
            aria-label={`Compare ${entry.memberName} vs. ${entry.compareWithName ?? "the state's other senator"}`}
            className={COMMAND}
          >
            [ VS. {(entry.compareWithName ?? "").toUpperCase()} ]
          </Link>
        )}
      </p>
    </li>
  );
}

export function OnTheRecordList({
  data,
  recess = false,
}: {
  data: Notable & { weekStart: string };
  recess?: boolean;
}) {
  return (
    <section aria-labelledby="on-the-record" className="mx-auto mt-8 max-w-7xl px-4 sm:px-6">
      {/* The week's link sits beside the heading, not in it: inside, it made
          the heading's name "On the record Week of ...: every roll call". */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        <h2 id="on-the-record">
          {recess ? `Last week of business: ${shortDate(data.weekStart)}` : "On the record"}
        </h2>
        <Link
          href={weekHref(data.weekStart)}
          className="normal-case tracking-[0.06em] underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          {recess ? "Every roll call" : `Week of ${shortDate(data.weekStart)}: every roll call`}{" "}
          &rarr;
        </Link>
      </div>
      <ul>
        {data.entries.map((e) => (
          <Entry
            key={`${e.kind}-${e.vote.chamber}-${e.vote.number}-${e.kind === "loneBreak" ? e.party : ""}`}
            entry={e}
          />
        ))}
      </ul>
    </section>
  );
}
