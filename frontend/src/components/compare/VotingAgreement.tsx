"use client";

import Link from "next/link";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchCompareAgreement } from "@/lib/api";
import { asciiScoreBar } from "@/lib/representation";
import { rollCallHref, shortDate } from "@/lib/congress";
import type { AgreementSplit } from "@/types/compare";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";

/**
 * The comparison's voting record: how often the two voted the same way
 * this Congress, and the latest votes they split on, each linked to its
 * roll call. Same chamber only; across chambers there is no shared vote.
 *
 * Every figure and the sentence are the backend's (GET
 * /api/compare/agreement); the bar is the page's own block-character
 * score bar, drawn from the backend's percentage.
 */
export default function VotingAgreement({
  chamber,
  left,
  right,
}: {
  chamber: "senate" | "house";
  left: { id: string; name: string };
  right: { id: string; name: string };
}) {
  const { data, error } = useAsyncData(`agreement:${chamber}:${left.id}:${right.id}`, () =>
    fetchCompareAgreement(chamber, left.id, right.id)
  );
  if (error || !data) return null;
  const query = new URLSearchParams({
    leftId: left.id,
    leftChamber: chamber,
    rightId: right.id,
    rightChamber: chamber,
  });
  return (
    <ShareSubjectProvider
      subject={{
        title: `${left.name} and ${right.name}`,
        subtitle: `How often they voted alike · ${chamber === "senate" ? "Senate" : "House"} roll calls this Congress`,
        url: absoluteUrl(`/compare?${query}`),
        source: `Roll calls as ${chamber === "senate" ? "Senate.gov" : "the House Clerk"} records them`,
        asOf: data.asOf ?? null,
      }}
    >
      <section
        aria-labelledby="voting-agreement"
        className="border-t border-white/[0.07] bg-surface-base px-3 py-3 font-mono text-xs"
        {...{ [SHARE_SECTION_ATTR]: "voted-alike" }}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h3 id="voting-agreement" className="tracking-[0.16em] text-ink-min">
            VOTED ALIKE · THIS CONGRESS
          </h3>
          <ShareSectionButton label="Voted alike" anchor={null} />
        </div>
        <p className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          {data.agreementPct != null && (
            <>
              <span className="font-display text-3xl font-extrabold leading-none text-ink-hi tabular-nums">
                {data.agreementPct}%
              </span>
              <span aria-hidden="true" className="tracking-tight text-signal-cyan">
                {asciiScoreBar(data.agreementPct)}
              </span>
            </>
          )}
        </p>
        <p className="mt-2 font-display text-sm leading-snug text-ink">{data.sentence}</p>
        {data.recentSplits.length > 0 && (
          <>
            <h4 className="mt-3 tracking-[0.12em] text-ink-min">LATEST VOTES THEY SPLIT ON</h4>
            <ul className="mt-1">
              {data.recentSplits.map((v) => (
                <Split
                  key={`${v.session}-${v.number}`}
                  vote={v}
                  left={left.name}
                  right={right.name}
                />
              ))}
            </ul>
          </>
        )}
      </section>
    </ShareSubjectProvider>
  );
}

function Split({ vote: v, left, right }: { vote: AgreementSplit; left: string; right: string }) {
  const what = v.billLabel ? `${v.billLabel}: ${v.title || v.question}` : v.title || v.question;
  return (
    <li className="border-b border-white/[0.07] py-2 last:border-b-0">
      <Link
        href={rollCallHref(v)}
        className="line-clamp-2 min-h-6 font-display text-sm leading-snug text-ink-hi hover:underline"
      >
        {what}
      </Link>
      <span className="mt-0.5 block text-ink-lo">
        {shortDate(v.date).toUpperCase()} · {v.result}
      </span>
      <span className="mt-0.5 block text-ink-lo">
        {left}: <span className="text-ink-hi">{v.left.toUpperCase()}</span> · {right}:{" "}
        <span className="text-ink-hi">{v.right.toUpperCase()}</span>
      </span>
    </li>
  );
}
